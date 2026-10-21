# 2026-10-21 Maximum Runnable SF: Baseline vs. Combined Configuration

## Objective
Measure the maximum SF each query can run on a 16 GB card, comparing the unmodified system (baseline) with the combined configuration (S3 + S2 + S4 + P1).

## Method
- Reuse the adaptive pool search in `experiments/profiling/mem_sweep.py` (as in E3): a query is considered runnable at an SF if any slab size allows it to complete; test SF in ascending order and stop testing larger SF values after a query fails.
- Baseline: no additional arguments. Combined: `--main_args "--free_dead --slab_eps 0.01 --coalesce_every" --alloc_conf expandable_segments:True`.
- SF 10, 15, 20 (SF20 data is 18.4 GB, the largest allowed by the disk quota); then test Q9 and Q21 additionally at SF 11–14 (`fine-*.csv`).
- `--quiet_gpu`: require total device memory usage $\le 0.5\,\mathrm{GB}$ before each run to avoid false OOMs caused by other users' jobs
- Server logs: `~/runs/tqp/20261021-013929-maxsf-baseline/`, `~/runs/tqp/*-maxsf-combined/`.

## Files
| File | Contents |
|---|---|
| `baseline.csv`, `combined.csv` | One row per attempt (same columns as `mem_profiler.py`'s `_COLUMNS`), SF 10/15/20 |
| `fine-baseline.csv`, `fine-combined.csv` | Q9 and Q21 attempts at SF 11–14 |

## Main Results (SF 10/15/20)
| Query | baseline | Combined |
|---|---|---|
| Q21 | OOM even at SF10 | SF10 (SF15 OOM) |
| Q9 | SF10 (SF15 OOM) | SF10 (SF15 OOM) |
| Q7 | SF15 (pool exceeds the 14 GB limit at SF20) | ≥ SF20 |
| Other 19 queries | ≥ SF20 | ≥ SF20 |

After additional tests at SF 11–14 (Q9, Q21):

| Query | baseline | Combined |
|---|---|---|
| Q21 | SF10 OOM (SF7 succeeded in E3) | **SF11** (SF12 OOM) |
| Q9 | **SF10** (SF11 OOM) | **SF12** (SF13 OOM) |
| Q7 | SF15 | ≥ SF20 |

With the combined configuration, all 22 queries run at SF11; baseline already encounters OOM for Q21 at SF10. Additional server logs: `~/runs/tqp/20261021-023727-maxsf-fine-combined/`, `~/runs/tqp/20261021-024137-maxsf-fine-baseline/`.
