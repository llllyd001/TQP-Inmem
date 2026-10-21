import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import shlex
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


def generate_data(data, sf, dbgen_dir):
    subprocess.run(['nice', '-n', '10', './dbgen', '-s', str(sf)],
                   cwd=dbgen_dir.expanduser(), input='1\n', text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = sorted(data.glob(f'SF{sf}-tensor-*.pth'))
    digest, size = hashlib.sha256(), 0
    for path in files:
        size += path.stat().st_size
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        digest.update(f'{path.name} {checksum}\n'.encode())
    print(f'SF={sf} data: {len(files)} files, {size / 1e9:.3f} GB, sha256={digest.hexdigest()}', flush=True)


def prepare_data(root, sf, dbgen_dir):
    data = root / Path(json.loads((root / 'config.json').read_text())['tensors path']).expanduser()
    sf1_bytes = sum(path.stat().st_size for path in data.glob('SF1-tensor-*.pth'))
    if any(data.glob(f'SF{sf}-tensor-*.pth')):
        return data, False
    if sf * sf1_bytes > shutil.disk_usage(data).free * 0.15:
        print(f'Sweep stopped: SF={sf} data estimate exceeds 15% of free space; ask the user first.', flush=True)
        return data, None
    try:
        generate_data(data, sf, dbgen_dir)
    except BaseException:
        if sf != 1:
            delete_data(data, sf)
        raise
    return data, True


def check_gpu_idle(gpu_uuid):
    apps = nvidia_smi('--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader')
    for uuid, pid in csv.reader(apps.splitlines(), skipinitialspace=True):
        if uuid.strip() == gpu_uuid:
            raise SystemExit(f'Sweep stopped: GPU {gpu_uuid} is occupied by PID {pid.strip()}')


def gpu_memory(gpu_uuid):
    rows = csv.reader(nvidia_smi('--query-gpu=uuid,memory.total,memory.used',
                                '--format=csv,noheader,nounits').splitlines(), skipinitialspace=True)
    for uuid, total, used in rows:
        if uuid == gpu_uuid:
            return int(total) * 2**20, int(used) * 2**20
    raise RuntimeError(f'GPU {gpu_uuid} not found')


def wait_for_quiet_gpu(gpu_uuid):
    while gpu_memory(gpu_uuid)[1] > 0.5e9:
        print(f'waiting for GPU {gpu_uuid} usage <= 0.5 GB', flush=True)
        time.sleep(30)


def wait_for_headroom(gpu_uuid, need_bytes):
    # Shared GPU: start only when the free memory covers this query; never touch other processes.
    while True:
        total, used = gpu_memory(gpu_uuid)
        if total - used >= need_bytes:
            return
        print(f'waiting for {need_bytes / 1e9:.1f} GB free on {gpu_uuid}', flush=True)
        time.sleep(30)


def delete_data(data, sf):
    for path in data.glob(f'SF{sf}-tensor-*.pth'):
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description='Sweep query memory limits in isolated processes.')
    parser.add_argument('--q', type=int, nargs='+', default=list(range(1, 23)), choices=range(1, 23))
    parser.add_argument('--sf', type=int, nargs='+', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--start_pool_gb', type=float, default=4)
    parser.add_argument('--max_pool_gb', type=float, default=14)
    parser.add_argument('--manage_data', action='store_true')
    parser.add_argument('--dbgen_dir', type=Path, default=Path('~/projects/tpch-dbgen-tensors/build'))
    parser.add_argument('--main_args', default='', help='Quoted arguments appended to main.py')
    parser.add_argument('--alloc_conf', help='PYTORCH_ALLOC_CONF for child processes')
    parser.add_argument('--quiet_gpu', action='store_true', help='Wait for GPU usage <= 0.5 GB')
    args = parser.parse_args()
    main_args = shlex.split(args.main_args)
    env = None if args.alloc_conf is None else {**os.environ, 'PYTORCH_ALLOC_CONF': args.alloc_conf}
    gate = wait_for_quiet_gpu if args.quiet_gpu else check_gpu_idle
    max_blocks = math.floor(args.max_pool_gb * 25 + 1e-9)

    gpu_uuid = os.environ['CUDA_VISIBLE_DEVICES']
    root = Path(__file__).resolve().parents[2]
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    states = {q: (0, 0, 'ok') for q in args.q}
    for sf in sorted(args.sf):
        if all(result != 'ok' for _, _, result in states.values()):
            break
        generated = False
        try:
            if args.manage_data:
                data, generated = prepare_data(root, sf, args.dbgen_dir)
                if generated is None:
                    break
            for q, (last_sf, peak, result) in states.items():
                if result != 'ok':
                    continue
                estimate = peak / 1e9 * (sf / last_sf) * 1.1 if last_sf else args.start_pool_gb
                # Pool sizes use 40 MB blocks (25 blocks = 1 GB).
                blocks = max(1, math.ceil(estimate * 25 - 1e-9))
                low = high = None
                result = 'pool cannot fit'
                while blocks <= max_blocks:
                    # Check the selected physical GPU before every fresh process.
                    gate(gpu_uuid)
                    previous_count = len(read_rows(out))
                    pool = f'{blocks / 25:.2f}'
                    started = time.perf_counter()
                    proc = subprocess.run(
                        ['nice', '-n', '10', sys.executable, 'main.py', '--SF', str(sf), '--q', str(q),
                         '--gpu_pool_gb', pool, '--no_debug', '--mem_profile', str(out), *main_args],
                        env=env,
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
                states[q] = last_sf, peak, result
        finally:
            if generated and sf != 1:
                delete_data(data, sf)
    for q, (last_sf, _, result) in states.items():
        print(f'Q={q}: max successful SF={last_sf or "none"}; result={result}', flush=True)


if __name__ == '__main__':
    main()
