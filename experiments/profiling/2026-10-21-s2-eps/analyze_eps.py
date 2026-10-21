"""Replay measured S3 allocation lifetimes for method A, without torch."""
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
S3 = HERE.parent / '2026-10-20-s3-free-dead'
sys.path.insert(0, str(S3))
sys.path.insert(0, str(HERE.parent / '2026-10-20-baseline-trace'))
from analyze_baseline import GB, Pool  # noqa: E402
from analyze_s3 import load_traces, read_rows  # noqa: E402
import svgplot  # noqa: E402

MB = 10**6
SETTINGS = [('40MB', None), ('0.10', 0.10), ('0.05', 0.05), ('0.02', 0.02), ('0.01', 0.01)]
LABELS = ['40 MB', '10%', '5%', '2%', '1%']


def events_for(ops, allocs, frees):
    order = {(op['plan'], op['op']): j for j, op in enumerate(ops)}
    starts, ends = defaultdict(list), defaultdict(list)
    for a, row in enumerate(allocs):
        starts[order[row['plan'], row['op']]].append((a, int(row['start_block']), int(row['requested_bytes'])))
    for row in frees:
        ends[order[row['plan'], row['op']]].append(int(row['start_block']))
    active, events = {}, []
    for j in range(len(ops)):
        for a, start, size in starts[j]:
            assert start not in active
            active[start] = a
        dead = [active.pop(start) for start in ends[j]]
        events.append(([(a, size) for a, start, size in starts[j]], dead))
    return events


def replay(events, block, merging):
    # All rounded requests together are sufficient, even if no span is ever reused.
    capacity = sum(max(1, (size + block - 1) // block) for new, dead in events for a, size in new) * block
    pool = Pool(capacity, block)
    active = {}
    highwater = internal = 0
    for new, dead in events:
        for a, size in new:
            start, count = pool.alloc(size)
            active[a] = (start, count, size)
        # Match S3's first maximal operator-end snapshot, before reclamation.
        extent = max((start + count for start, count, size in active.values()), default=0) * block
        if extent > highwater:
            highwater = extent
            internal = sum(count * block - size for start, count, size in active.values())
        for a in dead:
            start, count, size = active.pop(a)
            if merging == 'every':
                pool.free_times = 5
            pool.free(start, count)
    return highwater, internal


def summaries(rows):
    lines = ['SF  Setting Merge  Sum_H_GB  Mean_I/H_%  Mean_I/P_%  Bound_fail']
    for sf in sorted({r['sf'] for r in rows}):
        for setting, eps in SETTINGS:
            for merging in ('lazy', 'every'):
                group = [r for r in rows if (r['sf'], r['setting'], r['merge']) == (sf, setting, merging)]
                lines.append(f'{sf:<3} {setting:<7} {merging:<5} '
                             f'{sum(r["highwater_bytes"] for r in group) / GB:9.3f} '
                             f'{100 * statistics.mean(r["internal_frac"] for r in group):11.3f} '
                             f'{100 * statistics.mean(r["internal_bytes"] / r["pool_bytes"] for r in group):11.3f} '
                             f'{sum(not r["bound_holds"] for r in group):11}')
    return '\n'.join(lines)


def figures(rows):
    fig = HERE / 'figures'
    fig.mkdir(exist_ok=True)
    sf10 = [r for r in rows if r['sf'] == 10]
    internal = [statistics.mean(r['internal_bytes'] / r['pool_bytes'] for r in sf10
                                if r['setting'] == setting and r['merge'] == 'every') for setting, eps in SETTINGS]
    svgplot.line_chart(fig / '1_internal_vs_eps.svg', range(5),
                       [('Internal / pool (every)', internal), ('Epsilon bound', [eps for setting, eps in SETTINGS])],
                       'Internal fragmentation versus epsilon at SF10',
                       '21 measured queries; Q21 unavailable; mean of per-query ratios',
                       'Internal / pool', 'Block setting', labels=LABELS)
    series = [(merging, [sum(r['highwater_bytes'] for r in sf10
                            if r['setting'] == setting and r['merge'] == merging) / GB
                         for setting, eps in SETTINGS]) for merging in ('lazy', 'every')]
    svgplot.line_chart(fig / '2_highwater_vs_eps.svg', range(5), series,
                       'Total slab high-water mark versus epsilon at SF10',
                       '21 measured queries; Q21 unavailable; unlimited replay pool',
                       'Total high-water (GB)', 'Block setting', labels=LABELS)


def write_readme(rows, summary, mismatches, planning_failures):
    counts = ', '.join(f'SF{sf}：{sum(r["sf"] == sf for r in rows) // 10} 条'
                       for sf in sorted({r['sf'] for r in rows}))
    results = []
    for setting, eps in SETTINGS:
        group = [r for r in rows if r['sf'] == 10 and r['setting'] == setting and r['merge'] == 'every']
        results.append(f'- SF10 / `{setting}` / `every`：高水位合计 '
                       f'{sum(r["highwater_bytes"] for r in group) / GB:.3f} GB，平均内部碎片 / 池容量 '
                       f'{100 * statistics.mean(r["internal_bytes"] / r["pool_bytes"] for r in group):.3f}%。')
    (HERE / 'README.md').write_text(r'''# 2026-10-21 S2：方法 A 的 ε 与 slab 碎片离线回放

## 目的
在 S3 实测释放时机上，比较固定 40 MB 与方法 A 规划的块大小，量化内部碎片与 slab 高水位的变化，并检查理论上限。
不模拟 PyTorch 侧显存，不据此推断 GPU 耗时。

## 配置与运行
- 本地在 `experiments/profiling/` 下运行 `python3 -B 2026-10-21-s2-eps/analyze_eps.py`；分析仅用 Python 标准库，无 torch。
- 服务器在同一目录、项目环境中运行 `python3 -B 2026-10-21-s2-eps/plan_n_live.py`，
  从 22 个查询 pickle 重建 `plan_n_live.csv`；该脚本依赖服务器的 torch 和计划反序列化环境，本地不运行。
- 输入复用 S3 的 `load_traces()`：`trace-rerun/` 按 SF / 查询完整替换 `trace/` 对应记录；只分析成功查询。SF10 Q21 无数据，不补零。
- 单位为十进制：$1\,\mathrm{MB}=10^6$ 字节，$1\,\mathrm{GB}=10^9$ 字节

## 方法
保留算子、申请、释放的 CSV 行顺序。原始块起点仅用于在释放时匹配当前申请，匹配后使用独立申请编号；
块地址复用不会混淆生命周期。未出现在释放记录中的申请保留至查询结束。
每个算子先申请，再记录释放前状态，最后按原始顺序释放。
取第一次达到最大占用范围末端的算子结束时刻，与 S3 的快照定义一致；算子内只有申请，所以此定义不会遗漏更高的地址峰值。

复用 baseline 的 `Pool`，first-fit 按空闲列表找首个可用段并从低端分配；释放追加到列表末尾。
`lazy` 每第六次释放排序并合并，`every` 每次释放合并。回放池容量取全部申请向上取整后容量之和，保证不受容量上限约束；
不把实测池容量作为回放限制。有限池的尾段可能改变 first-fit 路径，因此固定块回放也逐行与实测比较。

计划列数取 `n_live_free_dead`，各计划使用 `max_live_columns(p.ops_idx, p.ops_dict, True)` 后取最大；
另存不开 S3 的 `n_live_no_free` 供复现原表。池容量取合并后该查询的 `gpu_pool_gb`。

$$
b_{\mathrm{MB}}=\max\left(1,\left\lfloor\frac{\varepsilon P}{N\,10^6}\right\rfloor\right),\qquad b=10^6 b_{\mathrm{MB}}
$$

$N$ 取“进入算子时仍存活的列数 + 该算子的输出列数”的最大值（每个输出拿到一块新申请，而输入在算子运行期间仍占着）。
同时占用的申请数不超过 $N$，每项取整浪费不超过一块，所以 $I\le Nb$；本实验逐行检验。1 MB 下限还可能破坏 $Nb\le\varepsilon P$，
所以脚本独立检查这条规划不等式；固定 40 MB 无对应的 ε 上限。

## 派生列定义
令 $A$ 为首次最大高水位快照内的申请集合，$s_a$ 为请求字节，$p_a$ 为回放块起点，$c_a=\max(1,\lceil s_a/b\rceil)$ 为占块数

| 列 | 定义 |
|---|---|
| `sf`, `qid`, `setting`, `merge` | 规模、查询、块大小设置与合并时机；`setting` 为 `40MB` 或 ε 的十进制字符串 |
| `block_mb`, `n_live`, `pool_bytes` | $b/10^6$、$N$、$P$ |
| `highwater_bytes` | $H=\max_t\max_{a\in A_t}(p_a+c_a)b$ |
| `internal_bytes` | $I=\sum_{a\in A}(c_ab-s_a)$ |
| `internal_frac` | $I/H$，包含空洞的高水位为分母 |
| `bound_bytes` | $Nb$ |
| `bound_holds` | $I\le Nb$，不等同于规划条件 $Nb\le\varepsilon P$ |

## 图
英文 SVG 由 `svgplot.py` 生成，并调用 `render_png` 输出同名 PNG。

![Internal fragmentation](figures/1_internal_vs_eps.svg)

图 1 使用 SF10 各查询 $I/P$ 的算术平均，仅绘制 `every`；参考线为 $y=\varepsilon$，固定 40 MB 处不画参考点

![High-water mark](figures/2_highwater_vs_eps.svg)

图 2 为 SF10 所有可用查询的 $H$ 合计，分别绘制 `lazy` 与 `every`；合计不是查询并发执行的池容量

## 主要结果
''' + f'- 数据覆盖：{counts}；输出 {len(rows)} 行。\n'
        + '\n'.join(results)
        + f'\n- 内部碎片上限不成立：{sum(not r["bound_holds"] for r in rows)} 行。'
        + f'\n- 规划上限不成立：{len(planning_failures)} 个 SF / 查询 / ε 组合（不重复计算两种合并时机）。'
        + ('\n' + '\n'.join(planning_failures) if planning_failures else '')
        + f'\n- 40 MB / lazy 与 S3 实测高水位逐行比较：{len(mismatches)} 行不一致。'
        + ('\n' + '\n'.join(mismatches) if mismatches else '全部 65 行相等。')
        + '\n\n## 汇总\n平均碎片分别报告高水位分母与实测池容量分母；合计单位 GB。\n\n```text\n'
        + summary + '\n```\n', encoding='utf-8')


def main():
    traces, grouped = load_traces()
    counts = {r['qid']: int(r['n_live_free_dead']) for r in read_rows(HERE / 'plan_n_live.csv')}
    measured = {(r['sf'], r['qid']): int(r['highwater_real_bytes'])
                for r in read_rows(S3 / 's3_derived.csv') if r['status'] == 'ok'}
    rows, mismatches, planning_failures = [], [], []
    for query in sorted(traces['queries'], key=lambda r: (int(r['sf']), int(r['qid']))):
        if query['status'] != 'ok':
            continue
        key = query['sf'], query['qid']
        events = events_for(*(grouped[name][key] for name in ('ops', 'allocs', 'frees')))
        n, pool_bytes = counts[query['qid']], int(float(query['gpu_pool_gb']) * GB)
        for setting, eps in SETTINGS:
            block_mb = 40 if eps is None else max(1, math.floor(eps * pool_bytes / n / MB))
            bound = n * block_mb * MB
            if eps is not None and bound > eps * pool_bytes:
                planning_failures.append(f'- SF{key[0]} Q{key[1]} ε={eps}: {bound} > {eps * pool_bytes:g} bytes')
            for merging in ('lazy', 'every'):
                highwater, internal = replay(events, block_mb * MB, merging)
                rows.append(dict(sf=int(key[0]), qid=int(key[1]), setting=setting, merge=merging,
                                 block_mb=block_mb, n_live=n, pool_bytes=pool_bytes, highwater_bytes=highwater,
                                 internal_bytes=internal, internal_frac=internal / highwater,
                                 bound_bytes=bound, bound_holds=internal <= bound))
                if setting == '40MB' and merging == 'lazy' and highwater != measured[key]:
                    mismatches.append(f'- SF{key[0]} Q{key[1]}: replay={highwater}, measured={measured[key]} bytes')
    with (HERE / 'eps_replay.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    summary = summaries(rows)
    print(summary)
    print(f'40MB/lazy mismatches: {len(mismatches)}; planning bound failures: {len(planning_failures)}')
    for line in mismatches + planning_failures:
        print(line)
    figures(rows)
    write_readme(rows, summary, mismatches, planning_failures)
    for svg in sorted((HERE / 'figures').glob('*.svg')):
        svgplot.render_png(svg)


if __name__ == '__main__':
    main()
