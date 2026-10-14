"""Does warmup change query-level memory numbers? One process per call, SF1, 4 GB pool, 40 MB blocks.

Run on the server from the repo root, passing the text of this file to python -c (files here are not synced):
  run_server.sh --gpu -- python -c <file text> <qid> cold|warm <repeat>
(run_server.sh pastes the command into a heredoc, so the code must not contain dollar signs, backticks or single quotes.)
cold: the query runs twice in a fresh process (run 1 = first ever, run 2 = repeat).
warm: the warmup() of TQP (all 22 SF1 queries) first, then the query once.
Prints CSV rows prefixed with "CSV,".
"""
import os
import sys
import torch
import main
from IO.pinned_mem import GPUMemory

qid, condition, repeat = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
pool = main.gpu_memory_pool = GPUMemory(4, 40)
slab = pool.pool_size_bytes
time_file = os.path.expanduser(f"~/runs/tqp/.time-{os.getpid()}.csv")
recording, rows = False, []
orig_free_all = pool.free_all


def free_all():
    # Called by main.exec once per query, right before the pool is emptied
    torch.cuda.synchronize()
    if recording:
        s = pool.stats()
        free, total = torch.cuda.mem_get_info()
        rows.append([s["peak_bytes"], s["requested_bytes"], s["num_allocs"],
                     torch.cuda.max_memory_allocated() - slab, torch.cuda.max_memory_reserved(), total - free])
    orig_free_all()
    pool.reset_peak()
    pool._num_allocs = 0
    torch.cuda.reset_peak_memory_stats()


pool.free_all = free_all
with torch.inference_mode():
    if condition == "warm":
        main.warmup(4)
    recording = True
    torch.cuda.reset_peak_memory_stats()
    main.exec(1, [qid] * (2 if condition == "cold" else 1), None, None, True, None, None, time_file, gpu_pool_gb=4)

# main.exec appends one "SF,qid,time_elapsed" line per query; the timer excludes disk reads and output
with open(time_file) as f:
    times = [line.strip().split(",")[2] for line in f.readlines()[1:]]
os.remove(time_file)

print("CSV,qid,condition,repeat,run,pool_peak_bytes,pool_requested_bytes,pool_allocs,"
      "transient_peak_bytes,reserved_peak_bytes,device_used_bytes,time_s")
for run, (r, t) in enumerate(zip(rows, times), 1):
    print("CSV," + ",".join(map(str, [qid, condition, repeat, run, *r, t])))
