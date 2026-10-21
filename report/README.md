# Reproducing the report

All commands run from the repository root after following `Setup.md`, with `config.json`
pointing at the tpch-dbgen-tensors `data/` directory. `--manage_data` generates each SF with
dbgen (`--dbgen_dir`, default `~/projects/tpch-dbgen-tensors/build`), runs, and deletes it again.
Each experiment folder in `experiments/profiling/` has a README with the derived columns and
results; copy a measurement's `--out` directory into that folder's `trace/` before analysing.

## Switches (all off by default)

| Switch | Change |
|---|---|
| `--free_dead` | S3: last-use reclamation |
| `--slab_eps 0.01` | S2: block size from a 1% waste budget |
| `--coalesce_every` | S4: merge free runs on every free |
| `PYTORCH_ALLOC_CONF=expandable_segments:True` | P1 (older PyTorch: `PYTORCH_CUDA_ALLOC_CONF`) |
| `--empty_cache_per_op` | control for P1 |
| `--mem_profile <csv>`, `--mem_trace <dir>` | memory measurement |
| `--poison_freed`, `--check_refs` | safety tests for `--free_dead` |

Correctness at SF 1 with every switch on:

```
python -m pytest -p no:cacheprovider -q test_main.py
python main.py --SF 1 --q $(seq 1 22) --free_dead --slab_eps 0.01 --coalesce_every --poison_freed --check_refs
```

## Experiments

| Report | Command |
|---|---|
| Slab sizes (E3 search, unmodified) | `python experiments/profiling/mem_sweep.py --sf 1 2 3 5 7 10 --manage_data --out <csv>` |
| Fig. 3, 4: baseline trace, SF 5/7/10 | `python experiments/profiling/2026-10-20-baseline-trace/measure_baseline.py --out <dir> --manage_data`, then `analyze_baseline.py` |
| S3 runs and replay | `python experiments/profiling/2026-10-20-s3-free-dead/measure_s3.py --out <dir> --manage_data`, then `analyze_s3.py` |
| S4 placement replay | `python experiments/profiling/2026-10-20-s4-placement-replay/analyze_s4.py` |
| Fig. 6: S2 ε replay | `python experiments/profiling/2026-10-21-s2-eps/plan_n_live.py`, then `analyze_eps.py` |
| Table 2, Fig. 5, 7: all configurations | `python experiments/profiling/2026-10-21-configs/measure_configs.py --out <dir> --manage_data` |
| Table 4: run time, SF 10 | `python experiments/profiling/2026-10-21-timing/measure_timing.py --sf 10 --out <dir> --manage_data` |
| Table 3: largest SF, unmodified | `python experiments/profiling/mem_sweep.py --sf 10 15 --manage_data --quiet_gpu --out <csv>`; `--sf 11 12 13 14 --q 9`; `--sf 8 9 --q 21` |
| Table 3: largest SF, all four | the same with `--main_args "--free_dead --slab_eps 0.01 --coalesce_every" --alloc_conf expandable_segments:True`; `--sf 11 12 13 14 --q 9 21` |
| Determinism check | see `experiments/profiling/2026-10-21-determinism/README.md` |

Then build the figures' tables and the PDF:

```
cd report && python make_data.py && latexmk -pdf main.tex
```
