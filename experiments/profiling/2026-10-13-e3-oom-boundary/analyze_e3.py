"""E3 OOM-boundary sweep: <exp dir>/e3_sweep.csv (raw, every attempt) -> e3_sweep_derived.csv,
e3_summary.csv (one row per query) and figures/.
Run: python3 -B 2026-10-13-e3-oom-boundary/analyze_e3.py  (from experiments/profiling/)"""
import csv
import math
import statistics
import sys
from pathlib import Path


DEVICE_BYTES = 16_611_868_672  # RTX 5080 total reported by torch.cuda.mem_get_info()
GB = 1e9

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent))  # svgplot lives in experiments/profiling/
import svgplot  # noqa: E402
raw = list(csv.DictReader(open(here / "e3_sweep.csv")))
num = lambda r, k: int(r[k])

# Derived columns sit right after the raw column they come from
rows, attempts = [], {}
for r in raw:
    key = (int(r["sf"]), int(r["qid"]))
    attempts[key] = attempts.get(key, 0) + 1
    fixed = num(r, "device_used_start_bytes") - num(r, "reserved_start_bytes")
    demand = num(r, "pool_peak_bytes") + num(r, "transient_peak_bytes")
    d = {}
    for k, v in r.items():
        d[k] = v
        if k == "status":
            d["attempt"] = attempts[key]
        elif k == "pool_requested_bytes":
            d["pool_utilization_pct"] = round(100 * num(r, k) / num(r, "pool_peak_bytes"), 1)
            d["pool_frag_bytes"] = num(r, "pool_peak_bytes") - num(r, k)
        elif k == "transient_peak_bytes":
            d["total_demand_bytes"] = demand
        elif k == "device_used_start_bytes":
            d["fixed_overhead_bytes"] = fixed
            d["total_need_bytes"] = demand + fixed
            d["headroom_bytes"] = DEVICE_BYTES - demand - fixed
    rows.append(d)
final = {}
for d in rows:  # the last attempt of each (sf, q) is its result
    final[(int(d["sf"]), int(d["qid"]))] = d
for d in rows:
    d["final"] = final[(int(d["sf"]), int(d["qid"]))] is d
with open(here / "e3_sweep_derived.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

sfs = sorted({sf for sf, _ in final})
qids = sorted({q for _, q in final})
ok = lambda sf, q: (sf, q) in final and final[(sf, q)]["status"] == "ok"
val = lambda sf, q, k: int(final[(sf, q)][k]) if ok(sf, q) else None


def slope(q, key):
    """Least-squares slope of log(value) vs log(SF) over the successful SFs (1.0 = linear growth)."""
    pts = [(math.log(sf), math.log(val(sf, q, key))) for sf in sfs if ok(sf, q)]
    if len(pts) < 2:
        return None
    mx, my = statistics.fmean(x for x, _ in pts), statistics.fmean(y for _, y in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)


def fit(q, key):
    xs = [sf for sf in sfs if ok(sf, q)]
    return statistics.linear_regression(xs, [val(sf, q, key) / GB for sf in xs])


summary = []
for q in qids:
    max_ok = max((sf for sf in sfs if ok(sf, q)), default=None)
    fail_sf = next((sf for sf in sfs if (sf, q) in final and not ok(sf, q)), None)
    # The sweep's verdict comes from all attempts at the failing SF, not just the last one
    seen = {d["status"] for d in rows if (int(d["sf"]), int(d["qid"])) == (fail_sf, q)}
    reason = ("pool + transient exceed GPU" if seen >= {"oom_pool", "oom_cuda"} else
              "transient exceeds GPU" if "oom_cuda" in seen else "pool cannot fit" if seen else "")
    s = {"qid": q, "max_ok_sf": max_ok, "headroom_gb_at_max_ok": round(int(final[(max_ok, q)]["headroom_bytes"]) / GB, 2),
         "fail_sf": fail_sf or "", "fail_reason": reason}
    for sf in sfs:
        need = val(sf, q, "total_need_bytes") if ok(sf, q) else None
        s[f"need_gb_sf{sf}"] = round(need / GB, 3) if need else ""
    for key in ("total_need_bytes", "transient_peak_bytes", "pool_peak_bytes", "pool_requested_bytes"):
        e = slope(q, key)
        s[f"exp_{key.removesuffix('_bytes')}"] = round(e, 2) if e is not None else ""
    b, a = fit(q, "total_need_bytes")
    s.update(need_slope_gb_per_sf=round(b, 3), need_intercept_gb=round(a, 3),
             pool_slope_gb_per_sf=round(fit(q, "pool_peak_bytes").slope, 3),
             transient_slope_gb_per_sf=round(fit(q, "transient_peak_bytes").slope, 3),
             predicted_oom_sf=round((DEVICE_BYTES / GB - a) / b, 3))
    summary.append(s)
ranked = sorted(summary, key=lambda s: s["predicted_oom_sf"])
for rank, s in enumerate(ranked, 1):
    s["pressure_rank"] = rank
with open(here / "e3_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=summary[0].keys())
    w.writeheader()
    w.writerows(summary)

fig = here / "figures"
fig.mkdir(exist_ok=True)
need = lambda sf, q: final[(sf, q)]["total_need_bytes"] / GB if ok(sf, q) else None

# 1. OOM boundary: total need per SF for the 6 queries that need the most at their largest common SF
common = max(sf for sf in sfs if all(ok(sf, q) for q in qids))
top = sorted(qids, key=lambda q: -need(common, q))[:6]
svgplot.line_chart(
    fig / "1_oom_boundary.svg", sfs, [(f"Q{q}", [need(sf, q) for sf in sfs]) for q in top],
    "GPU memory needed vs. scale factor (top 6 queries)",
    "Need = pool high-water mark + transient peak + fixed overhead; a line ends where the query OOMs",
    "GPU memory needed (GB, 10^9 bytes)", "TPC-H scale factor",
    ref=(DEVICE_BYTES / GB, f"GPU capacity {DEVICE_BYTES / GB:.1f} GB"))

# 2. How close each query gets to the GPU limit at the largest SF tested
top_sf = sfs[-1]
svgplot.bar_chart(
    fig / "2_need_at_max_sf.svg", [f"Q{q}" for q in qids], [("Need", [need(top_sf, q) or 0 for q in qids])],
    f"GPU memory needed at SF{top_sf}: only Q{', Q'.join(str(q) for q in qids if not ok(top_sf, q))} does not fit",
    "Bars show the successful runs; labels give the remaining headroom for the tightest queries",
    "GPU memory needed (GB)", "TPC-H query", ref=(DEVICE_BYTES / GB, f"GPU capacity {DEVICE_BYTES / GB:.1f} GB"),
    labels={f"Q{q}": "OOM" if not ok(top_sf, q) else f"{DEVICE_BYTES / GB - need(top_sf, q):.1f} GB left"
            for q in qids if not ok(top_sf, q) or need(top_sf, q) > 9})

# 3. What the need is made of at the largest SF where every query still runs
part = lambda q, k: int(final[(common, q)][k]) / GB
svgplot.bar_chart(
    fig / "3_need_breakdown.svg", [f"Q{q}" for q in qids],
    [("Pool data", [part(q, "pool_requested_bytes") for q in qids]),
     ("Pool fragmentation", [part(q, "pool_frag_bytes") for q in qids]),
     ("Transient peak", [part(q, "transient_peak_bytes") for q in qids]),
     ("Fixed overhead", [part(q, "fixed_overhead_bytes") for q in qids])],
    f"What each query needs at SF{common} (the largest SF where all queries run)",
    "Fixed overhead = device memory outside PyTorch (CUDA context, kernels)",
    "GPU memory (GB)", "TPC-H query", ref=(DEVICE_BYTES / GB, f"GPU capacity {DEVICE_BYTES / GB:.1f} GB"))

# 4. Pool utilization across SF (min / median / max over the queries that ran)
util = lambda sf: [float(final[(sf, q)]["pool_utilization_pct"]) for q in qids if ok(sf, q)]
svgplot.line_chart(
    fig / "4_pool_utilization.svg", sfs,
    [("max", [max(util(sf)) for sf in sfs]), ("median", [statistics.median(util(sf)) for sf in sfs]),
     ("min", [min(util(sf)) for sf in sfs])],
    "Pool utilization rises with scale factor",
    "Utilization = pool data / pool high-water mark, over the queries that ran at each SF",
    "Pool utilization (%)", "TPC-H scale factor")

# 5. How fast the transient peak grows: slope on log-log axes, 1.0 = linear in SF
exps = [s["exp_transient_peak"] or 0 for s in summary]
svgplot.bar_chart(
    fig / "5_transient_growth.svg", [f"Q{q}" for q in qids], [("Growth exponent", exps)],
    f"Transient peak grows linearly with SF for every query (exponent {min(exps):.2f}-{max(exps):.2f})",
    "Slope of log(transient peak) vs log(SF) over successful SFs; above 1.0 = faster than linear",
    "Growth exponent", "TPC-H query", ref=(1.0, "linear (1.0)"),
    labels={f"Q{s['qid']}": f"{s['exp_transient_peak']:.2f}" for s in summary
            if s["exp_transient_peak"] != "" and s["exp_transient_peak"] > 1.1})

# 6. Memory pressure ranked by the predicted OOM scale factor
svgplot.bar_chart(
    fig / "6_pressure_ranking.svg", [f"Q{s['qid']}" for s in ranked],
    [("Transient slope", [s["transient_slope_gb_per_sf"] for s in ranked]),
     ("Pool slope", [s["pool_slope_gb_per_sf"] for s in ranked])],
    "Memory pressure ranking: GB added per +1 SF",
    f"Label = predicted OOM scale factor on a {DEVICE_BYTES / GB:.1f} GB GPU (linear fit over measured SFs)",
    "Memory growth (GB per SF)", "TPC-H query (highest pressure first)",
    labels={f"Q{s['qid']}": f"SF{s['predicted_oom_sf']:.1f}" for s in ranked})

for svg in fig.glob("*.svg"):
    svgplot.render_png(svg)
print(f"wrote e3_sweep_derived.csv, e3_summary.csv and {len(list(fig.glob('*.svg')))} figures")
