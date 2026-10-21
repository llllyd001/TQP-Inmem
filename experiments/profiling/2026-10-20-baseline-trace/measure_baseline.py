import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mem_sweep import check_gpu_idle, delete_data, gpu_uuid, prepare_data, read_rows


def parse_args(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--sf', type=int, nargs='+', default=[5, 7, 10])
    parser.add_argument('--pools', type=Path, default=Path(__file__).resolve().parents[1] /
                        '2026-10-13-e3-oom-boundary/e3_sweep_derived.csv')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--manage_data', action='store_true')
    parser.add_argument('--dbgen_dir', type=Path, default=Path('~/projects/tpch-dbgen-tensors/build'))
    return parser.parse_args()


def measure(args, run_case):
    pools = {(int(row['sf']), int(row['qid'])): row['gpu_pool_gb'] for row in read_rows(args.pools)
             if row['status'] == 'ok' and row['final'].lower() in ('true', '1')}
    root = Path(__file__).resolve().parents[3]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    uuid = gpu_uuid()

    for sf in sorted(args.sf):
        generated = False
        try:
            if args.manage_data:
                data, generated = prepare_data(root, sf, args.dbgen_dir)
                if generated is None:
                    break
            for q in range(1, 23):
                pool = pools.get((sf, q))
                if pool is None:
                    print(f'SF={sf} Q={q}: skipped (no successful final E3 pool)', flush=True)
                    continue
                run_case(root, out, sf, q, pool, uuid)
        finally:
            if generated and sf != 1:
                delete_data(data, sf)


def run_query(root, out, sf, q, pool, gpu_uuid, flags, gate=check_gpu_idle, env=None):
    gate(gpu_uuid)
    queries = out / 'queries.csv'
    previous_count = len(read_rows(queries))
    started = time.perf_counter()
    proc = subprocess.run(
        ['nice', '-n', '10', sys.executable, 'main.py', '--SF', str(sf), '--q', str(q),
         '--gpu_pool_gb', pool, '--no_debug', *flags],
        env=None if env is None else {**os.environ, **env},
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
    )
    elapsed = time.perf_counter() - started
    rows = read_rows(queries)
    status = rows[-1]['status'] if len(rows) > previous_count else 'crash'
    if '--mem_profile' not in flags:
        status = 'ok' if proc.returncode == 0 else 'crash'
    if proc.returncode and status == 'ok':
        status = 'crash'
    if status == 'crash':
        print(f'Child exit code: {proc.returncode}\n' + '\n'.join(proc.stdout.splitlines()[-20:]), flush=True)
    return status, elapsed


def baseline_case(root, out, sf, q, pool, gpu_uuid):
    status, elapsed = run_query(root, out, sf, q, pool, gpu_uuid,
                                ['--mem_profile', str(out / 'queries.csv'), '--mem_trace', str(out)])
    print(f'SF={sf} Q={q} pool={pool}GB -> {status} ({elapsed:.1f}s)', flush=True)


def main():
    measure(parse_args('Measure baseline operator memory in isolated processes.'), baseline_case)


if __name__ == '__main__':
    main()
