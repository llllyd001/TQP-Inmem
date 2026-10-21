"""Analyze S3 traces in trace/; allocation lifetimes are delimited by frees.csv."""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mem_sweep import read_rows
import svgplot

GB = 10**9
SLAB = ('highwater_real_bytes', 'live_real', 'internal_real', 'kept_dead_real', 'kept_mask_real',
        'holes_real', 'highwater_bytes', 'highwater_free_bytes')
NEED = ('static_need_real_bytes', 'concurrent_need_real', 'static_need_bytes', 'concurrent_need_bytes')


def analyze(query, ops, allocs, reads, frees):
    block = int(float(query['block_mb']) * 10**6)
    order = {(op['plan'], op['op']): j for j, op in enumerate(ops)}
    grouped = []
    for records in (allocs, reads, frees):
        by_op = defaultdict(list)
        for row in records:
            by_op[order[row['plan'], row['op']]].append(row)
        grouped.append(by_op)
    new, accesses, releases = grouped
    active, allocations, snapshots = {}, [], []
    concurrent = 0
    for j, op in enumerate(ops):
        for row in new[j]:
            start, count, size = (int(row[k]) for k in ('start_block', 'blocks', 'requested_bytes'))
            if start in active:
                raise ValueError(f'Allocation at block {start} reused without a free')
            active[start] = len(allocations)
            allocations.append(dict(start=start, count=count, size=size, last=-1, mask=False))
        for read in accesses[j]:
            if read['in_slab'] != 'True':
                continue
            first, last = int(read['first_block']), int(read['last_block'])
            for a in active.values():
                allocation = allocations[a]
                if first < allocation['start'] + allocation['count'] and allocation['start'] <= last:
                    allocation['last'] = j
                    allocation['mask'] |= int(read['col']) >= 10_000
        occupied = sum(allocations[a]['count'] for a in active.values())
        extent = max((s + allocations[a]['count'] for s, a in active.items()), default=0)
        if occupied != int(op['slab_used_blocks_end']) or extent != int(op['slab_extent_blocks_end']):
            raise ValueError(f"SF{query['sf']} Q{query['qid']} op {j}: slab trace mismatch")
        snapshots.append(list(active.values()))
        concurrent = max(concurrent, extent * block + int(op['allocated_peak']) - int(query['allocated_start_bytes']))
        for row in releases[j]:
            del active[int(row['start_block'])]

    # The snapshot is before reclamation, exactly as recorded by op_end().
    k = max(range(len(ops)), key=lambda j: int(ops[j]['slab_extent_blocks_end']))
    highwater = int(ops[k]['slab_extent_blocks_end']) * block
    occupied = [allocations[a] for a in snapshots[k]]
    live = sum(a['size'] for a in occupied if a['last'] > k)
    dead = sum(a['size'] for a in occupied if a['last'] <= k)
    capacity = sum(a['count'] for a in occupied) * block
    return dict(highwater_real_bytes=highwater, live_real=live, internal_real=capacity - live - dead,
                kept_dead_real=dead, kept_mask_real=sum(a['size'] for a in occupied if a['last'] <= k and a['mask']),
                holes_real=highwater - capacity, concurrent_need_real=concurrent,
                static_need_real_bytes=highwater + int(query['transient_peak_bytes']))


def main():
    traces = {name: read_rows(HERE / 'trace' / f'{name}.csv')
              for name in ('queries', 'ops', 'allocs', 'reads', 'frees')}
    baseline = {(r['sf'], r['qid']): r for r in
                read_rows(HERE.parent / '2026-10-20-baseline-trace/baseline_derived.csv')}
    grouped = {}
    for name in ('ops', 'allocs', 'reads', 'frees'):
        grouped[name] = defaultdict(list)
        for row in traces[name]:
            grouped[name][row['sf'], row['qid']].append(row)
    rows = []
    seen = set()
    for query in traces['queries']:
        key = query['sf'], query['qid']
        if key in seen:
            raise ValueError(f'Duplicate query {key}; use a fresh measurement directory')
        seen.add(key)
        derived = dict.fromkeys(SLAB + NEED, '')
        if query['status'] == 'ok':
            derived.update(analyze(query, *(grouped[name][key] for name in ('ops', 'allocs', 'reads', 'frees'))))
            for column in ('highwater_bytes', 'highwater_free_bytes', 'static_need_bytes', 'concurrent_need_bytes'):
                derived[column] = int(baseline[key][column])
        row = {}
        for column, value in query.items():
            row[column] = value
            adjacent = {'pool_peak_bytes': SLAB, 'transient_peak_bytes': NEED}.get(column, ())
            row.update((name, derived[name]) for name in adjacent)
        rows.append(row)
    with (HERE / 's3_derived.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    lookup = {(int(r['sf']), int(r['qid'])): r for r in rows if r['status'] == 'ok'}
    sfs = sorted({int(r['sf']) for r in rows})
    qids = range(1, 23)
    panels = []
    for q in qids:
        groups = []
        for sf in sfs:
            r = lookup.get((sf, q))
            if r is None:
                groups.append(None)
                continue
            b = baseline[str(sf), str(q)]
            groups.append([
                [int(b['live_at_h0']) / GB, int(b['internal_at_h0']) / GB, int(b['dead_at_h0']) / GB, 0, 0],
                [r['live_real'] / GB, r['internal_real'] / GB,
                 (r['kept_dead_real'] - r['kept_mask_real']) / GB, r['holes_real'] / GB, r['kept_mask_real'] / GB]])
        panels.append((f'Q{q}', groups))
    fig = HERE / 'figures'
    fig.mkdir(exist_ok=True)
    slab = fig / '1_slab_breakdown.svg'
    svgplot.stacked_grid(slab, sfs, panels, ['Live', 'Internal (S2)', 'Dead / kept dead', 'Holes (S4)', 'Kept mask'])
    # stacked_grid's default caption describes baseline replay; label this pair as measured S3.
    slab.write_text(slab.read_text().replace('H1 free after last read', 'H1 measured S3 (before reclamation)'))
    svgplot.bar_chart(
        fig / '2_need_sf10.svg', [f'Q{q}' if (10, q) in lookup else f'Q{q}*' for q in qids],
        [(label, [lookup[10, q][key] / GB if (10, q) in lookup else 0 for q in qids])
         for label, key in [('Baseline static', 'static_need_bytes'), ('S3 static', 'static_need_real_bytes'),
                            ('Baseline concurrent', 'concurrent_need_bytes'),
                            ('S3 concurrent', 'concurrent_need_real')]],
        'Baseline and measured S3 memory demand at SF10',
        '*Missing / failed query; operator-level demand; reference: 16.61 GB decimal',
        'Memory (GB)', 'TPC-H query', ref=(16.61, 'GPU capacity: 16.61 GB (assumed)'), grouped=True)
    for svg in fig.glob('*.svg'):
        svgplot.render_png(svg)
    print(f'Wrote {len(rows)} query rows to s3_derived.csv and two SVG/PNG figures.')
    for sf in sfs:
        print(f'\nSF{sf}: median / max (GB)')
        for column in SLAB + NEED:
            values = [r[column] / GB for (scale, q), r in lookup.items() if scale == sf]
            if values:
                print(f'  {column:30s} {statistics.median(values):8.3f} / {max(values):8.3f}')


if __name__ == '__main__':
    main()
