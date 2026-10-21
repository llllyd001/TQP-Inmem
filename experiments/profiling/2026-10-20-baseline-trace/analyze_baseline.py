"""Analyze baseline traces without torch or third-party dependencies.
Run from experiments/profiling/: python3 -B 2026-10-20-baseline-trace/analyze_baseline.py
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent))
import svgplot  # noqa: E402

GB = 10**9
SLAB = ('highwater_bytes', 'live_at_h0', 'internal_at_h0', 'dead_at_h0', 'live_peak_bytes',
        'highwater_free_bytes', 'live_at_h1', 'internal_at_h1', 'holes_at_h1')
TOTAL = ('slab_side_peak', 'torch_side_peak', 'static_need_bytes', 'concurrent_need_bytes',
         'concurrent_need_free_bytes')
P1 = ('cached_unused_peak_bytes',)


class Pool:
    """Pure Python replay of IO/pinned_mem.py:20-23, 29-47, 71-96."""

    def __init__(self, capacity, block):
        self.block = block
        self.free_list = [(0, capacity // block)]
        self.free_times = 0

    def alloc(self, size):
        n = max((size + self.block - 1) // self.block, 1)
        for i, (start, length) in enumerate(self.free_list):
            if length >= n:
                if length == n:
                    self.free_list.pop(i)
                else:
                    self.free_list[i] = (start + n, length - n)
                return start, n
        raise MemoryError(f'Replay exceeds pool capacity or has no contiguous span for {n} blocks')

    def free(self, start, n):
        self.free_list.append((start, n))
        self.free_times += 1
        if self.free_times > 5:
            self.merge()
            self.free_times = 0

    def merge(self):
        merged = []
        for start, length in sorted(self.free_list):
            if merged and sum(merged[-1]) == start:
                prev, count = merged[-1]
                merged[-1] = (prev, count + length)
            else:
                merged.append((start, length))
        self.free_list = merged


def allocation_lifetimes(ops, allocs, reads):
    order = {(op['plan'], op['op']): j for j, op in enumerate(ops)}
    by_op = defaultdict(list)
    for a in allocs:
        j = order[a['plan'], a['op']]
        by_op[j].append((int(a['start_block']), int(a['blocks']), int(a['requested_bytes'])))
    allocations = [(j, s, n, r) for j in range(len(ops)) for s, n, r in sorted(by_op[j])]
    last_read = {}
    for read in reads:
        if read['in_slab'] == 'True':
            j = order[read['plan'], read['op']]
            for b in range(int(read['first_block']), int(read['last_block']) + 1):
                last_read[b] = max(last_read.get(b, -1), j)
    last_use = [max((last_read.get(b, i) for b in range(s, s + n)), default=i)
                for i, s, n, r in allocations]
    return allocations, last_use


def analyze(query, ops, allocs, reads):
    block = int(float(query['block_mb']) * 10**6)
    allocations, last_use = allocation_lifetimes(ops, allocs, reads)
    k = allocations[-1][0]
    h0 = sum(n * block for i, s, n, r in allocations)
    if h0 != int(query['pool_peak_bytes']):
        raise ValueError(f"SF{query['sf']} Q{query['qid']}: allocation sum {h0} != pool_peak_bytes")
    d = dict(highwater_bytes=h0,
             live_at_h0=sum(a[3] for a, last in zip(allocations, last_use) if last >= k),
             internal_at_h0=sum(n * block - r for i, s, n, r in allocations),
             dead_at_h0=sum(a[3] for a, last in zip(allocations, last_use) if last < k))

    # Free in original allocation order; capture the first allocation reaching the final H1.
    pool = Pool(int(float(query['gpu_pool_gb']) * GB), block)
    starts, ends = defaultdict(list), defaultdict(list)
    for a, (i, s, n, r) in enumerate(allocations):
        starts[i].append(a)
        ends[last_use[a]].append(a)
    active = {}
    h1 = live_peak = concurrent = concurrent_free = 0
    for j, op in enumerate(ops):
        for a in starts[j]:
            r = allocations[a][3]
            ptr, n = pool.alloc(r)
            active[a] = (ptr, n, r)
            if (ptr + n) * block > h1:
                h1 = (ptr + n) * block
                live = sum(v[2] for v in active.values())
                occupied = sum(v[1] for v in active.values()) * block
                d.update(live_at_h1=live, internal_at_h1=occupied - live, holes_at_h1=h1 - occupied)
        live_peak = max(live_peak, sum(v[2] for v in active.values()))
        torch = int(op['allocated_peak']) - int(query['allocated_start_bytes'])
        concurrent = max(concurrent, int(op['slab_used_blocks_end']) * block + torch)
        # Current occupied address extent before frees, not the historical high-water mark.
        extent = max((ptr + n for ptr, n, r in active.values()), default=0) * block
        concurrent_free = max(concurrent_free, extent + torch)
        for a in ends[j]:
            ptr, n, r = active.pop(a)
            pool.free(ptr, n)
    d.update(highwater_free_bytes=h1, live_peak_bytes=live_peak,
             slab_side_peak=h0, torch_side_peak=int(query['transient_peak_bytes']),
             static_need_bytes=h0 + int(query['transient_peak_bytes']),
             concurrent_need_bytes=concurrent, concurrent_need_free_bytes=concurrent_free,
             cached_unused_peak_bytes=max(int(op['reserved_peak']) - int(op['allocated_peak']) for op in ops))
    return d


def load_traces(directory):
    # Preserve trace row order across plans and all original query columns.
    traces = {}
    for name in ('queries', 'ops', 'allocs', 'reads'):
        with (directory / f'{name}.csv').open(newline='') as f:
            traces[name] = list(csv.DictReader(f))
    grouped = {}
    for name in ('ops', 'allocs', 'reads'):
        grouped[name] = defaultdict(list)
        for row in traces[name]:
            grouped[name][row['sf'], row['qid']].append(row)
    return traces, grouped


def main():
    traces, grouped = load_traces(here / "trace")
    rows = []
    for query in traces['queries']:
        key = query['sf'], query['qid']
        derived = analyze(query, *(grouped[name][key] for name in ('ops', 'allocs', 'reads')))
        row = {}
        for key, value in query.items():
            row[key] = value
            columns = {'pool_peak_bytes': SLAB, 'transient_peak_bytes': TOTAL, 'reserved_peak_bytes': P1}.get(key, ())
            row.update((column, derived[column]) for column in columns)
        rows.append(row)
    with (here / 'baseline_derived.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    fig = here / 'figures'
    fig.mkdir(exist_ok=True)
    lookup = {(int(r['sf']), int(r['qid'])): r for r in rows}
    sfs = sorted({sf for sf, q in lookup})
    qids = sorted({q for sf, q in lookup})
    panels = []
    for q in qids:
        groups = []
        for sf in sfs:
            r = lookup.get((sf, q))
            groups.append(None if r is None else [
                [r['live_at_h0'] / GB, r['internal_at_h0'] / GB, r['dead_at_h0'] / GB, 0],
                [r['live_at_h1'] / GB, r['internal_at_h1'] / GB, 0, r['holes_at_h1'] / GB]])
        panels.append((f'Q{q}', groups))
    svgplot.stacked_grid(fig / '1_slab_breakdown.svg', sfs, panels,
                         ['Live data', 'Internal (S2)', 'Dead data (S3)', 'Holes (S4)'])
    svgplot.bar_chart(
        fig / '2_static_vs_concurrent.svg', [f'Q{q}' if q != 21 else 'Q21*' for q in qids],
        [(label, [lookup[10, q][key] / GB if (10, q) in lookup else 0 for q in qids])
         for label, key in [('Static', 'static_need_bytes'), ('Concurrent', 'concurrent_need_bytes'),
                            ('Concurrent + free', 'concurrent_need_free_bytes')]],
        'Static partition vs. concurrent memory demand at SF10',
        'Operator-level upper bounds; *Q21: OOM (no trace); reference: 16 GB GPU = 16.61 GB decimal',
        'Memory (GB)', 'TPC-H query', ref=(16.61, 'GPU capacity: 16.61 GB (assumed)'), grouped=True)
    for svg in fig.glob('*.svg'):
        svgplot.render_png(svg)

    print(f'Validated {len(rows)} queries; wrote baseline_derived.csv and two SVG figures.')
    for sf in sfs:
        print(f'\nSF{sf}: median / max (GB)')
        for key in SLAB + TOTAL + P1:
            values = [r[key] / GB for r in rows if int(r['sf']) == sf]
            print(f'  {key:30s} {statistics.median(values):8.3f} / {max(values):8.3f}')


if __name__ == "__main__":
    main()
