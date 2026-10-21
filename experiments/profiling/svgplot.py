"""Minimal SVG charts with only the standard library (no matplotlib on either machine).

Bar charts (stacked or single), line charts and scatter plots, light theme, palette from the dataviz
reference (validated: adjacent CVD ΔE >= 9.2). Render to PNG with `render_png` (headless Chrome).
"""
import html
import math
import re
import subprocess
from pathlib import Path

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
FONT = "Noto Sans CJK SC, Noto Sans SC, Noto Sans, sans-serif"
W, H = 960, 520
M = dict(l=80, r=24, t=88, b=64)


def _nice_ticks(vmax, n=5):
    step = 10 ** math.floor(math.log10(vmax / n))
    step = next(m * step for m in (1, 2, 2.5, 5, 10) if vmax / (m * step) <= n)
    return [i * step for i in range(int(math.ceil(vmax / step)) + 1)]


def _fmt(v):
    return f"{v:,.0f}" if v >= 10 or v == 0 else f"{v:g}"


def _width(s, size=13):
    return sum(size if ord(c) > 127 else size * 0.58 for c in str(s))


def _text(x, y, s, size=13, fill=INK2, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{html.escape(str(s))}</text>')


class _Frame:
    """Plot area, y axis with gridlines, title, subtitle and legend shared by all chart types."""

    def __init__(self, title, subtitle, ylabel, ymax, legend):
        self.x0, self.x1 = M["l"], W - M["r"]
        self.y0, self.y1 = H - M["b"], M["t"]
        self.ticks = _nice_ticks(ymax)
        self.ymax = self.ticks[-1]
        self.parts = [f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
                      _text(M["l"], 30, title, 18, INK, weight="bold"),
                      _text(M["l"], 52, subtitle, 13)]
        for t in self.ticks:
            y = self.y(t)
            self.parts.append(f'<line x1="{self.x0}" x2="{self.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/>')
            self.parts.append(_text(self.x0 - 8, y + 4, _fmt(t), 12, anchor="end"))
        self.parts.append(_text(18, (self.y0 + self.y1) / 2, ylabel, 13, anchor="middle")
                          .replace("<text", f'<text transform="rotate(-90 18 {(self.y0 + self.y1) / 2:.1f})"', 1))
        lx = self.x1
        for name, color in reversed(legend if len(legend) > 1 else []):
            lx -= 17 + _width(name) + 16
            self.parts.append(f'<rect x="{lx:.1f}" y="62" width="12" height="12" rx="2" fill="{color}"/>')
            self.parts.append(_text(lx + 17, 73, name, 13, INK))

    def y(self, v):
        return self.y0 - (self.y0 - self.y1) * v / self.ymax

    def ref_line(self, v, label):
        y = self.y(v)
        self.parts.append(f'<line x1="{self.x0}" x2="{self.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK2}" '
                          f'stroke-width="1.5" stroke-dasharray="6 4"/>')
        # surface backing keeps the label readable where it crosses bars
        w = _width(label, 12) + 8
        self.parts.append(f'<rect x="{self.x1 - w:.1f}" y="{y - 20:.1f}" width="{w:.1f}" height="16" rx="3" '
                          f'fill="{SURFACE}"/>')
        self.parts.append(_text(self.x1 - 4, y - 8, label, 12, INK, anchor="end"))

    def save(self, path):
        body = "".join(self.parts)
        Path(path).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                              f'viewBox="0 0 {W} {H}" font-family="{FONT}">{body}</svg>', encoding="utf-8")


def bar_chart(path, categories, series, title, subtitle, ylabel, xlabel="", ref=None, labels=None, grouped=False):
    """series: [(name, values)], stacked in order (or side by side if grouped).
    labels: {category: text} drawn above the bar."""
    if grouped:
        return _grouped_bars(path, categories, series, title, subtitle, ylabel, xlabel, ref)
    totals = [sum(vals[i] for _, vals in series) for i in range(len(categories))]
    f = _Frame(title, subtitle, ylabel, max(totals + [ref[0] if ref else 0]) * 1.08,
               [(name, SERIES[k]) for k, (name, _) in enumerate(series)])
    slot = (f.x1 - f.x0) / len(categories)
    bw = min(slot * 0.7, 48)
    for i, cat in enumerate(categories):
        x = f.x0 + slot * i + (slot - bw) / 2
        base = 0
        for k, (_, vals) in enumerate(series):
            top = base + vals[i]
            y_top, y_base = f.y(top), f.y(base)
            # 2px surface gap between stacked segments; only the top segment gets rounded corners
            h = max(y_base - y_top - (2 if k else 0), 0)
            rx = 4 if k == len(series) - 1 else 0
            f.parts.append(f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="{rx}" '
                           f'fill="{SERIES[k]}"/>')
            base = top
        f.parts.append(_text(x + bw / 2, f.y0 + 18, cat, 12, anchor="middle"))
        if labels and cat in labels:
            f.parts.append(_text(x + bw / 2, f.y(totals[i]) - 6, labels[cat], 12, INK, anchor="middle"))
    if xlabel:
        f.parts.append(_text((f.x0 + f.x1) / 2, H - 16, xlabel, 13, anchor="middle"))
    if ref:
        f.ref_line(*ref)
    f.save(path)


def _grouped_bars(path, categories, series, title, subtitle, ylabel, xlabel, ref):
    f = _Frame(title, subtitle, ylabel, max([max(v) for _, v in series] + [ref[0] if ref else 0]) * 1.08,
               [(name, SERIES[k]) for k, (name, _) in enumerate(series)])
    slot = (f.x1 - f.x0) / len(categories)
    bw = min(slot * 0.8 / len(series), 40)
    for i, cat in enumerate(categories):
        x = f.x0 + slot * i + (slot - bw * len(series)) / 2
        for k, (_, vals) in enumerate(series):
            y = f.y(vals[i])
            # 2px surface gap between neighbouring bars
            f.parts.append(f'<rect x="{x + k * bw + 1:.1f}" y="{y:.1f}" width="{bw - 2:.1f}" '
                           f'height="{f.y0 - y:.1f}" rx="4" fill="{SERIES[k]}"/>')
        f.parts.append(_text(f.x0 + slot * (i + 0.5), f.y0 + 18, cat, 12, anchor="middle"))
    if xlabel:
        f.parts.append(_text((f.x0 + f.x1) / 2, H - 16, xlabel, 13, anchor="middle"))
    if ref:
        f.ref_line(*ref)
    f.save(path)


def line_chart(path, xs, series, title, subtitle, ylabel, xlabel, ref=None, labels=None):
    """series: [(name, ys)] over shared numeric xs; None in ys ends the line there (e.g. OOM).
    ref=(y, label) e.g. the GPU capacity. Lines are direct-labeled when there are at most 4."""
    f = _Frame(title, subtitle, ylabel,
               max([y for _, ys in series for y in ys if y is not None] + [ref[0] if ref else 0]) * 1.08,
               [(name, SERIES[k]) for k, (name, _) in enumerate(series)])
    span = (max(xs) - min(xs)) or 1
    px = lambda v: f.x0 + 20 + (f.x1 - f.x0 - 40) * (v - min(xs)) / span
    for i, x in enumerate(xs):
        f.parts.append(_text(px(x), f.y0 + 18, labels[i] if labels else _fmt(x), 12, anchor="middle"))
    for k, (name, ys) in enumerate(series):
        pts = [(px(x), f.y(y)) for x, y in zip(xs, ys) if y is not None]
        f.parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" '
                       f'stroke="{SERIES[k]}" stroke-width="2"/>')
        for x, y in pts:
            f.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{SERIES[k]}" '
                           f'stroke="{SURFACE}" stroke-width="2"/>')
        if len(series) <= 4:
            f.parts.append(_text(pts[-1][0] - 8, pts[-1][1] - 10, name, 12, INK, anchor="end"))
    f.parts.append(_text((f.x0 + f.x1) / 2, H - 16, xlabel, 13, anchor="middle"))
    if ref:
        f.ref_line(*ref)
    f.save(path)


def scatter(path, points, title, subtitle, ylabel, xlabel, diagonal=None):
    """points: [(x, y, label)]; label is drawn only when not None. diagonal: text for a y = x line."""
    xmax = max(p[0] for p in points) * 1.08
    f = _Frame(title, subtitle, ylabel, max(p[1] for p in points) * 1.08, [])
    px = lambda v: f.x0 + (f.x1 - f.x0) * v / f.ymax if diagonal else f.x0 + (f.x1 - f.x0) * v / xmax
    for t in (f.ticks if diagonal else _nice_ticks(xmax)):
        f.parts.append(_text(px(t), f.y0 + 18, _fmt(t), 12, anchor="middle"))
    if diagonal:
        f.parts.append(f'<line x1="{px(0):.1f}" y1="{f.y(0):.1f}" x2="{px(f.ymax):.1f}" y2="{f.y(f.ymax):.1f}" '
                       f'stroke="{INK2}" stroke-width="1.5" stroke-dasharray="6 4"/>')
        f.parts.append(_text(px(f.ymax * 0.72) + 10, f.y(f.ymax * 0.72) + 22, diagonal, 12, INK))
    for x, y, label in points:
        f.parts.append(f'<circle cx="{px(x):.1f}" cy="{f.y(y):.1f}" r="5" fill="{SERIES[0]}" '
                       f'stroke="{SURFACE}" stroke-width="2"/>')
        if label:
            f.parts.append(_text(px(x) + 8, f.y(y) - 8, label, 12, INK))
    f.parts.append(_text((f.x0 + f.x1) / 2, H - 16, xlabel, 13, anchor="middle"))
    f.save(path)


def render_png(svg_path):
    """SVG -> PNG next to it, via headless Chrome (available locally)."""
    svg = Path(svg_path).resolve()
    width, height = re.search(r'width="(\d+)" height="(\d+)"', svg.read_text()).groups()
    subprocess.run(["google-chrome", "--headless", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={width},{height}", f"--screenshot={svg.with_suffix('.png')}", svg.as_uri()],
                   check=True, capture_output=True)

def stacked_grid(path, categories, panels, segments):
    """Shared-scale small multiples; each category has two stacks, or None for OOM."""
    cols, pw, ph = 4, 300, 240
    width, height = cols * pw, 110 + math.ceil(len(panels) / cols) * ph
    ticks = _nice_ticks(max(sum(stack) for _, groups in panels for group in groups
                           if group is not None for stack in group) * 1.05)
    parts = [f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
             _text(24, 30, 'Slab high-water breakdown by query and scale factor', 20, INK, weight='bold'),
             _text(24, 54, 'Memory in GB (10^9 bytes); paired bars: H0 baseline / H1 free after last read', 14)]
    x = 24
    for i, name in enumerate(segments):
        parts.append(f'<rect x="{x}" y="72" width="12" height="12" fill="{SERIES[i]}"/>')
        parts.append(_text(x + 18, 83, name, 13))
        x += 30 + _width(name)
    for index, (title, groups) in enumerate(panels):
        ox, oy = (index % cols) * pw, 110 + (index // cols) * ph
        x0, x1, y0, y1 = ox + 40, ox + pw - 12, oy + 184, oy + 26
        y = lambda value: y0 - (y0 - y1) * value / ticks[-1]
        parts.append(_text((x0 + x1) / 2, oy + 16, title, 15, INK, anchor='middle', weight='bold'))
        for tick in ticks:
            parts.append(f'<line x1="{x0}" x2="{x1}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" '
                         f'stroke="{GRID}"/>')
            parts.append(_text(x0 - 6, y(tick) + 4, _fmt(tick), 11, anchor='end'))
        slot = (x1 - x0) / len(categories)
        for i, (cat, group) in enumerate(zip(categories, groups)):
            center = x0 + slot * (i + 0.5)
            if group is None:
                parts.append(_text(center, y0 - 12, 'OOM', 12, INK2, anchor='middle'))
            else:
                for b, stack in enumerate(group):
                    bx = center - 24 + b * 26
                    base = 0
                    for s, value in enumerate(stack):
                        top = base + value
                        parts.append(f'<rect x="{bx:.1f}" y="{y(top):.1f}" width="22" '
                                     f'height="{y(base) - y(top):.1f}" fill="{SERIES[s]}"/>')
                        base = top
                    parts.append(_text(bx + 11, y0 + 14, f'H{b}', 10, anchor='middle'))
            parts.append(_text(center, y0 + 32, f'SF{cat}', 12, anchor='middle'))
    Path(path).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                          f'viewBox="0 0 {width} {height}" font-family="{FONT}">{"".join(parts)}</svg>',
                          encoding='utf-8')
