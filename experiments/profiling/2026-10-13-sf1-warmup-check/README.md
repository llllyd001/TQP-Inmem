# 2026-10-13 · SF1 · warmup 会不会影响查询级显存测量

## 目的
判断查询级显存测量需不需要先做 warmup。

- **假设：** warmup 只影响时间，不影响显存。理由是张量需要多少字节，和是不是第一次运行无关。
- **判定标准：** 三种条件下，池高水位、真实数据量、借用次数、池外临时峰值和 reserved 峰值都完全相同（逐字节相等），就说明测显存不需要 warmup。时间应该明显不同，作为对照。

## 环境与代码
- dbresearch1 · NVIDIA GeForce RTX 5080（16.6 GB）· torch 2.14.0+cu130 · driver 590.48
- 代码：`memory-optimization` @ `4f9338b`，仓库代码没有未提交的改动
- 数据：TPC-H SF1；GPU 池 4 GB，块大小 40 MB
- 运行时没有加 `--log`，原始输出完整保存在本目录的 `stdout.log`，服务器上没有留下文件

## 方法
- 每次运行都是一个**独立的新进程**，用 `experiments/profiling/measure_warmup.py` 测量。
- 查询：Q1（大聚合）、Q2（池高水位最高）、Q9、Q21（池外临时峰值最高的两条）。
- 每条查询测三种条件：

| 条件（label） | 做法 |
| --- | --- |
| `first run` | 新进程里第一次运行这条查询 |
| `repeat in same process` | 同一个进程里紧接着再运行一次 |
| `after warmup` | 另起一个新进程，先运行 TQP 自带的 `warmup()`（SF1 的 22 条查询全部跑一遍），再运行这条查询 |

- 整个实验完整重复 2 次（`repeat` = 1、2），一共 16 个进程，得到 24 行数据。
- 显存的记录方式和 2026-10-12 那次相同：在 `free_all()` 之前读数，然后清零。时间用 TQP 自己的计时，不含读盘和输出。

## 文件
| 文件 | 内容 |
| --- | --- |
| `stdout.log` | 16 个进程的完整输出 |
| `warmup.csv` | 原始数据，从 `stdout.log` 里提取，单位是字节和秒 |
| `warmup_derived.csv` | 全部原始列，加上派生列 |
| `figures/` | 3 张图，PNG 和 SVG 各一份 |

代码在仓库的 `experiments/profiling/`：测量用 `measure_warmup.py`，分析和画图用 `analyze_warmup.py`。

### 派生列定义
| 派生列 | 放在哪一列后面 | 公式 | 含义 |
| --- | --- | --- | --- |
| `label` | `run` | 由 condition 和 run 组合得到 | 这一行属于三种条件中的哪一种 |
| `memory_identical_to_first_run` | `reserved_peak_bytes` | 5 个显存列是否都和同一查询、同一 repeat 的 first run 相等 | True 表示逐字节相同 |
| `device_used_diff_bytes` | `device_used_bytes` | 本行的设备已用量 − first run 的设备已用量 | 整张卡多用了多少显存，包括 PyTorch 之外的部分 |
| `time_speedup_vs_first_run` | `time_s`（最后一列） | first run 的时间 ÷ 本行的时间 | 比第一次运行快了多少倍 |

## 结果
| 指标 | 结果 |
| --- | --- |
| 5 个显存列（池高水位、真实数据、借用次数、临时峰值、reserved 峰值） | **24 行全部和对应的 first run 逐字节相同**；两次 repeat 之间也完全相同 |
| 设备已用量 | `repeat in same process`：+0 字节；`after warmup`：每条查询都正好多 **35,651,584 字节（34 MiB）** |
| 时间 | first run 是 255–316 ms；其余两种条件是 12–35 ms，快了 **7.5–22.5 倍** |
| 和 2026-10-12 的同进程实验对比 | 4 条查询的 5 个显存列，和那次 `per_query.csv` 完全一样 |

## 图表
**图 1 Transient peak**（`figures/1_transient_peak.png`）
- 怎么读：每条查询有三根柱子，分别是三种条件下的池外临时峰值。
- 看到什么：每组三根柱子一样高（逐字节相等），warmup 不改变临时峰值。

**图 2 Pool high-water mark**（`figures/2_pool_peak.png`）
- 怎么读：同上，只是指标换成池高水位。
- 看到什么：三根柱子一样高，warmup 也不改变池高水位。

**图 3 Query time**（`figures/3_time.png`）
- 怎么读：同样的三种条件，指标换成查询时间。
- 看到什么：只有 first run 很高，其余两种条件都低得多。warmup 改变的是时间。

## 观察（已测到的）
1. **查询级显存测量不需要 warmup。** 第一次运行、同进程内重复运行、warmup 之后运行，5 个显存指标都逐字节相同，两次完整重复也相同。
2. **warmup 只给整张卡多加了一个固定的 34 MiB。** 这部分在 PyTorch 统计之外（allocated 和 reserved 都没变），每条查询都一样大，和查询本身无关。
3. **时间需要 warmup。** 第一次运行慢 7.5–22.5 倍，所以测性能时必须先 warmup，或者丢掉第一次的结果。
4. **10-12 那次同进程实验的数据可信。** 这 4 条查询在独立进程里测得的数字和那次完全一样，说明同一个进程里前面的查询没有给它们留下影响。

## 推测（还没验证）
- 那多出来的 34 MiB 很可能是 warmup 运行其他查询时，CUDA 加载了更多 kernel（GPU 程序），扩大了 CUDA context。依据是：它不在 PyTorch 的统计里，而且大小固定。
- 第一次运行慢的原因可能包括：kernel 首次加载、`torch.jit.load` 或计划解析的首次开销、缓存分配器首次向显卡申请显存。本实验没有区分这几项。

## 结论与规则
- **测显存：** 用 `--warmup_iter 0`，一条查询一个新进程。这样既得到正确的数字，又避免 warmup 带来的历史影响。在 16 GB 的显存预算里，把 CUDA context 等固定开销单独记一项。
- **测时间：** 关闭测量工具，先 warmup，或者丢掉第一次运行；每个配置重复 3–5 次，报告中位数。

## 延伸：下一步问题与实验
| 结论 / 推测 | 新问题 | 对应实验 | 在 schedule 中的位置 |
| --- | --- | --- | --- |
| 测显存不需要 warmup | 正式的查询级工具可以直接按"一条查询一个进程、不做 warmup"来设计 | 查询级测量工具和批量运行脚本 | B1 |
| CUDA context 等固定开销是 OOM 预算的一部分 | 16 GB 的卡上，固定开销一共多大（context、kernel、库的工作空间）？SF 变大时会变吗？ | 规模扩展实验里，记录查询开始前的设备已用量 | B2 / E3 |
| 第一次运行慢 7.5–22.5 倍 | 慢在哪里？会不会影响性能对比的结论？ | 做 E 阶段性能评估时，用 PyTorch profiler 拆开第一次运行的时间 | E1（可选） |

## 局限
- 只测了 4 条查询、SF1、4 GB 池。显存逐字节相同的结论，原理上和规模无关，但更大的 SF 还没有测过。
- 只测了查询级。阶段级测量需要更多同步，等工具写好后，再确认一次 warmup 对它也没有影响。
- 时间只是作为对照，每种条件只测了 2 次，不能当作性能结果。
