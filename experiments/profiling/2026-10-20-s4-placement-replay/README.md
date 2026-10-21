# 2026-10-20 S4：slab 放置与合并时机离线回放

## 目的
使用 baseline 原始记录，理想化地在申请最后一次被读的算子结束后归还全部块，比较不同放置规则和合并时机的最小池容量。只分析 slab，不包含 PyTorch 侧显存，也不修改执行器。

## 配置与运行
- 输入为相邻 baseline 实验的 `trace/queries.csv`、`ops.csv`、`allocs.csv`、`reads.csv`。
- 共 65 条记录：SF5、SF7 各 22 条，SF10 为 21 条；SF10 Q21 无记录，不补零、不替用 SF7。
- 块大小 $B = 40{,}000{,}000$ 字节；十进制 $1\,\mathrm{GB} = 10^9$ 字节
- 在 `experiments/profiling/` 下运行 `python3 -B 2026-10-20-s4-placement-replay/analyze_s4.py`。
- 数据分析仅用标准库，无 torch / GPU；SVG 使用 `svgplot.py`，PNG 使用其 `render_png`（本地 Chrome）。
- 本次沙箱禁止 Chrome 所需的 socket 操作，常规命令在 PNG 阶段退出；CSV、SVG、README 已先写出。
  本次 PNG 通过临时运行包装器调用本机已有的 librsvg/Cairo 生成；未安装包、未向脚本加入该依赖。

## 方法
复用 `analyze_baseline.py` 的 `load_traces`、`allocation_lifetimes` 和 `Pool`。
保持算子原始行顺序；同一算子内按原始块起点、块数、请求字节排序申请。只考虑 `in_slab` 读取，以块范围重叠判断生命周期。
同一算子先完成全部申请，再按原申请顺序释放到期申请；未被读取的申请在其申请算子结束后释放。生命周期始终来自原始地址，不使用回放后的地址重新匹配读取。

| 放置规则 | 定义 |
|---|---|
| `first` | 按空闲列表顺序找到第一个足够大的段，取低端；列表未必按地址排序 |
| `best` | 在所有足够大的段中取最短者，并列取最低地址，取段低端 |
| `lifetime-t` | 当 $\operatorname{last\_use}(a)-i_a\le t$ 时，按地址从高到低找第一个足够大的段并取高端；否则沿用 `first`，取段低端；$t\in\{0,1,2\}$ |
| `lazy` | 每次释放追加到空闲列表尾部，第六次释放时排序并合并相邻段，计数清零 |
| `every` | 每次释放都排序并合并相邻段 |

先确认 H₀ 容量能成功，再在下限与 H₀ 之间二分。由于放置轨迹可能随容量改变，不能假设成功性单调；额外逐块检查下限到二分候选之间的容量，找到全局最小值。最后再次回放该容量及少一块的容量，分别确认成功和失败。
H₀ 仍失败时 CSV 的容量与时间均留空；空值不作为零计入汇总；合计显示 NA，中位数和最大耗时仅统计成功项；图中省略不完整系列。

## 指标定义
| 指标 | 定义 |
|---|---|
| 申请算子序号 | $i_a$，按原始算子顺序从零编号 |
| `last_use` | $\operatorname{last\_use}(a)=\max\{j: \text{算子 }j\text{ 的 slab 读取与申请 }a\text{ 的块范围重叠}\}$；无重叠读取时取 $i_a$ |
| `highwater_bytes` | $H_0=B\sum_a n_a$，其中 $n_a$ 为申请块数；检查与 baseline 一致 |
| `floor_blocks` | $\max_j\sum_{a:i_a\le j\le\operatorname{last\_use}(a)}n_a$，算子内申请完成、释放之前的最大占块数 |
| `floor_bytes` | $B\cdot\mathrm{floor\_blocks}$；包含块内碎片，与真实请求字节峰值不同 |
| `min_pool_blocks` | $\min\{C\in\mathbb{Z}: \text{容量为 }C\text{ 块时回放成功}\}$ |
| `<放置>_<合并>_pool_bytes` | $B\cdot\mathrm{min\_pool\_blocks}$ |
| `<放置>_<合并>_merge_us` | 最小容量确认回放中，所有排序与合并的 `time.perf_counter` 时间之和，单位 $\mu\mathrm{s}$；不累计搜索回放耗时 |

合并时间包含查询末尾的释放，计时不含释放追加、放置扫描及生命周期计算。它是本机 Python 单次运行的微秒级估计，有计时器和调度噪声，不能当作 GPU 查询加速比。

## 文件与图
| 文件 | 内容 |
|---|---|
| `analyze_s4.py` | 回放、全局最小容量验证、CSV、绘图及本说明生成 |
| `s4_replay.csv` | 每个 SF / 查询一行；四列参照指标及十组容量、合并时间 |
| `figures/1_pool_need_sf10.svg`、`.png` | SF10 七个系列；lifetime 阈值按 SF10 的 every 合计选择，并列选较小阈值 |
| `figures/2_summary.svg`、`.png` | 按 SF 分组，十种组合及 floor 合计；合计不是同时运行所有查询的容量 |

![SF10 pool need](figures/1_pool_need_sf10.svg)

![Summary](figures/2_summary.svg)

## 主要结果
- SF5：H₀ 合计 50.04 GB；`first_lazy` 26.20 GB；最小合计 25.48 GB（`lifetime-1_every`）；floor 合计 25.48 GB。
- SF7：H₀ 合计 65.60 GB；`first_lazy` 37.64 GB；最小合计 36.40 GB（`lifetime-1_every`、`lifetime-2_every`）；floor 合计 36.24 GB。
- SF10：H₀ 合计 71.48 GB；`first_lazy` 43.84 GB；最小合计 42.68 GB（`lifetime-2_every`）；floor 合计 42.56 GB。
- SF10 图选用 `lifetime-2_every`。
- 全局检查修正了 0 个二分候选；成功的 650 组均完成最小值与少一块验证。
- 所有组合不低于 floor；`first_lazy` 不超过 baseline 的 `highwater_free_bytes`。

## 汇总
合计与中位数单位 GB；最大合并耗时为该 SF 内单条查询的最大值。

```text
SF  Rule                 Sum_GB  Median_GB  Max_merge_us  Success
5   H0                    50.040      2.140             -  22/22
5   floor                 25.480      1.120             -  22/22
5   first_lazy            26.200      1.180        27.064  22/22
5   first_every           25.560      1.140        98.486  22/22
5   best_lazy             26.160      1.160        28.376  22/22
5   best_every            25.640      1.140        66.027  22/22
5   lifetime-0_lazy       26.200      1.180        22.324  22/22
5   lifetime-0_every      25.560      1.140        54.381  22/22
5   lifetime-1_lazy       26.120      1.180        21.599  22/22
5   lifetime-1_every      25.480      1.120        68.760  22/22
5   lifetime-2_lazy       26.280      1.220        16.881  22/22
5   lifetime-2_every      25.520      1.140        53.893  22/22
7   H0                    65.600      2.760             -  22/22
7   floor                 36.240      1.620             -  22/22
7   first_lazy            37.640      1.740        20.733  22/22
7   first_every           36.600      1.660       198.892  22/22
7   best_lazy             37.320      1.720        28.322  22/22
7   best_every            36.600      1.660        86.027  22/22
7   lifetime-0_lazy       37.640      1.740        19.946  22/22
7   lifetime-0_every      36.480      1.640       101.253  22/22
7   lifetime-1_lazy       37.440      1.720        26.137  22/22
7   lifetime-1_every      36.400      1.640        74.059  22/22
7   lifetime-2_lazy       37.560      1.800        18.656  22/22
7   lifetime-2_every      36.400      1.640        63.779  22/22
10  H0                    71.480      3.040             -  21/21
10  floor                 42.560      1.920             -  21/21
10  first_lazy            43.840      2.240        22.613  21/21
10  first_every           43.040      1.960        77.755  21/21
10  best_lazy             44.000      2.200        48.396  21/21
10  best_every            43.040      1.960        66.622  21/21
10  lifetime-0_lazy       43.840      2.240        23.123  21/21
10  lifetime-0_every      42.800      1.920        72.545  21/21
10  lifetime-1_lazy       43.640      2.200        23.520  21/21
10  lifetime-1_every      42.720      1.920        61.698  21/21
10  lifetime-2_lazy       43.560      2.200        18.848  21/21
10  lifetime-2_every      42.680      1.920        70.232  21/21
```
