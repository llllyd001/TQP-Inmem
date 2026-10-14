import argparse
import csv
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def nvidia_smi(*args):
    # stderr dropped: the broken GPU0 on this server always prints a warning there
    return subprocess.check_output(['nvidia-smi', *args], text=True, stderr=subprocess.DEVNULL)


def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description='Sweep query memory limits in isolated processes.')
    parser.add_argument('--q', type=int, nargs='+', default=list(range(1, 23)), choices=range(1, 23))
    parser.add_argument('--sf', type=int, nargs='+', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--start_pool_gb', type=float, default=4)
    parser.add_argument('--max_pool_gb', type=float, default=14)
    args = parser.parse_args()
    max_blocks = math.floor(args.max_pool_gb * 25 + 1e-9)

    gpu_uuid = os.environ['CUDA_VISIBLE_DEVICES']
    root = Path(__file__).resolve().parents[2]
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    summaries = []
    for q in args.q:
        last_sf, peak = 0, 0
        result = 'ok'
        for sf in sorted(args.sf):
            estimate = peak / 1e9 * (sf / last_sf) * 1.1 if last_sf else args.start_pool_gb
            # Pool sizes use 40 MB blocks (25 blocks = 1 GB).
            blocks = max(1, math.ceil(estimate * 25 - 1e-9))
            low = high = None
            result = 'pool cannot fit'
            while blocks <= max_blocks:
                # Check the selected physical GPU before every fresh process.
                apps = nvidia_smi('--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader')
                for uuid, pid in csv.reader(apps.splitlines(), skipinitialspace=True):
                    if uuid.strip() == gpu_uuid:
                        raise SystemExit(f'Sweep stopped: GPU {gpu_uuid} is occupied by PID {pid.strip()}')
                previous_count = len(read_rows(out))
                pool = f'{blocks / 25:.2f}'
                started = time.perf_counter()
                proc = subprocess.run(
                    ['nice', '-n', '10', sys.executable, 'main.py', '--SF', str(sf), '--q', str(q),
                     '--gpu_pool_gb', pool, '--no_debug', '--mem_profile', str(out)],
                    cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
                )
                rows = read_rows(out)
                row = rows[-1] if len(rows) > previous_count else {}
                status = row.get('status', 'crash')
                print(f'SF={sf} Q={q} pool={pool}GB -> {status} ({time.perf_counter() - started:.1f}s)',
                      flush=True)
                if status == 'crash':
                    print(f'Child exit code: {proc.returncode}\n' + '\n'.join(proc.stdout.splitlines()[-20:]))
                    result = 'crash'
                    break
                if status == 'ok':
                    last_sf, peak = sf, int(row['pool_peak_bytes'])
                    result = 'ok'
                    break
                if status == 'oom_pool':
                    low = blocks
                else:
                    high = blocks
                    # The pool may have been oversized and squeezed the transient space:
                    # retry with just what it held at the failure (+10%) before calling it a true OOM
                    needed = math.ceil(int(row['pool_peak_bytes']) / 1e9 * 1.1 * 25)
                if high is None:
                    blocks = (blocks * 3 + 1) // 2
                elif low is None and needed < blocks:
                    blocks = needed
                elif low is not None and high - low > 5:
                    blocks = (low + high + 1) // 2
                else:
                    result = 'true OOM'
                    break
            if result != 'ok':
                result += f' at SF={sf}'
                break
        summaries.append(f'Q={q}: max successful SF={last_sf or "none"}; result={result}')
    print('\n'.join(summaries), flush=True)


if __name__ == '__main__':
    main()
