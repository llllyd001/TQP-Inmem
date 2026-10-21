# 2026-10-12 · SF1 · 4 GB GPU Pool · Per-Query GPU Memory

## Objective
Use the newly added pool counters (`stats()` / `reset_peak()`) to measure, for each query at SF1:
- Pool high-water mark: the minimum required pool size.
- Actual data size in the pool.
- Peak transient memory outside the pool.

This is the first measurement with the pool usage counters (`stats`, `reset_peak`).

## Environment and Code
- dbresearch1 · NVIDIA GeForce RTX 5080 (16.6 GB) · torch 2.14.0+cu130 (CUDA 13.0) · driver 590.48
- Code: `memory-optimization` @ `78a2ad2` (`--gpu_pool_gb` + pool counters), with no uncommitted code changes (the server log git.txt records the old hash `ac5afcd`, before the commit message was rewritten; the code is identical)
- Data: TPC-H SF1 (`~/data/tpch`)
- GPU pool: 4 GB (decimal), block size: 40 MB, 100 blocks total
- Raw server logs: `~/runs/tqp/20261012-001553-sf1-pool4gb-perquery/` (cmd.txt, git.txt, output.log)

## Method
1. Run all 22 queries sequentially in one process using `main.exec(1, [1..22], ...)`, without warmup.
2. Replace the pool's `free_all()` in the measurement script without changing `main.py`. At the end of each query, before clearing the pool, call `torch.cuda.synchronize()` and record:
   - `pool_peak_bytes`: maximum pool capacity allocated during the query (allocated block count × 40 MB).
   - `pool_requested_bytes`: actual requested bytes in the pool at query completion. The pool allocates without freeing during a query, so this is effectively all data placed in the pool by that query.
   - `pool_allocs`: number of pool allocation requests made by the query.
   - `transient_peak_bytes`: `torch.cuda.max_memory_allocated()` minus slab capacity, the peak memory for transient tensors outside the pool.
   - `reserved_peak_bytes`: `torch.cuda.max_memory_reserved()`, including the slab.
3. After recording, call the original `free_all()`, reset the pool high-water mark and PyTorch peak statistics, and start the next query.

## Files
| File | Contents |
| --- | --- |
| `per_query.csv` | Raw server output, preserved unchanged, in bytes |
| `per_query_derived.csv` | All raw columns, with derived columns inserted next to the relevant raw columns (definitions below) |
| `figures/*.svg`, `figures/*.png` | Figures (PNG for convenient viewing, SVG for lossless scaling) |

The code is in `experiments/profiling/` in the repository: `measure_perquery.py` is the measurement code actually run on the server (via `python -c`; the server's cmd.txt lost its quotes, so this copy is authoritative); `analyze_perquery.py` generates derived CSV files and figures. Run it from the repository root: `python3 -B experiments/profiling/analyze_perquery.py "experiments/profiling/2026-10-12-sf1-pool4gb-perquery"`.

### Derived Column Definitions
| Derived column | Inserted after | Formula | Meaning |
| --- | --- | --- | --- |
| `pool_peak_blocks` | `pool_peak_bytes` | $\text{pool high-water mark} / 40\,\mathrm{MB}$ | Pool high-water mark in blocks |
| `pool_utilization_pct` | `pool_requested_bytes` | $100 \times \text{actual data} / \text{pool high-water mark}$ | Pool utilization: fraction of allocated pool space that holds data |
| `pool_internal_frag_bytes` | Same as above | $\text{pool high-water mark} - \text{actual data}$ | Internal fragmentation: wasted pool space (direct subtraction is valid because the pool allocates without freeing) |
| `pool_extra_blocks` | `pool_allocs` | $\text{high-water block count} - \text{allocation count}$ | Blocks attributable to allocations spanning multiple blocks; 0 means every allocation occupies exactly 1 block |
| `avg_bytes_per_alloc` | Same as above | $\text{actual data} / \text{allocation count}$ | Average allocation size, directly comparable to a 40 MB block |
| `transient_over_pool_data` | `transient_peak_bytes` | $\text{transient peak outside pool} / \text{actual data in pool}$ | Extra transient GPU memory used by operators as a multiple of resident data |
| `total_demand_bytes` | Same as above | $\text{pool high-water mark} + \text{transient peak outside pool}$ | Total GPU memory required by the query with the current pool settings (excluding the CUDA context) |

## Results ($\mathrm{MB} = 10^6$ bytes)

| Q | Pool high-water mark | Actual pool data | Utilization | Allocation count | Transient peak outside pool |
|---|---|---|---|---|---|
| 1 | 880 | 234.0 | 26.6% | 18 | 492 |
| 2 | **3560** | 125.5 | **3.5%** | 89 | 78 |
| 3 | 1240 | 227.3 | 18.3% | 28 | 234 |
| 4 | 560 | 145.2 | 25.9% | 13 | 215 |
| 5 | 2040 | 281.6 | 13.8% | 47 | 290 |
| 6 | 360 | 174.0 | 48.3% | 6 | 48 |
| 7 | 2520 | 544.5 | 21.6% | 59 | 266 |
| 8 | 3120 | 288.3 | 9.2% | 73 | 205 |
| 9 | 2840 | 462.7 | 16.3% | 65 | **961** |
| 10 | 2400 | 303.5 | 12.6% | 57 | 412 |
| 11 | 1640 | 118.3 | 7.2% | 41 | 84 |
| 12 | 680 | 221.8 | 32.6% | 15 | 79 |
| 13 | 520 | 183.1 | 35.2% | 11 | 175 |
| 14 | 640 | 185.0 | 28.9% | 13 | 48 |
| 15 | 1240 | 349.2 | 28.2% | 25 | 48 |
| 16 | 920 | 46.5 | 5.0% | 23 | 72 |
| 17 | 1040 | 249.1 | 24.0% | 21 | 205 |
| 18 | 2040 | 386.1 | 18.9% | 45 | 205 |
| 19 | 1160 | 415.5 | 35.8% | 21 | 163 |
| 20 | 1760 | 243.6 | 13.8% | 44 | 98 |
| 21 | 2040 | 489.8 | 24.0% | 45 | **1102** |
| 22 | 680 | 20.7 | 3.0% | 17 | 76 |

- The maximum pool high-water mark is 3560 MB (Q2). Running all 22 queries at SF1 therefore requires a pool of at least 3.56 GB. This explains why the 2.5 GB pool failed on Q2 in the 10-11 run.
- Across all 22 queries, actual pool data accounts for only **16.8%** of the total pool high-water mark; the remaining approximately 83% is internal fragmentation.
- The maximum sum of pool high-water mark and transient peak outside the pool is approximately 3.80 GB (Q9); Q2 ranks second at 3.64 GB.

## Figures

**Figure 1. Per-query GPU memory demand breakdown** (`figures/1_demand_breakdown.png`)
- How to read: each bar is the total GPU memory required by a query. Blue Pool data is actual data in the pool, orange Pool fragmentation is pool fragmentation, and green Transient peak is the transient peak outside the pool. The dashed line marks the 4 GB pool capacity.
- What it shows: most bars are dominated by orange, indicating that fragmentation occupies most pool space. Q9 and Q21 have particularly large green segments and high transient peaks. Q9 has the largest total demand, approximately 3.80 GB.

**Figure 2. Pool utilization** (`figures/2_pool_utilization.png`)
- How to read: a taller bar means a larger fraction of allocated pool space holds data. The dashed line marks the median.
- What it shows: the median is only 20.3%. Q22 (3.0%), Q2 (3.5%), and Q16 (5.0%) are lowest; these queries make many small allocations.

**Figure 3. Pool high-water block count vs. allocation count** (`figures/3_blocks_vs_allocs.png`)
- How to read: each point is a query. A point on the dashed line $y = x$ means each allocation occupies exactly 1 block
- What it shows: all points lie close to the dashed line, deviating by at most 8 blocks. This indicates that required pool size depends on allocation count.

## Derived Metrics (Calculated from per_query.csv)
- Pool utilization (actual data divided by pool high-water mark): median 20.3%, aggregate 16.8% across 22 queries; Q22 (3.0%) and Q2 (3.5%) are lowest.
- Pool high-water block count vs. allocation count: correlation 0.995, differing by at most 8 per query; exactly equal for Q2, Q11, Q16, and Q22.
- Average actual data per allocation: 7.4 MB, compared with a block size of 40 MB.
- Pool high-water mark vs. actual data: correlation only 0.45, indicating that pool demand largely does not track actual data size.
- Rough extrapolation (assuming linear growth to SF10, **speculative only**): transient peaks outside the pool of approximately 11.0 GB for Q21, 9.6 GB for Q9, and 2.0 GB for Q18.

## Observations (Measured)
1. **At SF1, pool size is determined by allocation count rather than data size.** Almost every allocation occupies exactly 1 block (allocation count and high-water block count are nearly identical). Most SF1 columns and operator outputs are therefore smaller than 40 MB, but each allocation is charged a full 40 MB block.
   - Example: Q2 makes 89 allocations occupying 89 blocks (3.56 GB), with only 125 MB of actual data.
2. **Q21 (1.10 GB) and Q9 (0.96 GB) have the largest transient peaks outside the pool.** This matches the high-pressure queries identified in Tasks.md (Q21 is a self-join; Q9 is a multiway join). Q18 uses only 0.2 GB at SF1.

## Hypotheses (Unverified)
- The 40 MB block size was chosen for a "40 GB pool / 1000 blocks" configuration. It is clearly too large for SF1 and a 4 GB pool. Smaller blocks (e.g., 1–4 MB) could reduce Q2's required pool from 3.56 GB to a few hundred MB. The trade-off is a longer free-segment list and more segments to scan during first-fit searches.
- As SF increases, individual columns grow and the fragmentation fraction should decrease. This needs validation at SF2 or SF5.
- Identifying the source of transient peaks outside the pool (e.g. whether they come from join expansion matrices) requires operator-level instrumentation (`mem_profiler.py`).

## Comparison with Tasks.md
Tasks.md identifies Q18, Q9, and Q21 as the first queries to exhaust memory on a 24 GiB card at SF10. Q2 ranks first by pool high-water mark in this experiment; the findings are consistent:
- **Different scale:** this experiment measures only SF1.
- **Different metric:** Q2 ranks first by pool high-water mark, largely inflated by fragmentation. Q21 and Q9 have the largest transient peaks outside the pool, consistent with Tasks.md.
- **Unexplained:** linear extrapolation suggests Q18 should not exhaust memory at SF10. Its memory usage may grow superlinearly, or there may be memory consumption we have not measured.

## Follow-up: Questions and Experiments

| Conclusion / hypothesis | New question | Corresponding experiment |
| --- | --- | --- |
| Pool demand is largely inflated by fragmentation from 40 MB blocks | How much pool space can smaller blocks (40, 8, 1 MB) save? What is the cost (longer free lists, slower first-fit searches)? | Block-size sweep: measure both pool high-water mark and query time. Rough estimates with 1 MB blocks: approximately 0.17 GB for Q2 and 0.57 GB for Q7, the largest |
| Q21 and Q9 are clear outliers in transient peak outside the pool | Which step of which operator produces the peak? Is it join expansion (H1)? Q21 has LeftSemi and LeftAnti self-joins | Operator-level and stage-level measurements with `mem_profiler.py` |
| Linear extrapolation predicts no OOM for Q18 at SF10, contrary to Tasks.md | Does Q18's GPU memory usage grow superlinearly with SF? | Sweep SF1, 2, 5 and plot SF against peak memory |
| The fragmentation fraction should decrease as SF increases | How high is pool utilization at SF5? Does block size still matter? | Same as above, also recording utilization |
| Q6, Q14, and Q15 all have transient peaks outside the pool of approximately 48 MB | Coincidence, or fixed overhead from the same operation (e.g., a temporary copy of the same lineitem column)? | Identify the peak step for these three queries in stage-level measurements |

## Limitations
- Only one run. GPU memory allocation is largely deterministic, but repeat 2–3 times before drawing formal conclusions.
- `transient_peak_bytes` assumes the slab is the only large tensor resident outside the pool during a query. Small tensors retained across queries (e.g., subquery results) are included; the bias should be small.
- `reserved_peak_bytes` cannot be treated as an independent per-query value: the caching allocator retains previously freed blocks, and after `reset_peak_memory_stats()`, the reserved peak starts from the currently reserved amount. It is therefore for reference only and excluded from conclusions.
