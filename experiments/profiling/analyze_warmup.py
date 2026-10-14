"""<exp dir>/warmup.csv (raw) -> warmup_derived.csv + figures/.
Run from the repo root: python3 -B experiments/profiling/analyze_warmup.py 'Other materials/experiments/2026-10-13-sf1-warmup-check'"""
import csv
import sys
from pathlib import Path

import svgplot

here = Path(sys.argv[1]).resolve()
rows = list(csv.DictReader(open(here / "warmup.csv")))
MB = 10**6
MEM = ["pool_peak_bytes", "pool_requested_bytes", "pool_allocs", "transient_peak_bytes", "reserved_peak_bytes"]

# Baseline for every row: the first-ever run (cold, run 1) of the same query and repeat
base = {(r["qid"], r["repeat"]): r for r in rows if r["condition"] == "cold" and r["run"] == "1"}
out = []
for r in rows:
    b = base[(r["qid"], r["repeat"])]
    d = dict(r)
    d["label"] = "first run" if r is b else ("repeat in same process" if r["condition"] == "cold" else "after warmup")
    d["memory_identical_to_first_run"] = all(r[k] == b[k] for k in MEM)
    d["device_used_diff_bytes"] = int(r["device_used_bytes"]) - int(b["device_used_bytes"])
    d["time_s"] = r["time_s"]
    d["time_speedup_vs_first_run"] = round(float(b["time_s"]) / float(r["time_s"]), 1)
    out.append(d)

fields = list(rows[0].keys())
fields.insert(fields.index("run") + 1, "label")
fields.insert(fields.index("reserved_peak_bytes") + 1, "memory_identical_to_first_run")
fields.insert(fields.index("device_used_bytes") + 1, "device_used_diff_bytes")
fields.append("time_speedup_vs_first_run")
with open(here / "warmup_derived.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows({k: d[k] for k in fields} for d in out)

# Figures use repeat 1; repeat 2 is identical in every memory column (checked in the README)
labels = ["first run", "repeat in same process", "after warmup"]
qids = sorted({r["qid"] for r in out}, key=int)
pick = lambda q, lab: next(d for d in out if d["qid"] == q and d["label"] == lab and d["repeat"] == "1")
series = lambda key, scale: [(lab, [float(pick(q, lab)[key]) / scale for q in qids]) for lab in labels]
cats = [f"Q{q}" for q in qids]
speedups = [d["time_speedup_vs_first_run"] for d in out if d["label"] != "first run"]
fig = here / "figures"
fig.mkdir(exist_ok=True)

svgplot.bar_chart(fig / "1_transient_peak.svg", cats, series("transient_peak_bytes", MB),
                  "Transient peak is identical with or without warmup",
                  "Peak memory outside the pool per query; the three bars of each query are byte-for-byte equal",
                  "Transient peak (MB, 10^6 bytes)", "TPC-H query (SF1, 4 GB pool)", grouped=True)
svgplot.bar_chart(fig / "2_pool_peak.svg", cats, series("pool_peak_bytes", MB),
                  "Pool high-water mark is identical with or without warmup",
                  "Pool high-water mark per query; the three bars of each query are byte-for-byte equal",
                  "Pool high-water mark (MB)", "TPC-H query (SF1, 4 GB pool)", grouped=True)
svgplot.bar_chart(fig / "3_time.svg", cats, series("time_s", 1e-3),
                  f"Time is what warmup changes: first runs are {min(speedups):.1f}-{max(speedups):.1f}x slower",
                  "Query time as measured by TQP (excludes disk reads and output); same data as above",
                  "Query time (ms)", "TPC-H query (SF1, 4 GB pool)", grouped=True)
for svg in fig.glob("*.svg"):
    svgplot.render_png(svg)
print(f"wrote warmup_derived.csv and {len(list(fig.glob('*.svg')))} figures")
