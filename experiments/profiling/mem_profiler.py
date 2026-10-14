import csv
import os
import time

import torch


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


def set_mem_profiler(path, pool, scale_factor):
    global _mem_profiler
    _mem_profiler = MemProfiler(path, pool, scale_factor)


class MemProfiler:
    def __init__(self, path, pool, scale_factor):
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

    def query_end(self):
        self._write('ok', '')

    def query_failed(self, exc):
        status = 'oom_cuda' if isinstance(exc, torch.OutOfMemoryError) else 'oom_pool'
        self._write(status, str(exc).replace('\r', ' ').replace('\n', ' '))

    def _write(self, status, error):
        if self.path is None or self.qid is None:
            return
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - self.start_time
        free, total = torch.cuda.mem_get_info()
        stats = torch.cuda.memory_stats()
        pool_stats = self.pool.stats()
        row = (
            self.scale_factor, self.qid, pool_stats['capacity_bytes'] / 1e9, self.pool.block_size_bytes / 1e6,
            status, pool_stats['peak_bytes'], pool_stats['requested_bytes'],
            pool_stats['num_allocs'] - self.pool_allocs_start,
            torch.cuda.max_memory_allocated() - self.allocated_start, torch.cuda.max_memory_reserved(),
            stats['inactive_split_bytes.all.peak'], stats['num_alloc_retries'] - self.alloc_retries_start,
            self.device_used_start, total - free, self.allocated_start, self.reserved_start,
            torch.cuda.memory_allocated(), torch.cuda.memory_reserved(), elapsed, error,
        )
        file_exists = os.path.exists(self.path)
        with open(self.path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(_COLUMNS)
            writer.writerow(row)
        self.qid = None
