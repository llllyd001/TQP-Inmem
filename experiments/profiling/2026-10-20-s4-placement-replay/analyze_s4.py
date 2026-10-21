"""Replay baseline slab lifetimes with only the Python standard library."""
import csv
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / '2026-10-20-baseline-trace'
sys.path.insert(0, str(BASELINE))
from analyze_baseline import GB, Pool, allocation_lifetimes, analyze, load_traces  # noqa: E402
import svgplot  # noqa: E402

BLOCK = 40_000_000
PLACEMENTS = ('first', 'best', 'lifetime-0', 'lifetime-1', 'lifetime-2')
RULES = [(p, m) for p in PLACEMENTS for m in ('lazy', 'every')]
NAMES = [f'{p}_{m}' for p, m in RULES]


class ReplayPool(Pool):
    def __init__(self, blocks, placement, merging):
        super().__init__(blocks * BLOCK, BLOCK)
        self.placement = placement
        self.merging = merging
        self.merge_time_us = 0.0

    def alloc(self, size, lifetime):
        high = self.placement.startswith('lifetime-') and lifetime <= int(self.placement[-1])
        if self.placement == 'first' or (self.placement.startswith('lifetime-') and not high):
            return super().alloc(size)
        n = max((size + BLOCK - 1) // BLOCK, 1)
        candidates = [i for i, (_, length) in enumerate(self.free_list) if length >= n]
        if not candidates:
            raise MemoryError
        if high:
            i = max(candidates, key=lambda i: self.free_list[i][0])
        else:
            i = min(candidates, key=lambda i: (self.free_list[i][1], self.free_list[i][0]))
        start, length = self.free_list[i]
        ptr = start + length - n if high else start
        if length == n:
            self.free_list.pop(i)
        else:
            self.free_list[i] = (start if high else start + n, length - n)
        return ptr, n

    def free(self, start, n):
        if self.merging == 'every':
            self.free_times = 5
        super().free(start, n)

    def merge(self):
        begin = time.perf_counter()
        super().merge()
        self.merge_time_us += (time.perf_counter() - begin) * 1e6


def events_for(ops, allocations, last_use):
    starts, ends = [[] for _ in ops], [[] for _ in ops]
    occupied = floor = 0
    for a, ((i, _, n, size), last) in enumerate(zip(allocations, last_use)):
        assert last >= i and n == max((size + BLOCK - 1) // BLOCK, 1)
        starts[i].append((a, size, last - i))
        ends[last].append(a)
    for new, dead in zip(starts, ends):
        occupied += sum(allocations[a][2] for a, _, _ in new)
        floor = max(floor, occupied)
        occupied -= sum(allocations[a][2] for a in dead)
    assert occupied == 0
    return list(zip(starts, ends)), floor


def replay(events, blocks, placement, merging):
    pool = ReplayPool(blocks, placement, merging)
    active = {}
    for new, dead in events:
        for a, size, lifetime in new:
            try:
                active[a] = pool.alloc(size, lifetime)
            except MemoryError:
                return False, pool.merge_time_us
        for a in dead:
            pool.free(*active.pop(a))
    assert not active
    return True, pool.merge_time_us


def minimum_pool(events, floor, upper, placement, merging):
    def succeeds(blocks):
        return replay(events, blocks, placement, merging)[0]

    if not succeeds(upper):
        return None, None, False
    lo, hi = floor, upper
    while lo < hi:
        mid = (lo + hi) // 2
        if succeeds(mid):
            hi = mid
        else:
            lo = mid + 1
    # Placement need not be monotone in capacity; certify the global minimum too.
    candidate = lo
    for blocks in range(floor, candidate):
        if succeeds(blocks):
            lo = blocks
            break
    success, merge_us = replay(events, lo, placement, merging)
    assert success and not succeeds(lo - 1)
    return lo * BLOCK, merge_us, lo != candidate


def summaries(rows):
    lines = ['SF  Rule                 Sum_GB  Median_GB  Max_merge_us  Success']
    for sf in sorted({r['sf'] for r in rows}):
        group = [r for r in rows if r['sf'] == sf]
        for name in ['H0', 'floor'] + NAMES:
            column = {'H0': 'highwater_bytes', 'floor': 'floor_bytes'}.get(name, f'{name}_pool_bytes')
            values = [r[column] / GB for r in group if r[column] is not None]
            total = f'{sum(values):.3f}' if len(values) == len(group) else 'NA'
            median = f'{statistics.median(values):.3f}' if values else 'NA'
            times = [r[f'{name}_merge_us'] for r in group
                     if name in NAMES and r[f'{name}_merge_us'] is not None]
            maximum = f'{max(times):.3f}' if times else '-'
            lines.append(f'{sf:<3} {name:<20} {total:>7} {median:>10} {maximum:>13} '
                         f'{len(values):>3}/{len(group)}')
    return '\n'.join(lines)


def figures(rows):
    fig = HERE / 'figures'
    fig.mkdir(exist_ok=True)
    # Wide charts leave room for all query bars and the eleven-series legend.
    svgplot.W, svgplot.H = 2200, 650
    svgplot.SERIES = svgplot.SERIES + ['#855c34', '#647887', '#202020']
    sf10 = sorted((r for r in rows if r['sf'] == 10), key=lambda r: r['qid'])
    thresholds = [t for t in range(3) if all(r[f'lifetime-{t}_every_pool_bytes'] is not None for r in sf10)]
    best_t = min(thresholds, key=lambda t: sum(r[f'lifetime-{t}_every_pool_bytes'] for r in sf10))
    chosen = ['first_lazy', 'first_every', 'best_lazy', 'best_every', f'lifetime-{best_t}_every']
    series = [('H0', [r['highwater_bytes'] / GB for r in sf10])]
    series += [(name, [r[f'{name}_pool_bytes'] / GB for r in sf10]) for name in chosen
               if all(r[f'{name}_pool_bytes'] is not None for r in sf10)]
    series += [('floor', [r['floor_bytes'] / GB for r in sf10])]
    svgplot.bar_chart(fig / '1_pool_need_sf10.svg', [f"Q{r['qid']}" for r in sf10], series,
                      'Minimum slab pool by query at SF10',
                      f'Last-read reclamation; lifetime-{best_t} selected by SF10 total; Q21 has no trace',
                      'Pool (GB)', 'TPC-H query', grouped=True)
    sfs = sorted({r['sf'] for r in rows})
    series = [(name, [sum(r[f'{name}_pool_bytes'] for r in rows if r['sf'] == sf) / GB for sf in sfs])
              for name in NAMES if all(r[f'{name}_pool_bytes'] is not None for r in rows)]
    series += [('floor', [sum(r['floor_bytes'] for r in rows if r['sf'] == sf) / GB for sf in sfs])]
    svgplot.bar_chart(fig / '2_summary.svg', [f'SF{sf}' for sf in sfs], series,
                      'Total minimum slab pool by placement and coalescing rule',
                      '65 traces total: SF5 = 22, SF7 = 22, SF10 = 21; totals within each SF',
                      'Total pool (GB)', 'Scale factor', grouped=True)
    return best_t


def write_readme(rows, summary, best_t, corrections):
    results = []
    successes = sum(r[f'{name}_pool_bytes'] is not None for r in rows for name in NAMES)
    for sf in sorted({r['sf'] for r in rows}):
        group = [r for r in rows if r['sf'] == sf]
        totals = {name: sum(r[f'{name}_pool_bytes'] for r in group) / GB for name in NAMES
                  if all(r[f'{name}_pool_bytes'] is not None for r in group)}
        best = min(totals.values())
        winners = ', '.join(f'`{name}`' for name in totals if totals[name] == best)
        h0 = sum(r['highwater_bytes'] for r in group) / GB
        floor = sum(r['floor_bytes'] for r in group) / GB
        results.append(f'- SF{sf}: $H_0$ total {h0:.2f} GB; `first_lazy` {totals["first_lazy"]:.2f} GB; '
                       f'minimum total {best:.2f} GB ({winners}); floor total {floor:.2f} GB.')
    (HERE / 'README.md').write_text(r'''# 2026-10-20 S4: Offline Replay of Slab Placement and Coalescing Timing

## Objective
Use raw baseline traces to compare minimum pool capacity under different placement rules and coalescing timings, ideally reclaiming all blocks after the operator that last reads an allocation finishes. Analyze only the slab, excluding PyTorch-side memory, without modifying the executor.

## Configuration and Execution
- Inputs are `trace/queries.csv`, `ops.csv`, `allocs.csv`, and `reads.csv` from the adjacent baseline experiment.
- 65 traces total: 22 each at SF5 and SF7, and 21 at SF10; SF10 Q21 has no trace and is neither zero-filled nor replaced with SF7.
- Block size: $B = 40{,}000{,}000$ bytes; decimal units: $1\,\mathrm{GB} = 10^9$ bytes
- Run `python3 -B 2026-10-20-s4-placement-replay/analyze_s4.py` from `experiments/profiling/`.
- Data analysis uses only the standard library, without torch / GPU; SVG generation uses `svgplot.py`, and PNG generation uses its `render_png` (local Chrome).
  PNGs for this run were generated with the existing local librsvg/Cairo installation through a temporary execution wrapper; no packages were installed and this dependency was not added to the script.

## Method
Reuse `load_traces`, `allocation_lifetimes`, and `Pool` from `analyze_baseline.py`.
Preserve the original operator row order; within each operator, sort allocations by original starting block, block count, and requested bytes. Consider only `in_slab` reads, using block-range overlap to determine lifetimes.
Complete all allocations for an operator before freeing expired allocations in original allocation order; unread allocations are freed after their allocating operator finishes. Lifetimes always come from original addresses; replayed addresses are not used to rematch reads.

| Placement rule | Definition |
|---|---|
| `first` | Select the first sufficiently large segment in free-list order and allocate from its low end; the list is not necessarily address-sorted |
| `best` | Select the shortest sufficiently large segment, breaking ties by lowest address, and allocate from its low end |
| `lifetime-t` | When $\operatorname{last\_use}(a)-i_a\le t$, scan addresses from high to low for the first sufficiently large segment and allocate from its high end; otherwise use `first` and allocate from the low end; $t\in\{0,1,2\}$ |
| `lazy` | Append each freed segment to the free list; on the sixth free, sort and coalesce adjacent segments, then reset the counter |
| `every` | Sort and coalesce adjacent segments on every free |

First confirm success at capacity $H_0$, then binary-search between the lower bound and $H_0$. Placement may change with capacity, so success cannot be assumed monotonic; additionally check every block capacity from the lower bound to the binary-search candidate to find the global minimum. Finally, replay at that capacity and one block less to confirm success and failure, respectively.
If replay still fails at $H_0$, leave capacity and time blank in the CSV; blanks are not counted as zero in summaries. Totals display NA, medians and maximum times include only successful cases, and incomplete series are omitted from figures.

## Metric Definitions
| Metric | Definition |
|---|---|
| Allocating operator index | $i_a$, zero-based in original operator order |
| `last_use` | $\operatorname{last\_use}(a)=\max\{j: \text{slab reads of operator }j\text{ overlap the block range of allocation }a\}$; use $i_a$ if no read overlaps |
| `highwater_bytes` | $H_0=B\sum_a n_a$, where $n_a$ is the allocation's block count; checked against baseline |
| `floor_blocks` | $\max_j\sum_{a:i_a\le j\le\operatorname{last\_use}(a)}n_a$, the maximum occupied block count after allocations and before frees within an operator |
| `floor_bytes` | $B\cdot\mathrm{floor\_blocks}$; includes internal block fragmentation and differs from the peak of actual requested bytes |
| `min_pool_blocks` | $\min\{C\in\mathbb{Z}: \text{replay succeeds with a capacity of }C\text{ blocks}\}$ |
| `<placement>_<coalescing>_pool_bytes` | $B\cdot\mathrm{min\_pool\_blocks}$ |
| `<placement>_<coalescing>_merge_us` | Sum of `time.perf_counter` durations for all sorting and coalescing during the confirmation replay at minimum capacity, in $\mu\mathrm{s}$; excludes search replays |

Coalescing time includes frees at query end but excludes appending freed segments, placement scans, and lifetime computation. It is a microsecond-scale estimate from a single local Python run, subject to timer and scheduling noise, and must not be interpreted as a GPU query speedup.

## Files and Figures
| File | Contents |
|---|---|
| `analyze_s4.py` | Replay, global minimum capacity validation, CSV output, plotting, and generation of these notes |
| `s4_replay.csv` | One row per SF / query; four reference columns and ten sets of capacity and coalescing time |
| `figures/1_pool_need_sf10.svg`, `.png` | Seven SF10 series; the lifetime threshold is selected by the SF10 total for every, breaking ties in favor of the smaller threshold |
| `figures/2_summary.svg`, `.png` | Grouped by SF, with totals for ten combinations and floor; totals do not represent capacity for running all queries concurrently |

![SF10 pool need](figures/1_pool_need_sf10.svg)

![Summary](figures/2_summary.svg)

## Main Results
''' + '\n'.join(results) + f'\n- The SF10 figure uses `lifetime-{best_t}_every`.'
        + f'\n- Global checks corrected {corrections} binary-search candidates; all {successes} successful cases were validated at the minimum capacity and one block less.'
        + '\n- All combinations are at least floor; `first_lazy` does not exceed baseline\'s `highwater_free_bytes`.'
        + '\n\n## Summary\nTotals and medians are in GB; maximum coalescing time is the largest single-query value within that SF.\n\n```text\n'
        + summary + '\n```\n', encoding='utf-8')


def main():
    traces, grouped = load_traces(BASELINE / 'trace')
    rows, corrections = [], 0
    for query in traces['queries']:
        key = query['sf'], query['qid']
        ops, allocs, reads = (grouped[name][key] for name in ('ops', 'allocs', 'reads'))
        assert int(float(query['block_mb']) * 1e6) == BLOCK
        allocations, last_use = allocation_lifetimes(ops, allocs, reads)
        events, floor = events_for(ops, allocations, last_use)
        baseline = analyze(query, ops, allocs, reads)
        upper = baseline['highwater_bytes'] // BLOCK
        row = dict(sf=int(query['sf']), qid=int(query['qid']), highwater_bytes=upper * BLOCK,
                   floor_bytes=floor * BLOCK)
        for placement, merging in RULES:
            name = f'{placement}_{merging}'
            capacity, merge_us, corrected = minimum_pool(events, floor, upper, placement, merging)
            corrections += corrected
            assert capacity is None or capacity >= row['floor_bytes']
            row[f'{name}_pool_bytes'] = capacity
            row[f'{name}_merge_us'] = merge_us
        assert row['first_lazy_pool_bytes'] <= baseline['highwater_free_bytes']
        rows.append(row)
    with (HERE / 's4_replay.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    summary = summaries(rows)
    print(summary)
    print(f'Validated {len(rows)} traces; global-minimum corrections: {corrections}')
    best_t = figures(rows)
    write_readme(rows, summary, best_t, corrections)
    for svg in sorted((HERE / 'figures').glob('*.svg')):
        svgplot.render_png(svg)


if __name__ == '__main__':
    main()
