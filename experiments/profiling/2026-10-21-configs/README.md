# 2026-10-21 配置对比：S3 + S2 / S4 组合与 P1

## 目的
在相同条件下实测 slab 侧组合（S3+S2、S3+S4、S3+S2+S4）和 PyTorch 侧（P1：expandable segments；对照：每个算子后 `empty_cache`）的显存效果，以及 S3+P1、全部组合的设备总需求；同时检查结果是否与参考运行一致。

## 配置
- RTX 5080，SF 5、7、10；池大小 = E3 最后成功的池大小；Q21 SF10 跳过（baseline 无法运行）。
- 每个 (SF, 查询)：先跑一次参考（不开任何新参数），再依次跑下列配置，每个都开 `--mem_profile --mem_trace`：

  | 配置 | 参数 |
  |---|---|
  | `s3_s2` | `--free_dead --slab_eps 0.01` |
  | `s3_s4` | `--free_dead --coalesce_every` |
  | `s3_s2_s4` | `--free_dead --slab_eps 0.01 --coalesce_every` |
  | `p1` | `PYTORCH_ALLOC_CONF=expandable_segments:True` |
  | `p1_empty` | `--empty_cache_per_op` |
  | `s3_p1` | `--free_dead` + expandable segments |
  | `s3_s2_s4_p1` | 全部 |

- 共享 GPU：每次启动前等到空闲显存 ≥ 该查询 baseline 的 reserved 峰值 + 0.5 GB。显存指标按本进程统计；`device_used_*` 含别人的占用，不使用。
- 结果按 `test_main.py` 的规则（10 位有效数字）与参考运行比较。
- 服务器日志：`~/runs/tqp/*-configs/`；结果文件 `~/runs/tqp/configs-data/results/`（`.out` 不进仓库）。

## 文件
| 文件 | 内容 |
|---|---|
| `measure_configs.py` | 测量脚本 |
| `trace/<配置>/` | 原始记录（同 S3 实验：queries、ops、allocs、reads、frees、oom） |
| `runs.txt` | 每次运行的状态与结果比较 |

派生数据由 `report/make_data.py` 生成到 `report/plotdata/`（slab 高水位、缓存未用、设备总需求、正确性统计）。

## 主要结果
- slab 高水位合计（GB）：

  | SF | baseline | S3 | S3+S2 | S3+S4 | S3+S2+S4 |
  |---|---|---|---|---|---|
  | 5 | 50.04 | 29.80 | 22.61 | 26.44 | 21.71（−57%） |
  | 7 | 65.60 | 41.76 | 31.62 | 37.72 | 30.36（−54%） |
  | 10 | 71.48 | 48.12 | 42.03 | 44.36 | 40.71（−43%） |

  与离线回放一致（SF10 S3+S2：实测 42.03，回放 42.027；S3+S2+S4：40.709 对 40.709）。
- P1（SF10，21 条查询）：PyTorch 缓存着但没在用的显存合计 7.67 → 1.93 GB（expandable segments），`empty_cache` 对照为 5.70 GB。Q10 3.23 → 0.41 GB；Q1 从 0.01 增至 0.48 GB。
- 设备总需求合计（SF10）：baseline 129.9 → S3 106.5 → S3+P1 100.7 → 全部 93.3 GB（−28%）。
- 正确性：每个配置 65 次运行中 64 次与参考一致；不一致的是 SF10 Q16（7 个配置彼此一致，只有参考运行不同）和 SF5 Q21 的 `p1_empty`。原因见下一节。

## 关于不一致的结果
TQP 的哈希聚合（`operators/aggregate.py:320-325`）每次运行随机生成哈希系数，并且只按哈希值区分组；两个组哈希相同就会被合并，计数出错，出错与否每次运行都可能不同。SF10 Q16 的参考运行里 Brand#59 一组为 72（其余 7 次均为 64）。这是原系统的行为，与内存优化无关；用未修改的程序重复运行的确认实验见 `../2026-10-21-determinism/`。
