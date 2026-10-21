import csv
import os
import time

import torch

from utility.lifetime import slab_blocks


_mem_profiler = None
_COLUMNS = (
    'sf', 'qid', 'gpu_pool_gb', 'block_mb', 'status', 'pool_peak_bytes', 'pool_requested_bytes',
    'pool_allocs', 'transient_peak_bytes', 'reserved_peak_bytes', 'inactive_split_peak_bytes',
    'alloc_retries', 'device_used_start_bytes', 'device_used_end_bytes', 'allocated_start_bytes',
    'reserved_start_bytes', 'allocated_end_bytes', 'reserved_end_bytes', 'time_s', 'error',
)


def mem_profiler():
    if _mem_profiler is None:
        set_mem_profiler(None, None, None)
    return _mem_profiler


def set_mem_profiler(path, pool, scale_factor, trace_dir=None):
    global _mem_profiler
    _mem_profiler = MemProfiler(path, pool, scale_factor, trace_dir)


class MemProfiler:
    def __init__(self, path, pool, scale_factor, trace_dir=None):
        self.trace_dir = trace_dir
        self.path = path
        self.pool = pool
        self.scale_factor = scale_factor
        self.qid = None

    def query_start(self, qid):
        if self.path is None:
            return
        torch.cuda.synchronize()
        free, total = torch.cuda.mem_get_info()
        self.device_used_start = total - free
        self.allocated_start = torch.cuda.memory_allocated()
        self.reserved_start = torch.cuda.memory_reserved()
        self.alloc_retries_start = torch.cuda.memory_stats()['num_alloc_retries']
        self.pool_allocs_start = self.pool.stats()['num_allocs']
        self.start_time = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        self.pool.reset_peak()
        self.qid = qid
        if self.trace_dir is not None:
            self.rows = {name: [] for name in _TRACE_COLUMNS}
            self.plan = None
            self.active_op = None
            self.peaks = [0, 0, 0]

    def query_end(self):
        self._write('ok', '')

    def query_failed(self, exc):
        status = 'oom_cuda' if isinstance(exc, torch.OutOfMemoryError) else 'oom_pool'
        self._write(status, str(exc).replace('\r', ' ').replace('\n', ' '))

    def _write(self, status, error):
        if self.path is None or self.qid is None:
            return
        if self.trace_dir is not None and self.active_op is not None:
            self.op_end()
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - self.start_time
        free, total = torch.cuda.mem_get_info()
        stats = torch.cuda.memory_stats()
        pool_stats = self.pool.stats()
        peaks = [torch.cuda.max_memory_allocated(), torch.cuda.max_memory_reserved(),
                 stats['inactive_split_bytes.all.peak']]
        if self.trace_dir is not None:
            peaks = [max(a, b) for a, b in zip(self.peaks, peaks)]
            if status != 'ok':
                largest = max((block['size'] for segment in torch.cuda.memory_snapshot()
                               if segment['device'] == torch.cuda.current_device()
                               for block in segment['blocks'] if block['state'] == 'inactive'), default=0)
                self.rows['oom'].append((self.scale_factor, self.qid, self.plan, free,
                                         torch.cuda.memory_reserved() - torch.cuda.memory_allocated(), largest, error))
        row = (
            self.scale_factor, self.qid, pool_stats['capacity_bytes'] / 1e9, self.pool.block_size_bytes / 1e6,
            status, pool_stats['peak_bytes'], pool_stats['requested_bytes'],
            pool_stats['num_allocs'] - self.pool_allocs_start,
            peaks[0] - self.allocated_start, peaks[1], peaks[2], stats['num_alloc_retries'] - self.alloc_retries_start,
            self.device_used_start, total - free, self.allocated_start, self.reserved_start,
            torch.cuda.memory_allocated(), torch.cuda.memory_reserved(), elapsed, error,
        )
        file_exists = os.path.exists(self.path)
        with open(self.path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(_COLUMNS)
            writer.writerow(row)
        if self.trace_dir is not None:
            os.makedirs(self.trace_dir, exist_ok=True)
            for name, columns in _TRACE_COLUMNS.items():
                path = os.path.join(self.trace_dir, name + '.csv')
                exists = os.path.exists(path)
                with open(path, 'a', newline='', encoding='utf-8') as stream:
                    writer = csv.writer(stream)
                    if not exists:
                        writer.writerow(('sf', 'qid', 'plan', *columns))
                    writer.writerows(self.rows[name])
            self.rows.clear()
        self.qid = None

    def op_start(self, plan, op, name):
        if self.trace_dir is None:
            return
        stats = torch.cuda.memory_stats()
        # Preserve peaks between operators before resetting the allocator counters.
        self.peaks = [max(a, b) for a, b in zip(self.peaks, (
            torch.cuda.max_memory_allocated(), torch.cuda.max_memory_reserved(),
            stats['inactive_split_bytes.all.peak']))]
        self.plan = plan
        self.active_op = op
        self.op_name = name
        self.seen = set()
        self.allocs_start = set(self.pool._allocs)
        self.used_start = self.pool._used_blocks
        self.op_allocated_start = torch.cuda.memory_allocated()
        self.op_reserved_start = torch.cuda.memory_reserved()
        self.op_retries_start = stats['num_alloc_retries']
        torch.cuda.reset_peak_memory_stats()
        self.op_time = time.perf_counter()

    def op_end(self):
        if self.trace_dir is None:
            return
        elapsed = time.perf_counter() - self.op_time
        stats = torch.cuda.memory_stats()
        peaks = (torch.cuda.max_memory_allocated(), torch.cuda.max_memory_reserved(),
                 stats['inactive_split_bytes.all.peak'])
        self.peaks = [max(a, b) for a, b in zip(self.peaks, peaks)]
        prefix = (self.scale_factor, self.qid, self.plan, self.active_op)
        self.rows['ops'].append((*prefix, self.op_name, self.used_start, self.pool._used_blocks,
                                 max((s + n for s, n in self.pool._allocs.items()), default=0),
                                 self.op_allocated_start, torch.cuda.memory_allocated(), peaks[0],
                                 self.op_reserved_start, torch.cuda.memory_reserved(), peaks[1], peaks[2],
                                 stats['num_alloc_retries'] - self.op_retries_start, elapsed))
        for start in sorted(self.pool._allocs.keys() - self.allocs_start):
            self.rows['allocs'].append((*prefix, start, self.pool._allocs[start], self.pool._requested_bytes[start]))
        self.active_op = None

    def record_read(self, col, tensor):
        if self.active_op is None or col in self.seen or tensor.numel() == 0:
            return
        self.seen.add(col)
        blocks = slab_blocks(tensor, self.pool)
        in_slab = blocks is not None
        first, last = blocks if in_slab else ('', '')
        self.rows['reads'].append((self.scale_factor, self.qid, self.plan, self.active_op, col,
                                   in_slab, first, last, tensor.numel() * tensor.element_size()))

    def record_frees(self, plan, op, starts):
        if self.trace_dir is not None:
            self.rows['frees'].extend((self.scale_factor, self.qid, plan, op, start) for start in starts)


_TRACE_COLUMNS = {
    'ops': ('op', 'op_name', 'slab_used_blocks_start', 'slab_used_blocks_end', 'slab_extent_blocks_end',
            'allocated_start', 'allocated_end', 'allocated_peak', 'reserved_start', 'reserved_end',
            'reserved_peak', 'inactive_split_peak', 'alloc_retries', 'time_s'),
    'allocs': ('op', 'start_block', 'blocks', 'requested_bytes'),
    'frees': ('op', 'start_block'),
    'reads': ('op', 'col', 'in_slab', 'first_block', 'last_block', 'bytes'),
    'oom': ('device_free_bytes', 'cached_free_bytes', 'largest_cached_free_bytes', 'error'),
}


class TracedTensorGroup(dict):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        mem_profiler().record_read(key, value.tensor)
        return value
