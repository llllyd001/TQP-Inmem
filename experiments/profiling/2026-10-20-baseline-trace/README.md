# 2026-10-20 Baseline：slab 高水位线分解与两边总需求

## 目的
不改程序行为，记录每条查询的 slab 申请、每个算子读了哪些 slab 块、每个算子的 PyTorch 占用，离线回答：
S2 / S3 / S4 各占 slab 高水位线多少；"读完就还块"能把高水位线降到多少；静态切分比"同一时刻两边之和"多要多少显存。

## 配置
- RTX 5080 16 GB（dbresearch1），40 MB 块，每条查询一个新进程，不做 warmup。
- SF 5、7、10；池大小 = E3 中该 (SF, 查询) 最后成功的池大小，保证两边都不 OOM。Q21 SF10 在 E3 中无法运行，跳过。
- 记录全部在 CPU 端，查询结束时写盘。开 / 关记录时查询级指标与 `results.out` 完全相同（SF1 Q2/Q6/Q9 验证），本实验的 `pool_peak_bytes`、`pool_requested_bytes`、`pool_allocs`、`transient_peak_bytes` 与 E3 完全一致。
- 服务器日志：`~/runs/tqp/20261020-221724-baseline-trace/`；原始记录：`~/runs/tqp/baseline-trace-data/`。

## 文件
| 文件 | 内容 |
|---|---|
| `measure_baseline.py` | 批量测量（服务器上运行） |
| `analyze_baseline.py` | 离线分析和画图（本地运行：在 `experiments/profiling/` 下 `python3 -B 2026-10-20-baseline-trace/analyze_baseline.py`） |
| `trace/queries.csv` | 查询级原始记录（`mem_profiler.py` 的 `_COLUMNS`） |
| `trace/ops.csv`、`allocs.csv`、`reads.csv`、`oom.csv` | 算子级原始记录（`_TRACE_COLUMNS`；`oom.csv` 为空，本次无失败） |
| `baseline_derived.csv` | `queries.csv` 全部原始列 + 派生列 |
| `figures/` | 两张图 |

## 派生列
"op"指执行器里的一个算子；"最后一次读"按算子粒度：读它的算子整个结束才算死（偏保守）。读取按真实地址判断落在哪几块，切片 / 别名自动算同一块。

| 列 | 定义 |
|---|---|
| `highwater_bytes`（H₀） | 所有申请块数 × 40 MB；必须等于 `pool_peak_bytes`（脚本检查） |
| `live_at_h0` | H₀ 时刻（最后一次申请所在算子）之后还会被读的数据真实字节 |
| `internal_at_h0`（S2） | 所有已占块容量 − 其中的真实字节 |
| `dead_at_h0`（S3） | H₀ 时刻已不会再被读、但仍占着块的数据真实字节 |
| `live_peak_bytes` | 每个算子时刻存活数据真实字节的最大值；任何存储方式都降不到它以下 |
| `highwater_free_bytes`（H₁） | 按 `IO/pinned_mem.py` 的规则回放"最后一次读完就还块"后的高水位线 |
| `live_at_h1`、`internal_at_h1` | H₁ 时刻的存活数据、内部碎片 |
| `holes_at_h1`（S4） | H₁ 时刻 [0, H₁) 内的空块 |
| `slab_side_peak`、`torch_side_peak` | 即 `pool_peak_bytes`、`transient_peak_bytes` |
| `static_need_bytes` | 静态切分下的总需求 = 两者之和 |
| `concurrent_need_bytes` | max over 算子（算子结束时 slab 已用 + 算子内 PyTorch 峰值）；偏保守 |
| `concurrent_need_free_bytes` | 同上，slab 一侧换成回放"读完就还块"后的占用范围 |
| `cached_unused_peak_bytes` | max over 算子（`reserved_peak` − `allocated_peak`）：PyTorch 缓存着但没在用的显存 |

H₀ = live + S2 + S3；H₁ = live + 内部碎片 + S4（每行都成立）。注意 H₀ 和 H₁ 的拆分取自不同时刻：H₀ 在查询末尾，那时大部分数据已死；H₁ 在回放中占用范围最大的时刻，那时大部分数据还活着。

## 图
- `figures/1_slab_breakdown`：每个查询一个小图；每个 SF 两根柱，H₀ 与 H₁ 的拆分。
- `figures/2_static_vs_concurrent`：SF10 下每个查询的三种总需求，虚线为显卡容量（16.61 GB，假设值）。

## 主要结果（SF10，Q21 为 SF7）
- 高水位线里已死数据（S3）是最大的一块：中位数 2.26 GB，最大 5.43 GB（Q7）；S2 中位数 0.66 GB，最大 3.02 GB（Q2）。
- 回放"读完就还块"后 H₁ 中位数从 3.04 降到 2.36 GB；但 S4 空洞随之出现，中位数 0.24 GB，Q19 达 2.12 GB，几乎抵消了它的收益（H₀ 4.68 → H₁ 4.64 GB）。
- Q9：静态切分 15.31 GB，同一时刻 14.63 GB，再加读完就还块 10.59 GB。Q21 SF7：12.63 → 10.31 → 10.31 GB。
- Q1、Q4、Q6、Q12、Q13、Q14、Q19、Q22 加上读完就还块后，同一时刻总需求不变：总需求最高的那个算子时刻，slab 里还没有可以释放的数据。
