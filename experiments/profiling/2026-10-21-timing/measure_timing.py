"""Time each query four times in one fresh process; the first run is the warmup and is dropped."""
import argparse
import csv
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '2026-10-21-configs'))
from measure_configs import CONFIGS
from measure_baseline import measure, run_query
from mem_sweep import read_rows, wait_for_quiet_gpu


TIMING_CONFIGS = ('baseline', 's3', 's3_s2_s4', 'p1', 's3_p1', 's3_s2_s4_p1')


def timing_case(root, out, sf, q, pool, gpu_uuid):
    for label in TIMING_CONFIGS:
        flags, env = CONFIGS[label]
        with tempfile.TemporaryDirectory(prefix='tqp-timing-') as temp:
            times = Path(temp) / 'times.csv'
            # main.py's --warmup_iter runs all SF1 queries in this query's pool, which may not fit;
            # warm up on the measured query itself instead.
            status, elapsed = run_query(
                root, out, sf, q, pool, gpu_uuid,
                [*flags, '--q', *[str(q)] * 4, '--time_output_file', str(times)],
                wait_for_quiet_gpu, {'PYTORCH_ALLOC_CONF': '', **env})
            rows = read_rows(times)[1:]
            if status == 'ok' and len(rows) != 3:
                status = 'incomplete'
            if status == 'ok':
                with (out / 'timing.csv').open('a', newline='') as stream:
                    writer = csv.writer(stream)
                    for rep, row in enumerate(rows, 1):
                        writer.writerow([sf, q, label, rep, row['time_elapsed']])
            print(f'SF={sf} Q={q} config={label} -> {status} ({elapsed:.1f}s)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sf', type=int, nargs='+', default=[10])
    parser.add_argument('--pools', type=Path, default=Path(__file__).resolve().parents[1] /
                        '2026-10-13-e3-oom-boundary/e3_sweep_derived.csv')
    parser.add_argument('--out', type=Path, required=True, help='Directory containing timing.csv')
    parser.add_argument('--manage_data', action='store_true')
    parser.add_argument('--dbgen_dir', type=Path, default=Path('~/projects/tpch-dbgen-tensors/build'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / 'timing.csv'
    if not target.exists():
        with target.open('w', newline='') as stream:
            csv.writer(stream).writerow(['sf', 'qid', 'config', 'rep', 'time_s'])
    measure(args, timing_case)


if __name__ == '__main__':
    main()
