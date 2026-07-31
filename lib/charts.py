"""生成个股股价图（K线 + MACD + 成交量）三面板 PNG，返回 base64 data URI。

用于全市场缠论买点扫描报告：每只股票卡片下方嵌入一张图，带 MACD 与成交量，
并标出缠论信号价/信号日，便于直接对照。中国惯例：涨红跌绿。

无外部显示依赖（Agg 后端），可在无桌面的服务器/定时任务中运行。
"""
from __future__ import annotations

import base64
from io import BytesIO

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Rectangle
from matplotlib.gridspec import GridSpec

# 注册中文字体（macOS 自带 Hiragino Sans GB），让 K线图里的「成交量」等中文标签正常显示
_CJK_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
try:
    fm.fontManager.addfont(_CJK_FONT)
    _cjk_name = fm.FontProperties(fname=_CJK_FONT).get_name()
    matplotlib.rcParams["font.family"] = [_cjk_name, "DejaVu Sans"]
except Exception:  # noqa: 找不到中文字体时退回默认字体（中文标签可能显示为方块）
    pass
matplotlib.rcParams["axes.unicode_minus"] = False

# 中国惯例：上涨红、下跌绿
UP = "#d8392b"
DOWN = "#2ba471"
SIG = "#6f42c1"  # 信号标记紫


def _ema(arr, n: int):
    arr = np.asarray(arr, dtype=float)
    out = np.full_like(arr, np.nan)
    # 跳过开头 NaN，避免 warmup 期把 NaN 一路传染
    finite_idx = np.where(np.isfinite(arr))[0]
    if len(finite_idx) == 0 or len(arr) - finite_idx[0] < n:
        return out
    start = finite_idx[0]
    out[start + n - 1] = arr[start:start + n].mean()
    k = 2.0 / (n + 1)
    for i in range(start + n, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def compute_macd(close, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    diff = ema_fast - ema_slow
    dea = _ema(diff, signal)
    hist = 2 * (diff - dea)
    return diff, dea, hist


def make_price_chart_png(rows, signal_date=None, signal_price=None, point=None,
                         code: str = "", width: float = 4.2, height: float = 3.5,
                         dpi: int = 40) -> str | None:
    """rows: list[{date,open,high,low,close,volume}]（前复权）。返回 data URI 或 None。"""
    if not rows or len(rows) < 30:
        return None
    # 只取最近 ~90 根，既够 MACD(26+9) 与二买结构上下文，又让蜡烛在较小像素下更宽更清晰
    if len(rows) > 90:
        rows = rows[-90:]
    dates = [r.get("date", "") for r in rows]
    close = np.array([r["close"] for r in rows], dtype=float)
    op = np.array([r["open"] for r in rows], dtype=float)
    hi = np.array([r["high"] for r in rows], dtype=float)
    lo = np.array([r["low"] for r in rows], dtype=float)
    vol = np.array([r["volume"] for r in rows], dtype=float)
    diff, dea, hist = compute_macd(close)
    n = len(rows)
    x = np.arange(n)

    fig = plt.figure(figsize=(width, height), dpi=dpi)
    gs = GridSpec(3, 1, height_ratios=[3.0, 1.25, 1.25], hspace=0.12,
                  left=0.08, right=0.97, top=0.92, bottom=0.07)
    ax0 = fig.add_subplot(gs[0])
    ax1 = fig.add_subplot(gs[1], sharex=ax0)
    ax2 = fig.add_subplot(gs[2], sharex=ax0)

    # ---- K线 ----
    for i in range(n):
        c = UP if close[i] >= op[i] else DOWN
        o, cl = op[i], close[i]
        y0, y1 = min(o, cl), max(o, cl)
        ax0.add_patch(Rectangle((i - 0.4, y0), 0.8, max(y1 - y0, 1e-9),
                                facecolor=c, edgecolor=c, linewidth=0.5))
        ax0.plot([i, i], [lo[i], y0], color=c, linewidth=0.5)
        ax0.plot([i, i], [y1, hi[i]], color=c, linewidth=0.5)
    ma5 = _ema(close, 5)
    ma10 = _ema(close, 10)
    ma20 = _ema(close, 20)
    ax0.plot(x, ma5, color="#f59e0b", linewidth=0.8, label="MA5")
    ax0.plot(x, ma10, color="#3b82f6", linewidth=0.8, label="MA10")
    ax0.plot(x, ma20, color="#8b5cf6", linewidth=0.8, label="MA20")
    ax0.legend(loc="upper left", fontsize=7, framealpha=0.5, ncol=3)
    ax0.set_title(f"{code} · K线 / MACD / 成交量", fontsize=9, color="#333")
    ax0.tick_params(labelsize=7)
    ax0.grid(True, axis="y", color="#eee", linewidth=0.5)

    # 信号标记
    if signal_date and signal_date in dates:
        idx = dates.index(signal_date)
        ax0.axvline(idx, color=SIG, linewidth=0.8, linestyle="--", alpha=0.7)
        if signal_price:
            ax0.axhline(float(signal_price), color=SIG, linewidth=0.8,
                        linestyle=":", alpha=0.85)
            ax0.annotate(f"信号价 {signal_price}",
                         (idx, float(signal_price)), fontsize=6.5, color=SIG,
                         xytext=(2, 6), textcoords="offset points")

    # ---- MACD ----
    # 左轴：DIFF / DEA；右轴：红绿柱（按正负着色，柱子拉满高度，零轴对齐）
    ax1_right = ax1.twinx()
    mcolors = [UP if h >= 0 else DOWN for h in hist]
    ax1_right.bar(x, hist, color=mcolors, width=0.85)
    ax1.plot(x, diff, color="#333", linewidth=0.8, label="DIFF")
    ax1.plot(x, dea, color="#888", linewidth=0.8, label="DEA")
    ax1.axhline(0, color="#ccc", linewidth=0.6)
    # 让左右轴都以 0 为中心，确保红绿柱零轴与 DIFF/DEA 零轴在同一水平线
    max_h = float(np.nanmax(np.abs(hist))) if np.any(np.isfinite(hist)) else 1.0
    max_d = float(np.nanmax(np.abs(np.concatenate([diff, dea])))) if np.any(np.isfinite(np.concatenate([diff, dea]))) else 1.0
    ax1.set_ylim(-max_d * 1.15, max_d * 1.15)
    ax1_right.set_ylim(-max_h * 1.15, max_h * 1.15)
    ax1_right.set_yticks([])
    ax1.set_ylabel("MACD", fontsize=7)
    ax1.tick_params(labelsize=7)
    ax1.grid(True, axis="y", color="#eee", linewidth=0.5)

    # ---- 成交量 ----
    vcolors = [UP if close[i] >= op[i] else DOWN for i in range(n)]
    ax2.bar(x, vol, color=vcolors, width=0.8)
    ax2.set_ylabel("成交量", fontsize=7)
    ax2.tick_params(labelsize=7)
    ax2.grid(True, axis="y", color="#eee", linewidth=0.5)

    tick_pos = np.linspace(0, n - 1, 6).astype(int)
    ax2.set_xticks(tick_pos)
    ax2.set_xticklabels([dates[i][5:] for i in tick_pos], fontsize=6.5)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return "data:image/png;base64," + b64


if __name__ == "__main__":
    import json
    from . import chanlun_a as ca
    rows = ca.fetch_daily("600110", bars=180, clear_proxy=True)
    uri = make_price_chart_png(rows, code="600110")
    print("len", len(uri) if uri else None)
