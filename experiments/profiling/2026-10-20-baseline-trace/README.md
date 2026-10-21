# 2026-10-20 Baseline: Slab High-Water Mark Breakdown and Total Demand Across Both Sides

## Objective
Without changing program behavior, record each query's slab allocations, the slab blocks read by each operator, and each operator's PyTorch usage to answer offline:
how much S2 / S3 / S4 contribute to the slab high-water mark; how far freeing blocks after their last read can reduce it; and how much more memory static partitioning requires than the sum of both sides at the same time.

## Configuration
- RTX 5080 16 GB (dbresearch1), 40 MB blocks, one fresh process per query, no warmup.
- SF 5, 7, 10; pool size is the last successful size in E3 for that (SF, query), ensuring neither side runs out of memory. Q21 SF10 could not run in E3 and is skipped.
- All recording happens on the CPU and is written to disk at query completion. Query-level metrics and `results.out` are identical with recording enabled or disabled (verified on SF1 Q2/Q6/Q9). This experiment's `pool_peak_bytes`, `pool_requested_bytes`, `pool_allocs`, and `transient_peak_bytes` exactly match E3.
- Server logs: `~/runs/tqp/20261020-221724-baseline-trace/`; raw traces: `~/runs/tqp/baseline-trace-data/`.

## Files
| File | Contents |
|---|---|
| `measure_baseline.py` | Batch measurement (run on the server) |
| `analyze_baseline.py` | Offline analysis and plotting (run locally from `experiments/profiling/`: `python3 -B 2026-10-20-baseline-trace/analyze_baseline.py`) |
| `trace/queries.csv` | Raw query-level records (`mem_profiler.py`'s `_COLUMNS`) |
| `trace/ops.csv`, `allocs.csv`, `reads.csv`, `oom.csv` | Raw operator-level records (`_TRACE_COLUMNS`; `oom.csv` is empty, with no failures in this experiment) |
| `baseline_derived.csv` | All raw columns from `queries.csv` + derived columns |
| `figures/` | Two figures |

## Derived Columns
"op" denotes an executor operator; "last read" is defined at operator granularity: data is considered dead only after the entire reading operator finishes (a conservative definition). Reads are mapped to blocks by actual addresses, so slices and aliases automatically refer to the same blocks.

| Column | Definition |
|---|---|
| `highwater_bytes` ($H_0$) | $\text{total allocated blocks} \times 40\,\mathrm{MB}$; must equal `pool_peak_bytes` (checked by the script) |
| `live_at_h0` | Actual bytes of data that will be read after $H_0$ (the operator containing the last allocation) |
| `internal_at_h0` (S2) | $\text{capacity of all occupied blocks} - \text{actual bytes they contain}$ |
| `dead_at_h0` (S3) | Actual bytes of data that will no longer be read at $H_0$ but still occupy blocks |
| `live_peak_bytes` | Maximum actual bytes of live data over all operators; a lower bound for any storage scheme |
| `highwater_free_bytes` ($H_1$) | High-water mark from replaying "free blocks after their last read" under the rules in `IO/pinned_mem.py` |
| `live_at_h1`, `internal_at_h1` | Live data and internal fragmentation at $H_1$ |
| `holes_at_h1` (S4) | Free blocks within $[0, H_1)$ at $H_1$ |
| `slab_side_peak`, `torch_side_peak` | `pool_peak_bytes` and `transient_peak_bytes`, respectively |
| `static_need_bytes` | Total demand under static partitioning: the sum of the two peaks |
| `concurrent_need_bytes` | $\max_{\text{operators}}(\text{slab usage at operator end} + \text{PyTorch peak within operator})$; conservative |
| `concurrent_need_free_bytes` | Same as above, using the slab's occupied extent after replaying "free blocks after their last read" |
| `cached_unused_peak_bytes` | $\max_{\text{operators}}(\text{reserved\_peak} - \text{allocated\_peak})$: memory cached by PyTorch but not in use |

$H_0 = \text{live} + \mathrm{S2} + \mathrm{S3}$; $H_1 = \text{live} + \text{internal fragmentation} + \mathrm{S4}$ (true for every row). Note that the $H_0$ and $H_1$ breakdowns use different moments: $H_0$ is at the end of the query, when most data is dead; $H_1$ is at the maximum occupied extent during replay, when most data is still live

## Figures
- `figures/1_slab_breakdown`: one panel per query; two bars per SF showing the breakdowns of $H_0$ and $H_1$
- `figures/2_static_vs_concurrent`: three total-demand metrics per query at SF10; the dashed line marks GPU capacity (16.61 GB, assumed).

## Main Results (SF10, Except Q21 at SF7)
- Dead data (S3) is the largest component of the high-water mark: median 2.26 GB, maximum 5.43 GB (Q7); S2 has a median of 0.66 GB and maximum of 3.02 GB (Q2).
- Replaying "free blocks after their last read" reduces the median high-water mark from 3.04 to 2.36 GB ($H_1$), but introduces S4 holes: median 0.24 GB, reaching 2.12 GB for Q19, nearly offsetting its benefit ($H_0$: 4.68 → $H_1$: 4.64 GB).
- Q9: static partitioning 15.31 GB, concurrent demand 14.63 GB, and 10.59 GB with last-read reclamation. Q21 SF7: 12.63 → 10.31 → 10.31 GB.
- Concurrent total demand is unchanged for Q1, Q4, Q6, Q12, Q13, Q14, Q19, and Q22 after last-read reclamation: at the operator with the highest total demand, no slab data can yet be freed.
