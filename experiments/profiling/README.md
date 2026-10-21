# Memory Profiling Experiments

This directory holds the measurement tools and every experiment behind the memory-optimization report (`report/main.pdf`). `report/README.md` lists the exact command for each table and figure.

## Tools

| File | Purpose |
|---|---|
| `mem_profiler.py` | Query- and operator-level memory measurement (`main.py --mem_profile <csv>`, `--mem_trace <dir>`) |
| `mem_sweep.py` | Runs each query × SF in a fresh process and searches for a slab size that lets it run; writes one row per attempt and a per-query summary of the largest runnable SF |
| `svgplot.py` | Bar, line and scatter charts as SVG (Python standard library only); PNGs are rendered with headless Chrome |

## Layout

```
experiments/profiling/
  YYYY-MM-DD-<label>/        One experiment
    measure_<name>.py        Measurement script (early experiments ran as python -c)
    analyze_<name>.py        Raw CSV → derived CSV + figures; reads and writes its own directory
    <name>.csv, trace/       Raw data (bytes, seconds), kept exactly as measured
    <name>_derived.csv       Raw columns plus derived columns (utilization, fragmentation, ratios, totals)
    figures/                 SVG and PNG figures
    README.md                Objective, setup, method, derived-column definitions, results, limitations
```

A `measure_*.py` script is not changed after it has produced data; a changed measurement gets a new file, so every dataset stays reproducible from the code next to it. `analyze_*.py` scripts may change and are rerun.

## Conventions

- Memory is measured without warm-up, one fresh process per query; timing is measured after a warm-up run with the measurement tools off.
- Units in CSVs are bytes and seconds; GB means 10^9 bytes.
- Figures use a fixed color order (`svgplot.SERIES`), one y-axis per chart, and label only key points. OOM-boundary experiments include a scale → peak chart with the GPU capacity as a reference line.
