# 2026-10-20 S3 实测：读完就还块（方案 A）

## 目的
在与 baseline 相同的条件下，实测 `--free_dead`（按计划删列名 + 按真实地址还块）对 slab 高水位线和两边总需求的效果，并确认结果正确。

## 配置
- RTX 5080 16 GB（dbresearch1），40 MB 块，现在的放置 / 合并规则（first-fit，每 6 次释放合并）。
- SF 5、7、10；池大小 = E3 最后成功的池大小；Q21 SF10 跳过（E3 中无法运行）。
- 每个 (SF, 查询) 两个新进程：参考（不开任何新参数）与释放（`--free_dead --mem_profile --mem_trace`）；两份 `results.out` 按 `test_main.py` 的规则（10 位有效数字）比较。
- **共享 GPU**：另一账号的自动化任务同时在用这张卡（2–15 GB 不等）。经用户同意，每条查询启动前等到空闲显存 ≥ 该查询 baseline 实测的 reserved 峰值 + 0.5 GB 才启动（`mem_sweep.wait_for_headroom`）。显存指标按本进程统计，不受影响；`device_used_*` 列包含别人的占用，不使用。
- SF7 Q21 的释放运行第一次 `oom_cuda`：报错显示另一进程在我们通过检查后又占了 2.73 GiB。单独重跑一次成功，数据在 `trace-rerun/`；`trace/` 中保留原始失败记录，分析时用重跑数据替换这一组。
- 服务器日志：`~/runs/tqp/20261021-000223-s3-free-dead/`、`~/runs/tqp/20261021-001848-s3-rerun-sf7-q21/`。

## 文件
| 文件 | 内容 |
|---|---|
| `measure_s3.py` | 批量测量（服务器上运行） |
| `analyze_s3.py` | 离线分析和画图（在 `experiments/profiling/` 下 `python3 -B 2026-10-20-s3-free-dead/analyze_s3.py`） |
| `trace/` | 原始记录：`queries.csv`（只有释放运行）、`ops.csv`（含 `slab_extent_blocks_end`）、`allocs.csv`、`reads.csv`、`frees.csv`、`oom.csv` |
| `trace-rerun/` | SF7 Q21 的重跑记录 |
| `s3_derived.csv` | `queries.csv` 全部原始列 + 派生列 + 对应的 baseline 列 |
| `figures/` | 两张图 |

## 派生列
"op"指执行器里的一个算子。H 时刻 = 占用范围末端最大的那个算子结束时（释放之前）。

| 列 | 定义 |
|---|---|
| `highwater_real_bytes` | 实测高水位线：max over 算子 `slab_extent_blocks_end` × 40 MB（含空洞） |
| `live_real` | H 时刻仍被占用、且在该算子或之后还会被读的申请的真实字节 |
| `internal_real` | H 时刻已占块容量 − 已占申请的真实字节 |
| `kept_dead_real` | H 时刻仍被占用、但该算子之前就不再被读的申请的真实字节（方案 A 没能及时释放的部分） |
| `kept_mask_real` | `kept_dead_real` 中属于掩码列（编号 ≥ 10000，方案 A 一律不删）的部分 |
| `holes_real` | H 时刻 [0, 高水位) 内的空块 |
| `static_need_real_bytes` | `highwater_real_bytes + transient_peak_bytes`：S3 后按实测高水位开池时的静态切分总需求 |
| `concurrent_need_real` | max over 算子（占用范围末端 + 算子内 PyTorch 峰值） |
| `highwater_bytes`、`highwater_free_bytes`、`static_need_bytes`、`concurrent_need_bytes` | 取自 baseline：H₀、理想回放 H₁、两种总需求 |

## 图
- `figures/1_slab_breakdown`：每个查询一个小图；每个 SF 两根柱，baseline H₀ 与 S3 实测的拆分。
- `figures/2_need_sf10`：SF10 每个查询的 baseline 静态需求、S3 后静态需求、baseline 同一时刻需求、S3 后同一时刻需求，虚线为显卡容量（16.61 GB，假设值）。

## 主要结果
- **正确性**：65 组全部 `results=same`。
- **高水位线**（各 SF 全部查询之和，GB）：

  | | SF5 | SF7 | SF10 |
  |---|---|---|---|
  | H₀ 不释放 | 50.04 | 65.60 | 71.48 |
  | S3 实测 | 29.80 | 41.76 | 48.12 |
  | 理想回放 H₁ | 28.60 | 40.24 | 46.68 |

  实测比不释放降低 33–40%，与理想回放相差 3–5%；42/65 组与回放完全相等。
- **掩码列可以忽略**：`kept_mask_real` 最大 0.06 GB，不需要升级到方案 B。
- **与理想回放的差距主要来自 Spark 的 `Input` 偏宽**：一列会出现在它"流经"的每个算子的 `Input` 里，即使该算子并不读它，方案 A 因此晚释放。最大的是 Q18（SF10 实测 3.40 对回放 2.68 GB，未及时释放 0.62 GB）。
- **两边总需求（SF10，GB）**：Q9 15.31 → 13.03，Q7 9.46 → 6.02，Q10 8.60 → 6.80，Q8 7.40 → 4.84；Q21 SF7 12.63 → 10.31。
- 本实验开着 trace，`time_s` 不用于比较速度。
