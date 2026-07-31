"""全球债券市场与货币市场数据。

数据源：akshare（中债/美债收益率），新浪财经（主要汇率对），Yahoo Finance（美元指数兜底）。
仅取最近一个交易日的收盘/变化，用于每日报告的快速宏观概览。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from lib import common

log = common.log

# 债券配置：key, 中文名, akshare 列名（中债/美债）
BOND_CFG = [
    ("US2Y", "美国2年期国债", "美国国债收益率2年"),
    ("US5Y", "美国5年期国债", "美国国债收益率5年"),
    ("US10Y", "美国10年期国债", "美国国债收益率10年"),
    ("US30Y", "美国30年期国债", "美国国债收益率30年"),
    ("CN10Y", "中国10年期国债", "中国国债收益率10年"),
    ("CN2Y", "中国2年期国债", "中国国债收益率2年"),
]

# 新浪财经外汇代码：key, 中文名, sina code
FX_CFG = [
    ("EURUSD", "欧元/美元", "fx_seurusd"),
    ("GBPUSD", "英镑/美元", "fx_sgbpusd"),
    ("USDJPY", "美元/日元", "fx_susdjpy"),
    ("USDCNY", "美元/人民币", "fx_susdcny"),
    ("USDCHF", "美元/瑞郎", "fx_susdchf"),
    ("AUDUSD", "澳元/美元", "fx_saudusd"),
    ("USDCAD", "美元/加元", "fx_susdcad"),
]


def _fetch_bond_yields() -> dict[str, dict] | None:
    """用 akshare 获取中债/美债收益率，返回最新两个交易日数据用于计算变化。"""
    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return None
        df = df.sort_values("日期", ascending=False).head(5)
        # 取最近两个有效交易日（美债可能因时差导致最新一日为 NaN）
        rows = df.to_dict("records")
        out = {}
        for key, name, col in BOND_CFG:
            # 找到该债券最近两个有效值
            vals = []
            for row in rows:
                v = row.get(col)
                try:
                    fv = float(v) if v is not None and not (isinstance(v, float) and __import__('math').isnan(v)) else None
                except Exception:
                    fv = None
                if fv is not None:
                    vals.append((str(row["日期"])[:10], fv))
                if len(vals) >= 2:
                    break
            if len(vals) < 1:
                continue
            last_date, last = vals[0]
            prev = vals[1][1] if len(vals) >= 2 else None
            chg = (last - prev) if prev is not None else None
            chg_pct = (chg / prev * 100) if prev else None
            out[key] = {
                "key": key, "name": name, "symbol": col,
                "yield": round(last, 4), "change": round(chg, 4) if chg is not None else None,
                "change_pct": round(chg_pct, 4) if chg_pct is not None else None,
                "date": last_date,
            }
        return out
    except Exception as e:  # noqa
        log.warning("akshare 债券收益率获取失败: %s", e)
        return None


def _fetch_sina_fx() -> dict[str, dict] | None:
    """新浪财经外汇即期报价。返回最新价、昨收、涨跌。"""
    codes = ",".join([c for _, _, c in FX_CFG])
    url = f"https://hq.sinajs.cn/list={codes}"
    try:
        r = requests.get(url, headers={
            "User-Agent": common.UA,
            "Referer": "https://finance.sina.com.cn/",
        }, timeout=15)
        r.encoding = "gbk"
        text = r.text
        out = {}
        for key, name, code in FX_CFG:
            m = re.search(rf'var hq_str_{code}="([^"]*)"', text)
            if not m:
                continue
            p = m.group(1).split(",")
            # 新浪外汇格式：0 买入价, 1 卖出价, 3 昨收, 4 最高, 5 最低, 6 最新？
            # 实际常见格式： name, buy, sell, close, high, low, open, prev, ...
            # 这里取稳妥值：p[3] 为昨收, p[6] 或 p[7] 为最新，不同品种略有差异
            try:
                # 常见索引：0名称，1买入，2卖出，3昨收，4最高，5最低，6最新？但不同品种字段不同
                # 观察 fx_susdcny 格式： name, buy, sell, close, high, low, open, prev, datetime
                # 我们用 p[3]（昨收/最新基准）和 p[6] 或 p[7] 综合判断
                last = float(p[6]) if len(p) > 6 and p[6] not in ("", "0") else None
                prev = float(p[3]) if len(p) > 3 and p[3] not in ("", "0") else None
                if last is None and prev is not None:
                    last = prev
                if last is None or prev is None:
                    continue
                chg = last - prev
                chg_pct = chg / prev * 100 if prev else None
            except Exception:
                continue
            out[key] = {
                "key": key, "name": name, "symbol": code,
                "price": round(last, 4), "change": round(chg, 4),
                "change_pct": round(chg_pct, 4) if chg_pct is not None else None,
            }
        return out if out else None
    except Exception as e:  # noqa
        log.warning("新浪财经外汇获取失败: %s", e)
        return None


def _fetch_dxy() -> dict | None:
    """Yahoo 美元指数，限流严重时可能失败。"""
    import urllib.request
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": common.UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        res = (d.get("chart") or {}).get("result") or []
        if not res:
            return None
        closes = [c for c in res[0]["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return None
        prev, last = closes[-2], closes[-1]
        chg, chg_pct = last - prev, (last - prev) / prev * 100
        return {
            "key": "DXY", "name": "美元指数", "symbol": "DX-Y.NYB",
            "price": round(float(last), 4), "change": round(float(chg), 4),
            "change_pct": round(float(chg_pct), 4),
        }
    except Exception as e:  # noqa
        log.warning("美元指数获取失败: %s", e)
        return None


def get_global_markets(cfg: dict, data_date: str = None) -> dict[str, Any]:
    """返回 {bonds: [...], fx: [...], date, ok, error}。"""
    if not data_date:
        data_date = datetime.now().strftime("%Y-%m-%d")

    result = {
        "ok": False, "date": data_date,
        "bonds": [], "fx": [], "error": None,
    }

    bonds = _fetch_bond_yields()
    if bonds:
        result["bonds"] = list(bonds.values())
        # 用债券数据日期覆盖
        if bonds.get("US10Y") and bonds["US10Y"].get("date"):
            result["date"] = bonds["US10Y"]["date"]

    fx = _fetch_sina_fx()
    if fx:
        result["fx"] = list(fx.values())

    # 美元指数兜底
    dxy = _fetch_dxy()
    if dxy:
        result["fx"].insert(0, dxy)

    result["ok"] = bool(result["bonds"] or result["fx"])
    if not result["ok"]:
        result["error"] = "全球债券与货币数据暂缺（akshare/新浪/雅虎均不可用）。"
    return result


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_global_markets(cfg)
    print(json.dumps({
        "ok": r["ok"], "date": r["date"],
        "bonds": [{"name": b["name"], "yield": b["yield"], "change_pct": b["change_pct"]}
                  for b in r["bonds"]],
        "fx": [{"name": f["name"], "price": f["price"], "change_pct": f["change_pct"]}
               for f in r["fx"]],
    }, ensure_ascii=False, indent=2))
