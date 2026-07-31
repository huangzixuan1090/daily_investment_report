"""全市场缠论买点扫描报告（带 K线+MACD+成交量图）生成并发送。

- 读取 cache/chanlun_a_scan_YYYYMMDD.json
- 取前 N 只买点标的，并发抓取新浪日线（清一次代理、worker clear_proxy=False）
- 每只生成股价图（lib/charts），嵌入卡片下方
- 重写 reports/chanlun_a_buy_scan_YYYYMMDD.html 并以 HTML 正文发给 config 收件人
"""
from __future__ import annotations
import json
import socket
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lib import common, chanlun_a as ca, render, charts, mail

ROOT = common.ROOT
cfg = common.load_config()
TZ_CN = timezone(timedelta(hours=8))
tag = datetime.now(TZ_CN).strftime("%Y%m%d")
cache_dir = ROOT / cfg.get("paths", {}).get("cache_dir", "cache")
cache_path = cache_dir / f"chanlun_a_scan_{tag}.json"
# 当天缓存不存在时取最近一份
if not cache_path.exists():
    cands = sorted(cache_dir.glob("chanlun_a_scan_*.json"))
    if cands:
        cache_path = cands[-1]
print("缓存:", cache_path)

cache = json.loads(cache_path.read_text(encoding="utf-8"))
opps = cache.get("opportunities", [])
CAP = 60
opps = opps[:CAP]
print("展示标的数:", len(opps))

# 全局清一次代理，worker 内不再各自清（避免多线程竞态）
saved = ca._clear_proxy()
_orig_to = socket.getdefaulttimeout()
socket.setdefaulttimeout(30)

def _fetch(rec):
    code = rec.get("code")
    try:
        rows = ca.fetch_daily(code, bars=180, adjust="qfq", clear_proxy=False)
        return code, rows
    except Exception as e:  # noqa
        return code, None

bars_cache = cache_dir / f"scan_bars_{tag}.json"
if bars_cache.exists():
    _loaded = json.loads(bars_cache.read_text(encoding="utf-8"))
    bars_map = {k: v for k, v in _loaded.items() if v}
    print("复用 bars 缓存:", len(bars_map))
else:
    bars_map = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch, r): r for r in opps}
        done = 0
        for fut in as_completed(futs):
            done += 1
            code, rows = fut.result()
            bars_map[code] = rows
            if done % 15 == 0:
                print(f"  抓取日线 {done}/{len(opps)}")
    try:
        bars_cache.write_text(json.dumps(bars_map, ensure_ascii=False), encoding="utf-8")
        print("bars 缓存已保存:", bars_cache)
    except Exception:  # noqa
        pass

ca._restore_proxy(saved)
socket.setdefaulttimeout(_orig_to)

low_uris, hi_uris = {}, {}
for rec in opps:
    code = rec.get("code")
    rows = bars_map.get(code)
    if not rows:
        continue
    low = charts.make_price_chart_png(
        rows, signal_date=rec.get("signal_date"),
        signal_price=rec.get("signal_price"), point=rec.get("point"), code=code,
        width=4.2, height=3.5, dpi=40)
    if not low:
        continue
    low_uris[code] = low
    # 高分辨率版（用于应用内预览，不计入邮件 1MB 限制）
    hi = charts.make_price_chart_png(
        rows, signal_date=rec.get("signal_date"),
        signal_price=rec.get("signal_price"), point=rec.get("point"), code=code,
        width=6.6, height=4.7, dpi=82)
    if hi:
        hi_uris[code] = hi
print("生成图表数(低清/高清):", len(low_uris), len(hi_uris))

# 高清单：应用内预览（present_files / 浏览器打开）
html_hi = render.render_chanlun_scan_html(cache, charts=hi_uris)
out_hi = ROOT / "reports" / cache_path.name.replace("chanlun_a_scan_", "chanlun_a_buy_scan_").replace(".json", ".html")
out_hi.write_text(html_hi, encoding="utf-8")
print("HTML(高清预览):", out_hi, "大小(bytes):", len(html_hi))

# 低清单：邮件正文（受 1MB 限制，已压缩）
html_low = render.render_chanlun_scan_html(cache, charts=low_uris)
out_low = ROOT / "reports" / f"chanlun_a_buy_scan_{tag}.compact.html"
out_low.write_text(html_low, encoding="utf-8")
print("HTML(邮件正文):", out_low, "大小(bytes):", len(html_low))

cfg = common.load_config()
subject = f"全市场 A股 日线缠论买点扫描（近20个交易日）· 带股价图"
ok = mail.send_report(cfg, out_low, subject)
print("SEND_RESULT", ok, "预览文件:", out_hi.name)

