# TQP-Inmem
This is an re-implementation of the Microsoft Tensor Query Processing platform ([paper](https://www.vldb.org/pvldb/vol15/p2811-he.pdf)), and a fork of the on-going project to make TQP support out-of-GPU memory queries.

A large part of the original code volume is for out-of-memory processing. This fork removes the out-of-memory processing part to support only in-memory workloads as an experimental baseline.

The structure of the repo is approximately:
```
TQP-Inmem
    ├── IO               : In-memory pipeline and pure-Python pinned/GPU memory pools (block-based allocator on top of torch.empty)
    ├── experiments      : Memory measurements; experiments/profiling has one folder per experiment (scripts, raw CSVs, figures, README)
    ├── operators        : Database operators (Join, Filter, Aggregation, Project, ...) implemented using PyTorch
    ├── queries          : Query files and parsed intermediate forms (from Spark query plan)
    ├── report           : Memory-optimization report (main.tex, main.pdf) and reproduction commands (README.md)
    ├── tests            : Tests for the system
    ├── utility          : Common helpers (logger, tensor utilities, plan-based column lifetimes in lifetime.py)
    ├── config.json      : Points to the TPC-H tensor data directory
    ├── constants.py     : System-wide constants
    ├── conversion.py    : Type conversion helpers (dates, floats, strings)
    ├── expression.py    : SQL expression parser for Spark plan expressions
    ├── main.py          : Main entrypoint for the system
    ├── parsing.py       : Invokes Spark's query plan generator and then parses queries into a format that our system can run
    ├── requirements.txt : Python dependencies
    ├── test_main.py     : End-to-end correctness tests
    └── variable.py      : Variable / tensor-type wrappers
```
## Environment Setup

See [`Setup.md`](./Setup.md) for the conda environment, TPC-H data generation, and Python dependency steps.

Possible follow-up work are tracked in [`Tasks.md`](./Tasks.md).

## Memory Optimization (this branch)

This branch adds operator- and query-agnostic GPU memory management, all behind switches that are off by default; with every switch off the system behaves exactly as before.

| Switch | Effect |
| :--- | :--- |
| `--gpu_pool_gb G` | Size of the GPU slab pool (default 4 GB) |
| `--free_dead` | Return slab memory of columns after their last use in the plan |
| `--slab_eps 0.01` | Plan the slab block size before execution from a 1% waste budget |
| `--coalesce_every` | Merge adjacent free slab blocks on every free |
| `PYTORCH_ALLOC_CONF=expandable_segments:True` | PyTorch expandable segments (older PyTorch: `PYTORCH_CUDA_ALLOC_CONF`) |
| `--mem_profile <csv>`, `--mem_trace <dir>` | Per-query and per-operator memory measurement |

Example: `python3 main.py --SF 10 --q 9 --gpu_pool_gb 6 --free_dead --slab_eps 0.01 --coalesce_every --no_debug`

The report is [`report/main.pdf`](./report/main.pdf); [`report/README.md`](./report/README.md) lists the exact commands behind every table and figure.

## Reproduction: Largest Runnable SF per Query

One command finds, for every query, the largest SF that runs on the current GPU and the memory it needs there. For each SF in `--sf`, `--manage_data` generates the TPC-H tensors with tpch-dbgen-tensors (`--dbgen_dir`, default `~/projects/tpch-dbgen-tensors/build`), runs every query that has not failed yet in a fresh process, and deletes the data again. A query runs at an SF if some slab size lets it finish; the slab size is searched automatically.

Unmodified system:
```
python3 experiments/profiling/mem_sweep.py --sf $(seq 1 15) --manage_data --out runs/max_sf_unmodified.csv
```
All four memory optimizations:
```
python3 experiments/profiling/mem_sweep.py --sf $(seq 1 15) --manage_data --out runs/max_sf_optimized.csv \
    --main_args "--free_dead --slab_eps 0.01 --coalesce_every" --alloc_conf expandable_segments:True
```

When the sweep finishes, it prints the path of the summary (`runs/max_sf_<name>_max_sf.csv`) and the same table:

| Column | Meaning |
| :--- | :--- |
| `qid` | TPC-H query |
| `max_sf` | Largest SF in `--sf` at which the query runs |
| `peak_memory_gb` | Peak GPU memory at that SF: slab high-water mark + PyTorch memory reserved outside the slab (GB = 10^9 bytes; CUDA context not included) |
| `stopped_by` | `OOM at SF n` if the next SF tested fails, `largest SF tested` if the query ran at every SF |

`runs/max_sf_<name>.csv` keeps one row per attempt. The sweep uses the first GPU unless `CUDA_VISIBLE_DEVICES` names another one, and stops if another process is using it; `--quiet_gpu` waits instead until the GPU is idle. Generating SF n needs about 0.92·n GB of disk; SF 1–15 takes about an hour per command on an RTX 5080.

## Running the System

Make sure `config.json`'s `tensors path` points to the tpch-dbgen `data/` directory (see `Setup.md`).

The system is runnable via 

```
python3 main.py --SF {scale_factor} --q {TPC-H query no.}  --warmup_iter {warmup iterations} --time_output_file {directory to file to save runtime measurements} [--no_debug]
```

Also, a basic correctness test is provided via

``python3 main.py --test``

## Evaluation and Results

On a Nvidia A100 - 80GB card, Using the command 
```
python3 main.py --SF 1 --warmup_iter 2 --time_output_file ./time.csv --no_debug
```

We obtain the following results. We also provide a comparison with the original TQP paper's numbers.
| Query | out_system (s) | TQP reported(s) | Speedup (TQP / our_system) |
| :---- | ---------------: | -------: | ----------------------------: |
| Q1    |         0.016011 |    0.026 |                         1.62× |
| Q2    |         0.019191 |    0.028 |                         1.46× |
| Q3    |         0.011614 |    0.024 |                         2.07× |
| Q4    |         0.007643 |    0.018 |                         2.36× |
| Q5    |         0.016295 |    0.042 |                         2.58× |
| Q6    |         0.003077 |    0.002 |                         0.65× |
| Q7    |         0.022092 |    0.035 |                         1.59× |
| Q8    |         0.021916 |    0.039 |                         1.78× |
| Q9    |         0.023115 |    0.092 |                         3.98× |
| Q10   |         0.032032 |    0.052 |                         1.62× |
| Q11   |         0.012422 |    0.009 |                         0.72× |
| Q12   |         0.010219 |    0.021 |                         2.06× |
| Q13   |         0.038741 |    0.136 |                         3.51× |
| Q14   |         0.007072 |    0.005 |                         0.71× |
| Q15   |         0.012898 |      N/A |                             — |
| Q16   |         0.021118 |    0.301 |                        14.26× |
| Q17   |         0.017744 |    0.051 |                         2.87× |
| Q18   |         0.022537 |    0.048 |                         2.13× |
| Q19   |         0.014386 |    0.036 |                         2.50× |
| Q20   |         0.012795 |    0.041 |                         3.20× |
| Q21   |         0.030219 |    0.151 |                         5.00× |
| Q22   |         0.009958 |    0.010 |                         1.00× |


Using
```
python3 main.py --SF 10 --q 1 2 6 9 18  --warmup_iter 2 --time_output_file ./time.csv --no_debug
``` 

We obtain following numbers:

| qid | our system (s) |
| :-: | ---------------: |
|  1  |         0.091427 |
|  2  |         0.034542 |
|  6  |         0.005435 |
|  9  |         0.121054 |
|  18 |         0.078482 |


One note is that we calculate the runtime numbers by subtracting the total runtime by the result output time (writing to screen/file) and then substracting the scan time (scanning from disk -> GPU).
