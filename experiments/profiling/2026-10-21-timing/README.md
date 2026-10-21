# 2026-10-21 运行时间：各优化配置相对 baseline

## 目的
测量 S3、S2、S4、P1 及其组合对查询运行时间的影响（report 的 Trade-offs）。

## 配置
- RTX 5080，SF10，21 条查询（Q21 在 SF10 无法运行）。池大小 = E3 最后成功的池大小。
- 配置：`baseline`、`s3`（`--free_dead`）、`s3_s2_s4`（`--free_dead --slab_eps 0.01 --coalesce_every`）、`p1`（`PYTORCH_ALLOC_CONF=expandable_segments:True`）、`s3_p1`、`s3_s2_s4_p1`。不开任何测量工具。
- 每个 (配置, 查询) 一个新进程，同一进程里连跑该查询 4 次（`--q Q Q Q Q`），第 1 次作为 warmup 丢弃，记录后 3 次。时间为 `main.py` 的 `time_elapsed`（已扣除读盘和输出时间）。
- 每个进程启动前要求整卡已用显存 ≤ 0.5 GB（`mem_sweep.wait_for_quiet_gpu`），避免与别人的任务争抢。
- `s3_s2_s4` 与 `s3_s2_s4_p1` 在约前 6 条查询使用了修正前的 N（块大小略大）。块大小影响 slab 内部碎片（显存数据来自 N 修正后的 `2026-10-21-configs`，不受影响）和 slab 记账开销（微秒级，查询为 7–248 ms），不改变查询结果和 GPU 计算，因此不影响本实验的时间结论。
- 服务器日志：`~/runs/tqp/20261021-011803-timing/`。

## 文件
| 文件 | 内容 |
|---|---|
| `measure_timing.py` | 测量脚本 |
| `timing.csv` | 原始记录：sf, qid, config, rep, time_s |

派生数据（每个配置的中位数、相对 baseline 的比值、几何平均）由 `report/make_data.py` 生成到 `report/plotdata/timing_*.csv`。

## 主要结果
| 配置 | 相对 baseline，几何平均 | 最大 |
|---|---|---|
| s3 | 1.009 | 1.040 |
| p1 | 1.012 | 1.055 |
| s3_p1 | 1.023 | 1.065 |
| s3_s2_s4 | 1.016 | 1.065 |
| s3_s2_s4_p1 | 1.029 | 1.157（Q3：30 → 35 ms） |

- 查询时间 7–248 ms；3 次重复的变异系数中位数 0.4%。
