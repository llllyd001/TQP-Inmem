# 2026-10-21 Result Nondeterminism Check

## Objective
The configuration comparison (`../2026-10-21-configs/`) had two sets of results that differed from the reference: SF10 Q16 (all 7 configurations agree, with only the reference differing) and SF5 Q21 with `p1_empty`. Confirm that these are intermittent behaviors of the original system rather than effects of memory optimization.

## Method
Repeat runs with the same pool sizes, recording the sha256 and first two lines of `results.out`:
- Unmodified program: SF5 Q21 × 5, SF10 Q16 × 5;
- `--empty_cache_per_op`: SF5 Q21 × 5.
Require a quiet GPU (usage $\le 0.5\,\mathrm{GB}$) before each run. Server logs: `~/runs/tqp/20261021-024350-determinism-check/`, `~/runs/tqp/20261021-024658-determinism-p1-empty/`.

## Results (`runs.txt`)
- All five SF10 Q16 runs of the unmodified program are identical and match the 7 optimized configurations (Brand#59 has a count of 64); the reference run in the configuration comparison produced 72. The same unmodified program has produced two different results.
- SF5 Q21: all five unmodified runs and five `--empty_cache_per_op` runs match the reference; the single `p1_empty` run in the configuration comparison included two extra suppliers with counts of 25 and 24.

## Explanation
TQP's hash aggregation (`operators/aggregate.py:320-325`) generates random hash coefficients on each run and distinguishes groups solely by hash value: two distinct groups with the same hash are merged and their counts summed. Both anomalies involve inflated group counts (64 → 72; extra suppliers with inflated counts), consistent with occasional merging. This is behavior of the original system, unrelated to memory optimization; it occurred 2 times in 520 comparisons at SF5–10 (65 in the S3 experiment, 455 in the configuration comparison).
