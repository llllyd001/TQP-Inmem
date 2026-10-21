"""Replay measured S3 allocation lifetimes for method A, without torch."""
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
S3 = HERE.parent / '2026-10-20-s3-free-dead'
sys.path.insert(0, str(S3))
sys.path.insert(0, str(HERE.parent / '2026-10-20-baseline-trace'))
from analyze_baseline import GB, Pool  # noqa: E402
from analyze_s3 import load_traces, read_rows  # noqa: E402
import svgplot  # noqa: E402

MB = 10**6
SETTINGS = [('40MB', None), ('0.10', 0.10), ('0.05', 0.05), ('0.02', 0.02), ('0.01', 0.01)]
LABELS = ['40 MB', '10%', '5%', '2%', '1%']


def events_for(ops, allocs, frees):
    order = {(op['plan'], op['op']): j for j, op in enumerate(ops)}
    starts, ends = defaultdict(list), defaultdict(list)
    for a, row in enumerate(allocs):
        starts[order[row['plan'], row['op']]].append((a, int(row['start_block']), int(row['requested_bytes'])))
    for row in frees:
        ends[order[row['plan'], row['op']]].append(int(row['start_block']))
    active, events = {}, []
    for j in range(len(ops)):
        for a, start, size in starts[j]:
            assert start not in active
            active[start] = a
        dead = [active.pop(start) for start in ends[j]]
        events.append(([(a, size) for a, start, size in starts[j]], dead))
    return events


def replay(events, block, merging):
    # All rounded requests together are sufficient, even if no span is ever reused.
    capacity = sum(max(1, (size + block - 1) // block) for new, dead in events for a, size in new) * block
    pool = Pool(capacity, block)
    active = {}
    highwater = internal = 0
    for new, dead in events:
        for a, size in new:
            start, count = pool.alloc(size)
            active[a] = (start, count, size)
        # Match S3's first maximal operator-end snapshot, before reclamation.
        extent = max((start + count for start, count, size in active.values()), default=0) * block
        if extent > highwater:
            highwater = extent
            internal = sum(count * block - size for start, count, size in active.values())
        for a in dead:
            start, count, size = active.pop(a)
            if merging == 'every':
                pool.free_times = 5
            pool.free(start, count)
    return highwater, internal


def summaries(rows):
    lines = ['SF  Setting Merge  Sum_H_GB  Mean_I/H_%  Mean_I/P_%  Bound_fail']
    for sf in sorted({r['sf'] for r in rows}):
        for setting, eps in SETTINGS:
            for merging in ('lazy', 'every'):
                group = [r for r in rows if (r['sf'], r['setting'], r['merge']) == (sf, setting, merging)]
                lines.append(f'{sf:<3} {setting:<7} {merging:<5} '
                             f'{sum(r["highwater_bytes"] for r in group) / GB:9.3f} '
                             f'{100 * statistics.mean(r["internal_frac"] for r in group):11.3f} '
                             f'{100 * statistics.mean(r["internal_bytes"] / r["pool_bytes"] for r in group):11.3f} '
                             f'{sum(not r["bound_holds"] for r in group):11}')
    return '\n'.join(lines)


def figures(rows):
    fig = HERE / 'figures'
    fig.mkdir(exist_ok=True)
    sf10 = [r for r in rows if r['sf'] == 10]
    internal = [statistics.mean(r['internal_bytes'] / r['pool_bytes'] for r in sf10
                                if r['setting'] == setting and r['merge'] == 'every') for setting, eps in SETTINGS]
    svgplot.line_chart(fig / '1_internal_vs_eps.svg', range(5),
                       [('Internal / pool (every)', internal), ('Epsilon bound', [eps for setting, eps in SETTINGS])],
                       'Internal fragmentation versus epsilon at SF10',
                       '21 measured queries; Q21 unavailable; mean of per-query ratios',
                       'Internal / pool', 'Block setting', labels=LABELS)
    series = [(merging, [sum(r['highwater_bytes'] for r in sf10
                            if r['setting'] == setting and r['merge'] == merging) / GB
                         for setting, eps in SETTINGS]) for merging in ('lazy', 'every')]
    svgplot.line_chart(fig / '2_highwater_vs_eps.svg', range(5), series,
                       'Total slab high-water mark versus epsilon at SF10',
                       '21 measured queries; Q21 unavailable; unlimited replay pool',
                       'Total high-water (GB)', 'Block setting', labels=LABELS)


def write_readme(rows, summary, mismatches, planning_failures):
    counts = ', '.join(f'SF{sf}: {sum(r["sf"] == sf for r in rows) // 10} queries'
                       for sf in sorted({r['sf'] for r in rows}))
    results = []
    for setting, eps in SETTINGS:
        group = [r for r in rows if r['sf'] == 10 and r['setting'] == setting and r['merge'] == 'every']
        results.append(f'- SF10 / `{setting}` / `every`: total high-water mark '
                       f'{sum(r["highwater_bytes"] for r in group) / GB:.3f} GB, mean internal fragmentation / pool capacity '
                       f'{100 * statistics.mean(r["internal_bytes"] / r["pool_bytes"] for r in group):.3f}%.')
    (HERE / 'README.md').write_text(r'''# 2026-10-21 S2: Offline Replay of Method A's ε and Slab Fragmentation

## Objective
Using measured S3 reclamation timings, compare fixed 40 MB blocks with block sizes planned by method A, quantify changes in internal fragmentation and slab high-water mark, and check theoretical bounds.
Do not simulate PyTorch-side memory or infer GPU runtime from this analysis.

## Configuration and Execution
- Run `python3 -B 2026-10-21-s2-eps/analyze_eps.py` locally from `experiments/profiling/`; analysis uses only the Python standard library, without torch.
- On the server, run `python3 -B 2026-10-21-s2-eps/plan_n_live.py` from the same directory in the project environment
  to rebuild `plan_n_live.csv` from the 22 query pickles; this script depends on the server's torch and plan-deserialization environment and is not run locally.
- Reuse S3's `load_traces()` for input: `trace-rerun/` fully replaces the corresponding `trace/` records by SF / query; analyze successful queries only. SF10 Q21 has no data and is not zero-filled.
- Decimal units: $1\,\mathrm{MB}=10^6$ bytes, $1\,\mathrm{GB}=10^9$ bytes

## Method
Preserve CSV row order for operators, allocations, and frees. Original starting blocks are used only to match the current allocation when freeing; independent allocation IDs are used after matching,
so block-address reuse does not confuse lifetimes. Allocations absent from free records remain until query completion.
For each operator, allocate first, record the state before freeing, then free in the original order.
Use the first operator end at which the occupied-extent endpoint reaches its maximum, matching S3's snapshot definition; only allocations occur within an operator, so this definition cannot miss a higher address peak.

Reuse baseline's `Pool`: first-fit selects the first available segment in free-list order and allocates from its low end; frees append to the end of the list.
`lazy` sorts and coalesces on every sixth free; `every` coalesces on every free. Replay pool capacity is the sum of all rounded-up allocation capacities, ensuring capacity is not a constraint;
the measured pool capacity does not limit replay. The tail segment of a finite pool may change the first-fit path, so fixed-block replay is also compared row by row with measurements.

Use `n_live_free_dead` as the planned column count, taking the maximum of `max_live_columns(p.ops_idx, p.ops_dict, True)` over all plans;
also save `n_live_no_free` without S3 to reproduce the original table. Pool capacity is the query's `gpu_pool_gb` after merging the records.

$$
b_{\mathrm{MB}}=\max\left(1,\left\lfloor\frac{\varepsilon P}{N\,10^6}\right\rfloor\right),\qquad b=10^6 b_{\mathrm{MB}}
$$

$N$ is the maximum of "columns still live on entry to an operator + that operator's output columns" (each output receives a new allocation while inputs remain occupied during operator execution).
The number of simultaneously occupied allocations is at most $N$, and rounding wastes at most one block per allocation, so $I\le Nb$; this experiment checks every row. The 1 MB minimum can also violate $Nb\le\varepsilon P$,
so the script checks this planning inequality separately; fixed 40 MB blocks have no corresponding ε bound.

## Derived Column Definitions
Let $A$ be the set of allocations in the first maximum-high-water snapshot, $s_a$ the requested bytes, $p_a$ the replay starting block, and $c_a=\max(1,\lceil s_a/b\rceil)$ the occupied block count

| Column | Definition |
|---|---|
| `sf`, `qid`, `setting`, `merge` | Scale, query, block-size setting, and coalescing timing; `setting` is `40MB` or a decimal string for ε |
| `block_mb`, `n_live`, `pool_bytes` | $b/10^6$, $N$, $P$ |
| `highwater_bytes` | $H=\max_t\max_{a\in A_t}(p_a+c_a)b$ |
| `internal_bytes` | $I=\sum_{a\in A}(c_ab-s_a)$ |
| `internal_frac` | $I/H$, using the high-water mark including holes as the denominator |
| `bound_bytes` | $Nb$ |
| `bound_holds` | $I\le Nb$, distinct from the planning condition $Nb\le\varepsilon P$ |

## Figures
English SVGs are generated by `svgplot.py`, with `render_png` producing PNGs of the same name.

![Internal fragmentation](figures/1_internal_vs_eps.svg)

Figure 1 uses the arithmetic mean of per-query $I/P$ at SF10 and plots only `every`; the reference line is $y=\varepsilon$, with no reference point for fixed 40 MB blocks

![High-water mark](figures/2_highwater_vs_eps.svg)

Figure 2 sums $H$ over all available SF10 queries, plotting `lazy` and `every` separately; totals do not represent pool capacity for concurrent query execution

## Main Results
''' + f'- Data coverage: {counts}; {len(rows)} output rows.\n'
        + '\n'.join(results)
        + f'\n- Internal fragmentation bound violations: {sum(not r["bound_holds"] for r in rows)} rows.'
        + f'\n- Planning bound violations: {len(planning_failures)} SF / query / ε combination (counting the two coalescing timings only once).'
        + ('\n' + '\n'.join(planning_failures) if planning_failures else '')
        + f'\n- Row-by-row comparison of 40 MB / lazy with measured S3 high-water marks: {len(mismatches)} mismatches.'
        + ('\n' + '\n'.join(mismatches) if mismatches else ' All 65 rows match.')
        + '\n\n## Summary\nMean fragmentation is reported with both the high-water mark and measured pool capacity as denominators; totals are in GB.\n\n```text\n'
        + summary + '\n```\n', encoding='utf-8')


def main():
    traces, grouped = load_traces()
    counts = {r['qid']: int(r['n_live_free_dead']) for r in read_rows(HERE / 'plan_n_live.csv')}
    measured = {(r['sf'], r['qid']): int(r['highwater_real_bytes'])
                for r in read_rows(S3 / 's3_derived.csv') if r['status'] == 'ok'}
    rows, mismatches, planning_failures = [], [], []
    for query in sorted(traces['queries'], key=lambda r: (int(r['sf']), int(r['qid']))):
        if query['status'] != 'ok':
            continue
        key = query['sf'], query['qid']
        events = events_for(*(grouped[name][key] for name in ('ops', 'allocs', 'frees')))
        n, pool_bytes = counts[query['qid']], int(float(query['gpu_pool_gb']) * GB)
        for setting, eps in SETTINGS:
            block_mb = 40 if eps is None else max(1, math.floor(eps * pool_bytes / n / MB))
            bound = n * block_mb * MB
            if eps is not None and bound > eps * pool_bytes:
                planning_failures.append(f'- SF{key[0]} Q{key[1]} ε={eps}: {bound} > {eps * pool_bytes:g} bytes')
            for merging in ('lazy', 'every'):
                highwater, internal = replay(events, block_mb * MB, merging)
                rows.append(dict(sf=int(key[0]), qid=int(key[1]), setting=setting, merge=merging,
                                 block_mb=block_mb, n_live=n, pool_bytes=pool_bytes, highwater_bytes=highwater,
                                 internal_bytes=internal, internal_frac=internal / highwater,
                                 bound_bytes=bound, bound_holds=internal <= bound))
                if setting == '40MB' and merging == 'lazy' and highwater != measured[key]:
                    mismatches.append(f'- SF{key[0]} Q{key[1]}: replay={highwater}, measured={measured[key]} bytes')
    with (HERE / 'eps_replay.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    summary = summaries(rows)
    print(summary)
    print(f'40MB/lazy mismatches: {len(mismatches)}; planning bound failures: {len(planning_failures)}')
    for line in mismatches + planning_failures:
        print(line)
    figures(rows)
    write_readme(rows, summary, mismatches, planning_failures)
    for svg in sorted((HERE / 'figures').glob('*.svg')):
        svgplot.render_png(svg)


if __name__ == '__main__':
    main()
