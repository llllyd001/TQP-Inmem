"""Compare slab and allocator configurations at the final E3 pools."""
from pathlib import Path
import shutil
import sys

PROFILING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILING / '2026-10-20-baseline-trace'))
from measure_baseline import measure, parse_args, run_query
from mem_sweep import wait_for_headroom


CONFIGS = {
    'baseline': ([], {}),
    's3': (['--free_dead'], {}),
    's3_s2': (['--free_dead', '--slab_eps', '0.01'], {}),
    's3_s4': (['--free_dead', '--coalesce_every'], {}),
    's3_s2_s4': (['--free_dead', '--slab_eps', '0.01', '--coalesce_every'], {}),
    'p1': ([], {'PYTORCH_ALLOC_CONF': 'expandable_segments:True'}),
    'p1_empty': (['--empty_cache_per_op'], {}),
    's3_p1': (['--free_dead'], {'PYTORCH_ALLOC_CONF': 'expandable_segments:True'}),
    's3_s2_s4_p1': (['--free_dead', '--slab_eps', '0.01', '--coalesce_every'],
                    {'PYTORCH_ALLOC_CONF': 'expandable_segments:True'}),
}


def configs_case(root, out, sf, q, pool, gpu_uuid):
    sys.path.insert(0, str(PROFILING / '2026-10-20-s3-free-dead'))
    from measure_s3 import NEED, same_results

    gate = lambda uuid: wait_for_headroom(uuid, NEED[sf, q])
    results = out / 'results'
    results.mkdir(exist_ok=True)
    ref = results / f'ref_SF{sf}_Q{q}.out'
    ref_status, elapsed = run_query(root, out, sf, q, pool, gpu_uuid, [], gate,
                                    {'PYTORCH_ALLOC_CONF': ''})
    if ref_status == 'ok':
        shutil.copyfile(root / 'results.out', ref)
    print(f'SF={sf} Q={q} config=ref -> {ref_status} ({elapsed:.1f}s), results=reference', flush=True)

    for label, (flags, env) in CONFIGS.items():
        if label in ('baseline', 's3'):
            continue
        config_out = out / label
        config_out.mkdir(exist_ok=True)
        status, elapsed = run_query(
            root, config_out, sf, q, pool, gpu_uuid,
            [*flags, '--mem_profile', str(config_out / 'queries.csv'), '--mem_trace', str(config_out)],
            gate, {'PYTORCH_ALLOC_CONF': '', **env})
        comparison = 'unavailable'
        if status == 'ok':
            target = results / f'{label}_SF{sf}_Q{q}.out'
            shutil.copyfile(root / 'results.out', target)
            if ref_status == 'ok':
                comparison = 'same' if same_results(target, ref) else 'DIFF'
        print(f'SF={sf} Q={q} config={label} -> {status} ({elapsed:.1f}s), results={comparison}', flush=True)


if __name__ == '__main__':
    measure(parse_args('Measure slab and allocator configurations.'), configs_case)
