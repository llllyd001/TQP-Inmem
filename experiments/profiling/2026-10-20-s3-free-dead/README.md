# 2026-10-20 S3 Measurement: Free Blocks After Their Last Read (Approach A)

## Objective
Under the same conditions as baseline, measure the effect of `--free_dead` (remove column names according to the plan + reclaim blocks by actual addresses) on the slab high-water mark and total demand across both sides, and verify result correctness.

## Configuration
- RTX 5080 16 GB (dbresearch1), 40 MB blocks, current placement / coalescing rules (first-fit, coalescing every 6 frees).
- SF 5, 7, 10; pool size is the last successful size in E3; Q21 SF10 is skipped (could not run in E3).
- Two fresh processes per (SF, query): reference (no new flags) and reclamation (`--free_dead --mem_profile --mem_trace`); compare the two `results.out` files using `test_main.py` rules (10 significant digits).
- **Shared GPU:** an automated job under another account was using the card concurrently (2–15 GB). With user approval, each query waited until free GPU memory was at least its measured baseline reserved peak plus 0.5 GB (`mem_sweep.wait_for_headroom`). Memory metrics are per-process and unaffected; `device_used_*` columns include other users' usage and are not used.
- The first reclamation run for SF7 Q21 returned `oom_cuda`: the error showed that another process allocated an additional 2.73 GiB after our headroom check passed. A separate rerun succeeded, with data in `trace-rerun/`; `trace/` retains the original failure, and analysis replaces this group with the rerun data.
- Server logs: `~/runs/tqp/20261021-000223-s3-free-dead/`, `~/runs/tqp/20261021-001848-s3-rerun-sf7-q21/`.

## Files
| File | Contents |
|---|---|
| `measure_s3.py` | Batch measurement (run on the server) |
| `analyze_s3.py` | Offline analysis and plotting (from `experiments/profiling/`: `python3 -B 2026-10-20-s3-free-dead/analyze_s3.py`) |
| `trace/` | Raw records: `queries.csv` (reclamation runs only), `ops.csv` (including `slab_extent_blocks_end`), `allocs.csv`, `reads.csv`, `frees.csv`, `oom.csv` |
| `trace-rerun/` | Rerun records for SF7 Q21 |
| `s3_derived.csv` | All raw columns from `queries.csv` + derived columns + corresponding baseline columns |
| `figures/` | Two figures |

## Derived Columns
"op" denotes an executor operator. The time of $H$ is the end of the operator with the largest occupied-extent endpoint (before reclamation)

| Column | Definition |
|---|---|
| `highwater_real_bytes` | Measured high-water mark: $\max_{\text{operators}}\text{slab\_extent\_blocks\_end} \times 40\,\mathrm{MB}$ (including holes) |
| `live_real` | Actual bytes of allocations still occupied at $H$ and read by that operator or a later one |
| `internal_real` | $\text{occupied block capacity at }H - \text{actual bytes in occupied allocations}$ |
| `kept_dead_real` | Actual bytes of allocations still occupied at $H$ whose last read preceded that operator (not reclaimed promptly by approach A) |
| `kept_mask_real` | Portion of `kept_dead_real` belonging to mask columns (IDs $\ge 10000$, never removed by approach A) |
| `holes_real` | Free blocks within $[0, \text{high-water mark})$ at $H$ |
| `static_need_real_bytes` | $\text{highwater\_real\_bytes} + \text{transient\_peak\_bytes}$: total demand under static partitioning after S3, with the pool sized to the measured high-water mark |
| `concurrent_need_real` | $\max_{\text{operators}}(\text{occupied-extent endpoint} + \text{PyTorch peak within operator})$ |
| `highwater_bytes`, `highwater_free_bytes`, `static_need_bytes`, `concurrent_need_bytes` | From baseline: $H_0$, ideal replay $H_1$, and the two total-demand metrics |

## Figures
- `figures/1_slab_breakdown`: one panel per query; two bars per SF showing baseline $H_0$ and the measured S3 breakdown
- `figures/2_need_sf10`: baseline static demand, static demand after S3, baseline concurrent demand, and concurrent demand after S3 for each SF10 query; the dashed line marks GPU capacity (16.61 GB, assumed).

## Main Results
- **Correctness:** all 65 cases have `results=same`.
- **High-water mark** (sum over all queries at each SF, GB):

  | | SF5 | SF7 | SF10 |
  |---|---|---|---|
  | $H_0$, no reclamation | 50.04 | 65.60 | 71.48 |
  | Measured S3 | 29.80 | 41.76 | 48.12 |
  | Ideal replay $H_1$ | 28.60 | 40.24 | 46.68 |

  Measurements are 33–40% lower than without reclamation and 3–5% above ideal replay; 42/65 cases exactly match replay.
- **Mask columns are negligible:** `kept_mask_real` is at most 0.06 GB; no upgrade to approach B is needed.
- **The gap from ideal replay mainly comes from Spark's overly broad `Input`:** a column appears in the `Input` of every operator it passes through, even if the operator does not read it, delaying reclamation under approach A. The largest gap is Q18 (SF10: measured 3.40 vs. replay 2.68 GB, with 0.62 GB not reclaimed promptly).
- **Total demand across both sides (SF10, GB):** Q9 15.31 → 13.03, Q7 9.46 → 6.02, Q10 8.60 → 6.80, Q8 7.40 → 4.84; Q21 SF7 12.63 → 10.31.
- Tracing was enabled in this experiment; `time_s` is not used for speed comparisons.
