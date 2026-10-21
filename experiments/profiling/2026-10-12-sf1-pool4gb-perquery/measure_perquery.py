# Query-level memory of all 22 SF1 queries in one process (experiment 2026-10-12-sf1-pool4gb-perquery).
# Run from the repo root by passing the text of this file to python -c:
#   python -c "$(cat experiments/profiling/2026-10-12-sf1-pool4gb-perquery/measure_perquery.py)"
# Prints CSV rows prefixed with "CSV,".
import torch, main
from IO.pinned_mem import GPUMemory

# Pre-create the 4 GB pool and record stats right before each per-query free_all()
pool = main.gpu_memory_pool = GPUMemory(4, 40)
slab = pool.pool_size_bytes
qids = list(range(1, 23))
pending = iter(qids)
last_allocs = 0
rows = []
orig_free_all = pool.free_all

def free_all():
    global last_allocs
    torch.cuda.synchronize()
    s = pool.stats()
    rows.append((next(pending), s["peak_bytes"], s["requested_bytes"], s["num_allocs"] - last_allocs,
                 torch.cuda.max_memory_allocated() - slab, torch.cuda.max_memory_reserved()))
    last_allocs = s["num_allocs"]
    orig_free_all()
    pool.reset_peak()
    torch.cuda.reset_peak_memory_stats()

pool.free_all = free_all
torch.cuda.reset_peak_memory_stats()
with torch.inference_mode():
    main.exec(1, qids, None, None, True, None, None, None, gpu_pool_gb=4)

free, total = torch.cuda.mem_get_info()
print("ENV", torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0), "device_used_end", total - free, "total", total)
print("CSV,qid,pool_peak_bytes,pool_requested_bytes,pool_allocs,transient_peak_bytes,reserved_peak_bytes")
for r in rows:
    print("CSV," + ",".join(map(str, r)))
