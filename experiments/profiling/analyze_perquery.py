"""<exp dir>/per_query.csv (raw) -> per_query_derived.csv + figures/.
Run from the repo root: python3 -B experiments/profiling/analyze_perquery.py 'Other materials/experiments/2026-10-12-sf1-pool4gb-perquery'"""
import csv
import statistics
import sys
from pathlib import Path

import svgplot

here = Path(sys.argv[1]).resolve()

BLOCK = 40 * 10**6
MB = 10**6
rows = list(csv.DictReader(open(here / "per_query.csv")))

# Derived columns sit right after the raw column they are computed from
out = []
for r in rows:
    peak, req, allocs = int(r["pool_peak_bytes"]), int(r["pool_requested_bytes"]), int(r["pool_allocs"])
    trans = int(r["transient_peak_bytes"])
    out.append({
        "qid": r["qid"],
        "pool_peak_bytes": peak,
        "pool_peak_blocks": peak // BLOCK,
        "pool_requested_bytes": req,
        "pool_utilization_pct": round(100 * req / peak, 1),
        "pool_internal_frag_bytes": peak - req,
        "pool_allocs": allocs,
        "pool_extra_blocks": peak // BLOCK - allocs,
        "avg_bytes_per_alloc": req // allocs,
        "transient_peak_bytes": trans,
        "transient_over_pool_data": round(trans / req, 2),
        "total_demand_bytes": peak + trans,
        "reserved_peak_bytes": int(r["reserved_peak_bytes"]),
    })
with open(here / "per_query_derived.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=out[0].keys())
    w.writeheader()
    w.writerows(out)

fig = here / "figures"
fig.mkdir(exist_ok=True)
qs = [f"Q{r['qid']}" for r in out]
mb = lambda key: [r[key] / MB for r in out]

# 1. What one query needs on the GPU: pool data + pool fragmentation + transient
svgplot.bar_chart(
    fig / "1_demand_breakdown.svg", qs,
    [("Pool data", mb("pool_requested_bytes")), ("Pool fragmentation", mb("pool_internal_frag_bytes")),
     ("Transient peak", mb("transient_peak_bytes"))],
    "GPU memory demand per query (SF1, 4 GB pool, 40 MB blocks)",
    "Bar height = pool high-water mark + transient peak; orange is space wasted by 40 MB blocks",
    "Memory (MB, 10^6 bytes)", "TPC-H query",
    ref=(4000, "GPU pool capacity 4 GB"),
    labels={q: f"{r['total_demand_bytes'] / 10**9:.2f} GB" for q, r in zip(qs, out)
            if r["total_demand_bytes"] > 3 * 10**9})

# 2. How much of the borrowed pool space is real data
util = [r["pool_utilization_pct"] for r in out]
med = statistics.median(100 * r["pool_requested_bytes"] / r["pool_peak_bytes"] for r in out)
svgplot.bar_chart(
    fig / "2_pool_utilization.svg", qs, [("Pool utilization", util)],
    "Pool utilization = pool data / pool high-water mark",
    f"Median {med:.1f}%: about 80% of the borrowed pool space holds no data",
    "Utilization (%)", "TPC-H query", ref=(med, f"Median {med:.1f}%"),
    labels={q: f"{u:.1f}%" for q, u in zip(qs, util) if u < 6 or u > 45})

# 3. Pool size is set by the number of allocations, not by data volume
svgplot.scatter(
    fig / "3_blocks_vs_allocs.svg",
    [(r["pool_allocs"], r["pool_peak_blocks"], q if r["pool_allocs"] > 60 else None) for q, r in zip(qs, out)],
    "Pool high-water blocks vs. number of allocations",
    f"Points lie almost on y = x (correlation "
    f"{statistics.correlation([r['pool_allocs'] for r in out], [r['pool_peak_blocks'] for r in out]):.3f}"
    "): nearly every allocation takes exactly one block",
    "Pool high-water mark (blocks, 40 MB each)", "Number of allocations", diagonal="y = x (one block per allocation)")

for svg in fig.glob("*.svg"):
    svgplot.render_png(svg)
print(f"wrote per_query_derived.csv and {len(list(fig.glob('*.svg')))} figures")
