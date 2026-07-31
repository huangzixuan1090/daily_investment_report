"""ETF 表现：涨跌幅 + 主力资金流入/流出。

数据源：东方财富 ETF 排行接口（与板块模块同源，复用 push2 clist）。
沪深 ETF 市场用 fs=b:MK0021,b:MK0022（沪/深 ETF 板块合并）。
字段：f12=代码 f14=名称 f3=涨跌幅 f62=主力净流入 f184=主力净流入占比 f6=成交额。
数据与板块同口径：抓取「最新交易日」，标注为报告统一 data_date。
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from typing import List, Dict, Any
import requests

from . import common

log = common.log

HOSTS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
]
ETF_FS = "b:MK0021,b:MK0022"
TOP_FIELDS = "f12,f14,f2,f3,f62,f184,f6"


def _to_float(v):
    try:
        return float(v) if v not in (None, "-") else None
    except Exception:
        return None


def _fetch_top(fid: str, po: int, limit: int) -> List[Dict[str, Any]]:
    """按 fid 排序(po:1降序/0升序)取前 limit 只 ETF。直连东财，多主机+退避重试（避开限流）。"""
    params = {
        "pn": 1, "pz": limit, "po": po, "np": 1, "fltt": 2, "invt": 2,
        "fid": fid, "fs": ETF_FS, "fields": TOP_FIELDS,
    }
    headers = {"User-Agent": common.UA, "Referer": "https://quote.eastmoney.com/"}
    last_err = None
    for host in HOSTS:
        for attempt in range(2):
            try:
                r = requests.get(host, params=params, headers=headers, timeout=12)
                d = r.json()
                if d.get("rc") != 0:
                    raise RuntimeError(f"东财 rc={d.get('rc')}")
                diff = (d.get("data") or {}).get("diff") or []
                if not diff:
                    raise RuntimeError("东财返回空 diff")
                rows = []
                for x in diff:
                    rows.append({
                        "code": x.get("f12"), "name": x.get("f14"),
                        "price": _to_float(x.get("f2")),
                        "change_pct": _to_float(x.get("f3")),
                        "main_inflow": _to_float(x.get("f62")),
                        "main_inflow_pct": _to_float(x.get("f184")),
                        "amount": _to_float(x.get("f6")),
                    })
                return rows
            except Exception as e:  # noqa
                last_err = e
                if attempt < 1:
                    time.sleep(3)
    raise last_err


def get_etf(cfg: dict, data_date: str = None) -> dict:
    top_n = cfg.get("etf", {}).get("top_n", 15)
    today_tag = datetime.now().strftime("%Y%m%d")
    if not data_date:
        data_date = datetime.now().strftime("%Y-%m-%d")

    out = {
        "section": "etf", "date": data_date, "source": "eastmoney",
        "top_gain": [], "top_loss": [], "top_inflow": [], "top_outflow": [],
    }
    try:
        out["top_gain"] = _fetch_top("f3", 1, top_n)
        time.sleep(0.4)
        out["top_loss"] = _fetch_top("f3", 0, top_n)
        time.sleep(0.4)
        out["top_inflow"] = _fetch_top("f62", 1, top_n)
        time.sleep(0.4)
        out["top_outflow"] = _fetch_top("f62", 0, top_n)
        log.info("ETF: 东财成功 涨幅%d 跌幅%d 净流入%d 净流出%d",
                 len(out["top_gain"]), len(out["top_loss"]),
                 len(out["top_inflow"]), len(out["top_outflow"]))
    except Exception as e:  # noqa
        log.warning("ETF 东财失败: %s", e)
        out["error"] = f"东方财富 ETF 接口暂不可用（{e}），稍后重试"

    try:
        cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_dir / f"etf_{today_tag}.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass
    return out


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_etf(cfg)
    if "error" in r:
        print("ERR", r["error"])
    else:
        for kind, label in [("top_gain", "涨幅前"), ("top_loss", "跌幅前"),
                             ("top_inflow", "主力净流入前"), ("top_outflow", "主力净流出前")]:
            print(f"== {label} ==")
            for x in r[kind][:5]:
                print(f"  {x['name']:<16} 涨{x['change_pct']}  净流入{x['main_inflow']}")
