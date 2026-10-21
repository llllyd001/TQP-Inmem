"""Build the small pgfplots tables from profiling results (standard library only)."""
import csv
import math
from collections import defaultdict
from pathlib import Path
import re
import statistics


ROOT = Path(__file__).resolve().parents[1]
PROFILING = ROOT / 'experiments/profiling'
OUT = ROOT / 'report/plotdata'
CONFIG_DIR = PROFILING / '2026-10-21-configs'
CONFIGS = ('s3_s2', 's3_s4', 's3_s2_s4', 'p1', 'p1_empty', 's3_p1', 's3_s2_s4_p1')
GB = 10**9


def available(path, name):
    if path.exists():
        return True
    print(f'skip {name}: missing {path.relative_to(ROOT)}')
    return False


def read_csv(path, name):
    if not available(path, name):
        return []
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def key(row):
    return float(row['sf']), int(row['qid'])


def runs(rows):
    return {key(row): row for row in rows if row['status'] == 'ok'}


def reserved(row):
    return float(row['reserved_peak_bytes']) - float(row['reserved_start_bytes'])


def cached(row):
    return reserved(row) - float(row['transient_peak_bytes'])


def context(row):
    # device_used counts other users' jobs on the shared GPU, so use the CUDA context
    # measured once on an idle GPU (median over the baseline runs) for every configuration.
    return CONTEXT


def need(row):
    return row['slab'] + reserved(row) + context(row)


def write(name, fields, rows, counts):
    rows = list(rows)
    OUT.mkdir(exist_ok=True)
    with (OUT / f'{name}.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        # Explicit x positions keep bars aligned with their labels even when a row is all nan.
        x = fields[0] == 'qid'
        writer.writerow(['x', *fields] if x else fields)
        # pgfplots keeps an x position for 'nan' but drops rows with empty cells.
        cells = [[f'{v:.3f}' if isinstance(v, float) else 'nan' if v == '' else v for v in row] for row in rows]
        writer.writerows([[i, *row] for i, row in enumerate(cells)] if x else cells)
    counts[name] = len(rows)


def comparison(name, configs, data, order, metric, counts):
    rows = []
    for q in order:
        values = []
        for config in configs:
            row = data.get(config, {}).get((10, q))
            values.append(metric(row) / GB if row and q != 21 else '')
        rows.append([q, *values])
    write(name, ['qid', *configs], rows, counts)


def totals(configs, data, metric):
    configs = [c for c in configs if data.get(c)]
    if not configs:
        return
    common = set.intersection(*(set(data[c]) for c in configs))
    for sf in sorted({sf for sf, q in common}):
        keys = sorted(k for k in common if k[0] == sf and k != (10, 21))
        if not keys:
            continue
        baseline = sum(metric(data['baseline'][k]) for k in keys)
        for config in configs:
            total = sum(metric(data[config][k]) for k in keys)
            yield sf, config, total / GB, len(keys), 100 * (1 - total / baseline)


def main():
    counts = {}
    summaries = []
    # Load successful runs and the actual slab extent, including holes.
    data = {}
    for config, folder, filename, slab_field in (
        ('baseline', '2026-10-20-baseline-trace', 'baseline_derived.csv', 'pool_peak_bytes'),
        ('s3', '2026-10-20-s3-free-dead', 's3_derived.csv', 'highwater_real_bytes'),
    ):
        data[config] = runs(read_csv(PROFILING / folder / filename, config))
        for row in data[config].values():
            row['slab'] = float(row[slab_field])
    for config in CONFIGS:
        folder = CONFIG_DIR / 'trace' / config
        queries = read_csv(folder / 'queries.csv', config)
        data[config] = runs(queries)
        if not queries:
            continue
        ops = read_csv(folder / 'ops.csv', f'{config}.slab')
        extents = {}
        for row in ops:
            k = key(row)
            extents[k] = max(extents.get(k, 0), int(row['slab_extent_blocks_end']))
        for k, row in data[config].items():
            if k in extents:
                row['slab'] = extents[k] * float(row['block_mb']) * 10**6

    baseline = data['baseline']
    global CONTEXT
    CONTEXT = statistics.median(float(r['device_used_start_bytes']) - float(r['reserved_start_bytes'])
                                for r in baseline.values())
    summaries.append(f'CUDA context (baseline median): {CONTEXT / GB:.3f} GB')
    ranked = sorted((q for sf, q in baseline if sf == 10 and q != 21),
                    key=lambda q: (-need(baseline[10, q]), q))
    order = [21, *ranked]
    e3 = read_csv(PROFILING / '2026-10-13-e3-oom-boundary/e3_sweep_derived.csv', 'q21_oom')
    oom = not e3 or any(key(r) == (10, 21) and r['status'] in ('oom_pool', 'oom_cuda') for r in e3)
    if ranked:
        write('ranking_sf10', ['qid', 'oom', 'need', 'slab', 'torch', 'ctx'],
              [[21, int(oom), '', '', '', '']] + [
                  [q, 0, need(baseline[10, q]) / GB, baseline[10, q]['slab'] / GB,
                   reserved(baseline[10, q]) / GB, context(baseline[10, q]) / GB] for q in ranked], counts)
        write('breakdown_sf10', ['qid', 'live', 'internal', 'dead', 'torch_inuse', 'torch_cached', 'ctx'],
              [[21, '', '', '', '', '', '']] + [
                  [q, *(float(baseline[10, q][f]) / GB for f in
                        ('live_at_h0', 'internal_at_h0', 'dead_at_h0', 'transient_peak_bytes')),
                   cached(baseline[10, q]) / GB, context(baseline[10, q]) / GB] for q in ranked], counts)
        summaries.append('SF10 baseline need top 3: ' + ', '.join(
            f'Q{q}={need(baseline[10, q]) / GB:.3f} GB' for q in ranked[:3]))

    # Compare only the common query set of configurations currently available.
    slab_data = {c: {k: r for k, r in rows.items() if 'slab' in r} for c, rows in data.items()}
    slab_configs = ('baseline', 's3', 's3_s2', 's3_s4', 's3_s2_s4')
    need_configs = ('baseline', 's3', 's3_p1', 's3_s2_s4_p1')
    if ranked:
        comparison('slab_sf10', slab_configs, slab_data, order, lambda r: r['slab'], counts)
        comparison('need_sf10', need_configs, slab_data, order, need, counts)
        p1_configs = ('baseline', 'p1', 'p1_empty', 's3_p1')
        if any(data[c] for c in p1_configs[1:]):
            comparison('p1_sf10', p1_configs, data, order, cached, counts)
            comparison('p1_reserved_sf10', p1_configs, data, order, reserved, counts)
    if baseline:
        slab_totals = list(totals(slab_configs, slab_data, lambda r: r['slab']))
        write('slab_totals', ['sf', 'config', 'total', 'n'], (r[:4] for r in slab_totals), counts)
        for label, rows in [('slab', slab_totals), ('need', totals(need_configs, slab_data, need))]:
            for sf, config, total, n, reduction in rows:
                if config != 'baseline':
                    summaries.append(f'{label} SF{sf:g} {config}: total={total:.3f} GB, '
                                     f'n={n}, reduction={reduction:.3f}%')

    eps = read_csv(PROFILING / '2026-10-21-s2-eps/eps_replay.csv', 'eps_sf10')
    if eps:
        rows = []
        for setting in ('40MB', '0.10', '0.05', '0.02', '0.01'):
            groups = [[r for r in eps if float(r['sf']) == 10 and r['setting'] == setting
                       and r['merge'] == merge] for merge in ('every', 'lazy')]
            ratios = [100 * statistics.mean(float(r['internal_bytes']) / float(r['pool_bytes']) for r in g)
                      if g else '' for g in groups]
            highwater = [sum(float(r['highwater_bytes']) for r in g) / GB if g else '' for g in groups]
            rows.append([setting, '' if setting == '40MB' else float(setting), *ratios, *highwater])
        write('eps_sf10', ['setting', 'eps', 'internal_over_pool_every', 'internal_over_pool_lazy',
                          'highwater_every', 'highwater_lazy'], rows, counts)

    # Maximum SF uses any successful attempt; later failures do not erase successes.
    maxsf = {}
    for config in ('baseline', 'combined'):
        # SF 10/15/20 for all queries, plus SF 11-14 for the queries that failed between 10 and 15.
        rows = [r for name in (config, f'fine-{config}')
                for r in read_csv(PROFILING / f'2026-10-21-maxsf/{name}.csv', f'maxsf.{name}')]
        if rows:
            maxsf[config] = {}
            for r in rows:
                sf, q = key(r)
                if r['status'] == 'ok':
                    maxsf[config][q] = max(sf, maxsf[config].get(q, 0))
    if maxsf and ranked:
        write('maxsf', ['qid', 'baseline', 'combined'],
              ([q, *(maxsf.get(c, {}).get(q, '') for c in ('baseline', 'combined'))] for q in order), counts)

    timing = read_csv(PROFILING / '2026-10-21-timing/timing.csv', 'timing')
    if timing and ranked:
        samples = defaultdict(list)
        for r in timing:
            if float(r['sf']) == 10:
                samples[int(r['qid']), r['config']].append(float(r['time_s']))
        configs = sorted({c for q, c in samples}, key=lambda c: (c != 'baseline', c))
        medians = {k: statistics.median(v) for k, v in samples.items()}
        ratios = defaultdict(list)
        rows = []
        for q in order:
            times = [medians.get((q, c), '') if q != 21 else '' for c in configs]
            ref = medians.get((q, 'baseline'))
            relative = [t / ref if t != '' and ref is not None else '' for t in times]
            rows.append([q, *times, *relative])
            for c, ratio in zip(configs, relative):
                if ratio != '':
                    ratios[c].append(ratio)
        write('timing_sf10', ['qid', *configs, *(f'{c}_ratio' for c in configs)], rows, counts)
        write('timing_summary', ['config', 'geomean_ratio', 'max_ratio'],
              ([c, math.exp(statistics.mean(math.log(v) for v in ratios[c])), max(ratios[c])]
               for c in configs if ratios[c]), counts)

    log = CONFIG_DIR / 'runs.txt'
    if available(log, 'correctness'):
        results = defaultdict(lambda: [0, 0, 0, 0])
        pattern = r'SF=\S+ Q=\d+ config=(\S+) -> (\S+) \([^)]*s\), results=(\S+)'
        for config, status, result in re.findall(pattern, log.read_text()):
            if config == 'ref':
                continue
            counts_for_config = results[config]
            counts_for_config[0] += 1
            if status != 'ok' or result not in ('same', 'DIFF'):
                counts_for_config[3] += 1
            else:
                counts_for_config[1 if result == 'same' else 2] += 1
        write('correctness', ['config', 'runs', 'same', 'diff', 'failed'],
              ([c, *values] for c, values in sorted(results.items())), counts)

    for name, count in counts.items():
        print(f'{name}.csv: {count} rows')
    for summary in summaries:
        print(summary)


if __name__ == '__main__':
    main()
