# 2026-10-21 最大可运行 SF：baseline 与组合配置

## 目的
测量每条查询在 16 GB 卡上能跑通的最大 SF，比较未修改系统（baseline）与组合配置（S3 + S2 + S4 + P1）。

## 方法
- 复用 `experiments/profiling/mem_sweep.py` 的自适应池搜索（与 E3 相同）：一条查询在某个 SF 下，只要存在一个 slab 大小能让它跑通就算"能跑"；SF 从小到大，一条查询失败后不再测更大的 SF。
- baseline：不加参数。组合：`--main_args "--free_dead --slab_eps 0.01 --coalesce_every" --alloc_conf expandable_segments:True`。
- SF 10、15、20（SF20 数据 18.4 GB，是磁盘限额允许的最大值）；之后对 Q9、Q21 在 SF 11–14 加测（`fine-*.csv`）。
- `--quiet_gpu`：每次启动前要求整卡已用显存 ≤ 0.5 GB，避免别人的任务造成假的 OOM。
- 服务器日志：`~/runs/tqp/20261021-013929-maxsf-baseline/`、`~/runs/tqp/*-maxsf-combined/`。

## 文件
| 文件 | 内容 |
|---|---|
| `baseline.csv`、`combined.csv` | 每次尝试一行（列同 `mem_profiler.py` 的 `_COLUMNS`），SF 10/15/20 |
| `fine-baseline.csv`、`fine-combined.csv` | Q9、Q21 在 SF 11–14 的尝试 |

## 主要结果（SF 10/15/20）
| 查询 | baseline | 组合 |
|---|---|---|
| Q21 | SF10 也 OOM | SF10（SF15 OOM） |
| Q9 | SF10（SF15 OOM） | SF10（SF15 OOM） |
| Q7 | SF15（SF20 池超过 14 GB 上限） | ≥ SF20 |
| 其余 19 条 | ≥ SF20 | ≥ SF20 |

组合配置下 22 条查询都能跑 SF10。
