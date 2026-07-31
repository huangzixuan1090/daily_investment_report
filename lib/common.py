"""公共工具：配置加载、日志、带重试的 HTTP。"""
from __future__ import annotations
import json
import logging
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

log = logging.getLogger("daily_report")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                     "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(os.environ.get("DR_LOG", "INFO"))


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 20, retries: int = 3, referer: str | None = None) -> str:
    hdr = {"User-Agent": UA}
    if referer:
        hdr["Referer"] = referer
    if headers:
        hdr.update(headers)
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=hdr, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa
            last = str(e)
            time.sleep(1.0 + i)
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last}")


def http_json(url: str, **kw) -> object:
    return json.loads(http_get(url, **kw))


def fmt_money(v: float | None) -> str:
    """金额格式化：亿/万。"""
    if v is None:
        return "-"
    try:
        v = float(v)
    except Exception:
        return "-"
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.2f}万"
    return f"{v:.0f}"


def fmt_signed_money(v: float | None) -> str:
    if v is None:
        return "-"
    s = fmt_money(abs(v))
    if v > 0:
        return f"+{s}"
    if v < 0:
        return f"-{s}"
    return s


def pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.{digits}f}%"
    except Exception:
        return "-"


def color_by_pct(v: float | None) -> str:
    """中国股市惯例：涨红跌绿。"""
    if v is None:
        return "#333"
    try:
        v = float(v)
    except Exception:
        return "#333"
    if v > 0:
        return "#d8392b"  # 红
    if v < 0:
        return "#2ba471"  # 绿
    return "#333"
