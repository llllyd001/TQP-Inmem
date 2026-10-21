"""Replay baseline slab lifetimes with only the Python standard library."""
import csv
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / '2026-10-20-baseline-trace'
sys.path.insert(0, str(BASELINE))
from analyze_baseline import GB, Pool, allocation_lifetimes, analyze, load_traces  # noqa: E402
import svgplot  # noqa: E402

BLOCK = 40_000_000
PLACEMENTS = ('first', 'best', 'lifetime-0', 'lifetime-1', 'lifetime-2')
RULES = [(p, m) for p in PLACEMENTS for m in ('lazy', 'every')]
NAMES = [f'{p}_{m}' for p, m in RULES]


class ReplayPool(Pool):
    def __init__(self, blocks, placement, merging):
        super().__init__(blocks * BLOCK, BLOCK)
        self.placement = placement
        self.merging = merging
        self.merge_time_us = 0.0

    def alloc(self, size, lifetime):
        high = self.placement.startswith('lifetime-') and lifetime <= int(self.placement[-1])
        if self.placement == 'first' or (self.placement.startswith('lifetime-') and not high):
            return super().alloc(size)
        n = max((size + BLOCK - 1) // BLOCK, 1)
        candidates = [i for i, (_, length) in enumerate(self.free_list) if length >= n]
        if not candidates:
            raise MemoryError
        if high:
            i = max(candidates, key=lambda i: self.free_list[i][0])
        else:
            i = min(candidates, key=lambda i: (self.free_list[i][1], self.free_list[i][0]))
        start, length = self.free_list[i]
        ptr = start + length - n if high else start
        if length == n:
            self.free_list.pop(i)
        else:
            self.free_list[i] = (start if high else start + n, length - n)
        return ptr, n

    def free(self, start, n):
        if self.merging == 'every':
            self.free_times = 5
        super().free(start, n)

    def merge(self):
        begin = time.perf_counter()
        super().merge()
        self.merge_time_us += (time.perf_counter() - begin) * 1e6


def events_for(ops, allocations, last_use):
    starts, ends = [[] for _ in ops], [[] for _ in ops]
    occupied = floor = 0
    for a, ((i, _, n, size), last) in enumerate(zip(allocations, last_use)):
        assert last >= i and n == max((size + BLOCK - 1) // BLOCK, 1)
        starts[i].append((a, size, last - i))
        ends[last].append(a)
    for new, dead in zip(starts, ends):
        occupied += sum(allocations[a][2] for a, _, _ in new)
        floor = max(floor, occupied)
        occupied -= sum(allocations[a][2] for a in dead)
    assert occupied == 0
    return list(zip(starts, ends)), floor


def replay(events, blocks, placement, merging):
    pool = ReplayPool(blocks, placement, merging)
    active = {}
    for new, dead in events:
        for a, size, lifetime in new:
            try:
                active[a] = pool.alloc(size, lifetime)
            except MemoryError:
                return False, pool.merge_time_us
        for a in dead:
            pool.free(*active.pop(a))
    assert not active
    return True, pool.merge_time_us


def minimum_pool(events, floor, upper, placement, merging):
    def succeeds(blocks):
        return replay(events, blocks, placement, merging)[0]

    if not succeeds(upper):
        return None, None, False
    lo, hi = floor, upper
    while lo < hi:
        mid = (lo + hi) // 2
        if succeeds(mid):
            hi = mid
        else:
            lo = mid + 1
    # Placement need not be monotone in capacity; certify the global minimum too.
    candidate = lo
    for blocks in range(floor, candidate):
        if succeeds(blocks):
            lo = blocks
            break
    success, merge_us = replay(events, lo, placement, merging)
    assert success and not succeeds(lo - 1)
    return lo * BLOCK, merge_us, lo != candidate


def summaries(rows):
    lines = ['SF  Rule                 Sum_GB  Median_GB  Max_merge_us  Success']
    for sf in sorted({r['sf'] for r in rows}):
        group = [r for r in rows if r['sf'] == sf]
        for name in ['H0', 'floor'] + NAMES:
            column = {'H0': 'highwater_bytes', 'floor': 'floor_bytes'}.get(name, f'{name}_pool_bytes')
            values = [r[column] / GB for r in group if r[column] is not None]
            total = f'{sum(values):.3f}' if len(values) == len(group) else 'NA'
            median = f'{statistics.median(values):.3f}' if values else 'NA'
            times = [r[f'{name}_merge_us'] for r in group
                     if name in NAMES and r[f'{name}_merge_us'] is not None]
            maximum = f'{max(times):.3f}' if times else '-'
            lines.append(f'{sf:<3} {name:<20} {total:>7} {median:>10} {maximum:>13} '
                         f'{len(values):>3}/{len(group)}')
    return '\n'.join(lines)


def figures(rows):
    fig = HERE / 'figures'
    fig.mkdir(exist_ok=True)
    # Wide charts leave room for all query bars and the eleven-series legend.
    svgplot.W, svgplot.H = 2200, 650
    svgplot.SERIES = svgplot.SERIES + ['#855c34', '#647887', '#202020']
    sf10 = sorted((r for r in rows if r['sf'] == 10), key=lambda r: r['qid'])
    thresholds = [t for t in range(3) if all(r[f'lifetime-{t}_every_pool_bytes'] is not None for r in sf10)]
    best_t = min(thresholds, key=lambda t: sum(r[f'lifetime-{t}_every_pool_bytes'] for r in sf10))
    chosen = ['first_lazy', 'first_every', 'best_lazy', 'best_every', f'lifetime-{best_t}_every']
    series = [('H0', [r['highwater_bytes'] / GB for r in sf10])]
    series += [(name, [r[f'{name}_pool_bytes'] / GB for r in sf10]) for name in chosen
               if all(r[f'{name}_pool_bytes'] is not None for r in sf10)]
    series += [('floor', [r['floor_bytes'] / GB for r in sf10])]
    svgplot.bar_chart(fig / '1_pool_need_sf10.svg', [f"Q{r['qid']}" for r in sf10], series,
                      'Minimum slab pool by query at SF10',
                      f'Last-read reclamation; lifetime-{best_t} selected by SF10 total; Q21 has no trace',
                      'Pool (GB)', 'TPC-H query', grouped=True)
    sfs = sorted({r['sf'] for r in rows})
    series = [(name, [sum(r[f'{name}_pool_bytes'] for r in rows if r['sf'] == sf) / GB for sf in sfs])
              for name in NAMES if all(r[f'{name}_pool_bytes'] is not None for r in rows)]
    series += [('floor', [sum(r['floor_bytes'] for r in rows if r['sf'] == sf) / GB for sf in sfs])]
    svgplot.bar_chart(fig / '2_summary.svg', [f'SF{sf}' for sf in sfs], series,
                      'Total minimum slab pool by placement and coalescing rule',
                      '65 traces total: SF5 = 22, SF7 = 22, SF10 = 21; totals within each SF',
                      'Total pool (GB)', 'Scale factor', grouped=True)
    return best_t


def write_readme(rows, summary, best_t, corrections):
    results = []
    successes = sum(r[f'{name}_pool_bytes'] is not None for r in rows for name in NAMES)
    for sf in sorted({r['sf'] for r in rows}):
        group = [r for r in rows if r['sf'] == sf]
        totals = {name: sum(r[f'{name}_pool_bytes'] for r in group) / GB for name in NAMES
                  if all(r[f'{name}_pool_bytes'] is not None for r in group)}
        best = min(totals.values())
        winners = '、'.join(f'`{name}`' for name in totals if totals[name] == best)
        h0 = sum(r['highwater_bytes'] for r in group) / GB
        floor = sum(r['floor_bytes'] for r in group) / GB
        results.append(f'- SF{sf}：H₀ 合计 {h0:.2f} GB；`first_lazy` {totals["first_lazy"]:.2f} GB；'
                       f'最小合计 {best:.2f} GB（{winners}）；floor 合计 {floor:.2f} GB。')
    (HERE / 'README.md').write_text(r'''# 2026-10-20 S4：slab 放置与合并时机离线回放

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
''' + '\n'.join(results) + f'\n- SF10 图选用 `lifetime-{best_t}_every`。'
        + f'\n- 全局检查修正了 {corrections} 个二分候选；成功的 {successes} 组均完成最小值与少一块验证。'
        + '\n- 所有组合不低于 floor；`first_lazy` 不超过 baseline 的 `highwater_free_bytes`。'
        + '\n\n## 汇总\n合计与中位数单位 GB；最大合并耗时为该 SF 内单条查询的最大值。\n\n```text\n'
        + summary + '\n```\n', encoding='utf-8')


def main():
    traces, grouped = load_traces(BASELINE / 'trace')
    rows, corrections = [], 0
    for query in traces['queries']:
        key = query['sf'], query['qid']
        ops, allocs, reads = (grouped[name][key] for name in ('ops', 'allocs', 'reads'))
        assert int(float(query['block_mb']) * 1e6) == BLOCK
        allocations, last_use = allocation_lifetimes(ops, allocs, reads)
        events, floor = events_for(ops, allocations, last_use)
        baseline = analyze(query, ops, allocs, reads)
        upper = baseline['highwater_bytes'] // BLOCK
        row = dict(sf=int(query['sf']), qid=int(query['qid']), highwater_bytes=upper * BLOCK,
                   floor_bytes=floor * BLOCK)
        for placement, merging in RULES:
            name = f'{placement}_{merging}'
            capacity, merge_us, corrected = minimum_pool(events, floor, upper, placement, merging)
            corrections += corrected
            assert capacity is None or capacity >= row['floor_bytes']
            row[f'{name}_pool_bytes'] = capacity
            row[f'{name}_merge_us'] = merge_us
        assert row['first_lazy_pool_bytes'] <= baseline['highwater_free_bytes']
        rows.append(row)
    with (HERE / 's4_replay.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    summary = summaries(rows)
    print(summary)
    print(f'Validated {len(rows)} traces; global-minimum corrections: {corrections}')
    best_t = figures(rows)
    write_readme(rows, summary, best_t, corrections)
    for svg in sorted((HERE / 'figures').glob('*.svg')):
        svgplot.render_png(svg)


if __name__ == '__main__':
    main()
