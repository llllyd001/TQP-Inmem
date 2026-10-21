"""Compare reference and S3 execution in separate processes at the final E3 pools."""
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '2026-10-20-baseline-trace'))
from measure_baseline import measure, parse_args, run_query
from mem_sweep import read_rows, wait_for_headroom
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from test_main import SIGFIGS_THRESHOLD, _compare_lines


def same_results(a, b):
    # GPU float sums vary in the last digits between identical runs; compare like test_main.py.
    lines_a, lines_b = a.read_text().splitlines(), b.read_text().splitlines()
    try:
        assert len(lines_a) == len(lines_b)
        for i, (x, y) in enumerate(zip(lines_a, lines_b), 1):
            _compare_lines(x, y, i, fixed_sigfigs=SIGFIGS_THRESHOLD)
    except AssertionError:
        return False
    return True


# Per-query GPU need from the baseline run: reserved peak (pool + PyTorch) + ~0.5 GB CUDA context.
BASELINE = Path(__file__).resolve().parents[1] / '2026-10-20-baseline-trace/baseline_derived.csv'
NEED = {(int(r['sf']), int(r['qid'])): int(r['reserved_peak_bytes']) + 0.5e9 for r in read_rows(BASELINE)}


def s3_case(root, out, sf, q, pool, gpu_uuid):
    gate = lambda uuid: wait_for_headroom(uuid, NEED[sf, q])
    results = out / 'results'
    results.mkdir(exist_ok=True)
    runs = []
    for label, flags in [('ref', []), ('free', ['--free_dead', '--mem_profile', str(out / 'queries.csv'),
                                              '--mem_trace', str(out)])]:
        target = results / f'{label}_SF{sf}_Q{q}.out'
        status, elapsed = run_query(root, out, sf, q, pool, gpu_uuid, flags, gate)
        if status == 'ok':
            shutil.copyfile(root / 'results.out', target)
        runs.append((status, elapsed, target))
    ref, free = runs
    comparison = 'unavailable'
    if ref[0] == free[0] == 'ok':
        comparison = 'same' if same_results(ref[2], free[2]) else 'DIFF'
    print(f'SF={sf} Q={q} pool={pool}GB -> ref={ref[0]} ({ref[1]:.1f}s), '
          f'free={free[0]} ({free[1]:.1f}s), results={comparison}', flush=True)


if __name__ == '__main__':
    measure(parse_args('Measure S3 reclamation against an isolated reference run.'), s3_case)
