# 2026-10-13 · SF1 · Does Warmup Affect Query-Level GPU Memory Measurements?

## Objective
Determine whether query-level GPU memory measurements require warmup.

- **Hypothesis:** warmup affects timing but not GPU memory. The number of bytes a tensor requires does not depend on whether this is the first run.
- **Criterion:** if the pool high-water mark, actual data size, allocation count, transient peak outside the pool, and reserved peak are identical (byte for byte) under all three conditions, GPU memory measurements do not require warmup. Timing should differ substantially as a control.

## Environment and Code
- dbresearch1 · NVIDIA GeForce RTX 5080 (16.6 GB) · torch 2.14.0+cu130 · driver 590.48
- Code: `memory-optimization` @ `4f9338b`, with no uncommitted repository code changes
- Data: TPC-H SF1; GPU pool: 4 GB, block size: 40 MB
- Runs did not use `--log`; complete raw output is saved in `stdout.log` in this directory, with no files left on the server

## Method
- Each run uses a **separate fresh process**, measured with `experiments/profiling/measure_warmup.py`.
- Queries: Q1 (large aggregation), Q2 (highest pool high-water mark), Q9, and Q21 (the two highest transient peaks outside the pool).
- Measure each query under three conditions:

| Condition (label) | Procedure |
| --- | --- |
| `first run` | First execution of the query in a fresh process |
| `repeat in same process` | Execute the query again immediately in the same process |
| `after warmup` | Start another fresh process, run TQP's built-in `warmup()` (all 22 SF1 queries once), then execute the query |

- Repeat the entire experiment 2 times (`repeat` = 1, 2), using 16 processes and producing 24 rows.
- GPU memory is recorded as in the 2026-10-12 experiment: read counters before `free_all()`, then reset them. Timing uses TQP's own timer, excluding disk reads and output.

## Files
| File | Contents |
| --- | --- |
| `stdout.log` | Complete output from 16 processes |
| `warmup.csv` | Raw data extracted from `stdout.log`, in bytes and seconds |
| `warmup_derived.csv` | All raw columns plus derived columns |
| `figures/` | 3 figures, each in PNG and SVG |

The code is in `experiments/profiling/` in the repository: `measure_warmup.py` for measurement and `analyze_warmup.py` for analysis and plotting.

### Derived Column Definitions
| Derived column | Inserted after | Formula | Meaning |
| --- | --- | --- | --- |
| `label` | `run` | Combination of condition and run | Which of the three conditions this row represents |
| `memory_identical_to_first_run` | `reserved_peak_bytes` | Whether all 5 GPU memory columns equal the first run for the same query and repeat | True means identical byte for byte |
| `device_used_diff_bytes` | `device_used_bytes` | $\text{device usage in this row} - \text{device usage in first run}$ | Additional memory used on the entire card, including usage outside PyTorch |
| `time_speedup_vs_first_run` | `time_s` (last column) | $\text{first-run time} / \text{time in this row}$ | Speedup relative to the first run |

## Results
| Metric | Result |
| --- | --- |
| 5 GPU memory columns (pool high-water mark, actual data, allocation count, transient peak, reserved peak) | **All 24 rows match the corresponding first run byte for byte**; both repeats are also identical |
| Device memory used | `repeat in same process`: +0 bytes; `after warmup`: exactly **35,651,584 bytes (34 MiB)** more for every query |
| Time | First run: 255–316 ms; the other two conditions: 12–35 ms, **7.5–22.5 times faster** |
| Comparison with the 2026-10-12 experiment in a single process | All 5 GPU memory columns for the 4 queries exactly match that experiment's `per_query.csv` |

## Figures
**Figure 1. Transient peak** (`figures/1_transient_peak.png`)
- How to read: each query has three bars showing the transient peak outside the pool under the three conditions.
- What it shows: all three bars in each group have the same height (identical byte for byte); warmup does not change the transient peak.

**Figure 2. Pool high-water mark** (`figures/2_pool_peak.png`)
- How to read: same as above, using pool high-water mark instead.
- What it shows: all three bars have the same height; warmup does not change the pool high-water mark either.

**Figure 3. Query time** (`figures/3_time.png`)
- How to read: the same three conditions, using query time instead.
- What it shows: only the first run is high; the other two conditions are much lower. Warmup affects timing.

## Observations (Measured)
1. **Query-level GPU memory measurements do not require warmup.** All 5 GPU memory metrics are identical byte for byte for the first run, a repeat in the same process, and a run after warmup, including across both full repeats.
2. **Warmup adds only a fixed 34 MiB to total device usage.** This is outside PyTorch's statistics (allocated and reserved are unchanged), identical for every query, and independent of the query itself.
3. **Timing requires warmup.** The first run is 7.5–22.5 times slower, so performance measurements must warm up first or discard the first result.
4. **The 10-12 experiment in a single process is reliable.** Measurements for these 4 queries in separate processes exactly match that experiment, indicating that preceding queries in the same process had no residual effect on them.

## Hypotheses (Unverified)
- The extra 34 MiB likely comes from CUDA loading additional kernels (GPU programs) while warmup runs other queries, expanding the CUDA context. The evidence is its fixed size and absence from PyTorch's statistics.
- Possible causes of the slower first run include initial kernel loading, initial overhead from `torch.jit.load` or plan parsing, and the caching allocator's first device-memory requests. This experiment does not distinguish these factors.

## Conclusions and Rules
- **GPU memory measurements:** use `--warmup_iter 0` and a fresh process per query. This produces correct measurements while avoiding history effects from warmup. Account for fixed overhead such as the CUDA context separately within the 16 GB memory budget.
- **Timing measurements:** disable measurement tools, warm up first or discard the first run; repeat each configuration 3–5 times and report the median.

## Follow-up: Questions and Experiments
| Conclusion / hypothesis | New question | Corresponding experiment | Position in schedule |
| --- | --- | --- | --- |
| GPU memory measurements do not require warmup | The formal query-level tool can use "one process per query, no warmup" directly | Query-level measurement tool and batch runner | B1 |
| Fixed overhead such as the CUDA context is part of the OOM budget | What is the total fixed overhead on a 16 GB card (context, kernels, library workspaces)? Does it change with larger SF? | Record device memory usage before query execution in scaling experiments | B2 / E3 |
| The first run is 7.5–22.5 times slower | Where is the extra time spent? Could it affect performance comparisons? | Break down first-run time with the PyTorch profiler during phase E performance evaluation | E1 (optional) |

## Limitations
- Only 4 queries at SF1 with a 4 GB pool were measured. Byte-for-byte memory equality should in principle be independent of scale, but larger SF values have not been tested.
- Only query-level measurements were tested. Stage-level measurements require more synchronization; confirm that warmup has no effect once the tool is ready.
- Timing serves only as a control, with 2 measurements per condition; it is not a performance result.
