"""股票板块表现：行业板块 + 概念板块。
数据源优先级：东方财富 push2（主，带退避重试）→ 新浪行业板（兜底，仅行业板）。
东财对本机 IP 偶发限流，退避重试给恢复留窗口；行业板有新浪兜底，概念板限流时优雅降级。
"""
from __future__ import annotations
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

import requests

from . import common

log = common.log

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
FIELDS = "f12,f14,f2,f3,f4,f5,f6,f7,f8,f62,f184,f66,f69,f72,f75,f124,f133,f134"

SINA_INDUSTRY_URL = "https://money.finance.sina.com.cn/q/view/newFLJK.php"


def _to_float(v):
    try:
        return float(v) if v not in (None, "-") else None
    except Exception:
        return None


def _fetch_top(fs: str, fid: str, po: int, limit: int) -> List[Dict[str, Any]]:
    """按指定字段 fid 排序(po:1降序/0升序)取前 limit 个板块。"""
    params = {
        "pn": 1, "pz": limit, "po": po, "np": 1, "fltt": 2, "invt": 2,
        "fid": fid, "fs": fs, "fields": FIELDS,
    }
    data = common.http_json(CLIST_URL, params=params,
                            referer="https://quote.eastmoney.com/",
                            timeout=10, retries=2)
    diff = (data.get("data") or {}).get("diff") or []
    total = (data.get("data") or {}).get("total", 0)
    rows = []
    for d in diff:
        rows.append({
            "code": d.get("f12"), "name": d.get("f14"),
            "price": _to_float(d.get("f2")), "change_pct": _to_float(d.get("f3")),
            "amount": _to_float(d.get("f6")), "amplitude": _to_float(d.get("f7")),
            "main_inflow": _to_float(d.get("f62")), "main_inflow_pct": _to_float(d.get("f184")),
            "up_count": d.get("f134"),
        })
    return rows, total


def _try_eastmoney_board(b: dict, top_n: int) -> Dict[str, Any]:
    """东财主源：取涨/跌/主力流入/流出各 top_n。带退避重试。"""
    fs = b["fs"]
    last_err = None
    for attempt in range(3):
        try:
            top_gain, total = _fetch_top(fs, "f3", 1, top_n)
            time.sleep(0.4)
            top_loss, _ = _fetch_top(fs, "f3", 0, top_n)
            time.sleep(0.4)
            top_inflow, _ = _fetch_top(fs, "f62", 1, top_n)
            time.sleep(0.4)
            top_outflow, _ = _fetch_top(fs, "f62", 0, top_n)
            return {
                "key": b["key"], "name": b["name"], "total": total,
                "top_gain": top_gain, "top_loss": top_loss,
                "top_inflow": top_inflow, "top_outflow": top_outflow,
                "source": "eastmoney",
            }
        except Exception as e:  # noqa
            last_err = e
            if attempt < 2:
                time.sleep(30)  # 退避，给东财限流恢复留窗口
    raise RuntimeError(f"东财失败: {last_err}")


def _fetch_sina_industry() -> List[Dict[str, Any]]:
    """新浪行业板兜底（仅行业板有涨跌幅+领涨股）。GBK 编码。"""
    r = requests.get(SINA_INDUSTRY_URL,
                     headers={"User-Agent": common.UA, "Referer": "https://finance.sina.com.cn/"},
                     timeout=10)
    r.encoding = "gbk"
    # 新浪返回形如 var S_Finance_bankuai_ = {...}; 或直接 {...}（可能无结尾分号）
    i = r.text.find("{")
    j = r.text.rfind("}")
    if i < 0 or j <= i:
        raise RuntimeError("新浪行业板响应无数据")
    obj = r.text[i:j + 1]
    if '"__ERR"' in obj:
        raise RuntimeError("新浪行业板返回错误")
    pairs = re.findall(r'"([^"]+)":"([^"]*)"', obj)
    rows = []
    for _k, v in pairs:
        p = v.split(",")
        if len(p) < 10:
            continue
        try:
            chg = float(p[4])   # [3]=均价, [4]=涨跌幅
            lead_chg = float(p[9]) if p[9] not in ("", "-") else None
        except Exception:
            continue
        rows.append({
            "code": _k, "name": p[1], "change_pct": chg,
            "leading_stock": p[12] if len(p) > 12 else p[-1],
            "leading_change": lead_chg,
            "leading_code": p[8] if len(p) > 8 else None,
        })
    if not rows:
        raise RuntimeError("新浪行业板解析为空")
    return rows


def get_sectors(cfg: dict, data_date: str = None) -> dict:
    top_n = cfg["sectors"].get("top_n", 15)
    boards = cfg["sectors"]["boards"]
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_tag = datetime.now().strftime("%Y%m%d")
    if not data_date:
        data_date = datetime.now().strftime("%Y-%m-%d")

    result = {"section": "sectors", "date": data_date, "boards": []}
    for b in boards:
        board_out = None
        # 1) 东财主源
        try:
            board_out = _try_eastmoney_board(b, top_n)
            board_out["date"] = data_date
            log.info("板块 %s: 东财成功, 共%d", b["name"], board_out["total"])
        except Exception as e:  # noqa
            log.warning("板块 %s 东财失败: %s", b["name"], e)
        # 2) 行业板新浪兜底
        if board_out is None and b["key"] == "industry":
            try:
                rows = _fetch_sina_industry()
                gain = sorted([x for x in rows if x["change_pct"] >= 0],
                              key=lambda x: x["change_pct"], reverse=True)
                loss = sorted([x for x in rows if x["change_pct"] < 0],
                              key=lambda x: x["change_pct"])
                board_out = {"key": b["key"], "name": b["name"], "total": len(rows),
                             "top_gain": gain[:top_n], "top_loss": loss[:top_n],
                             "source": "sina", "date": data_date}
                log.info("板块 %s: 新浪兜底成功, 共%d", b["name"], len(rows))
            except Exception as e2:  # noqa
                log.warning("板块 %s 新浪兜底也失败: %s", b["name"], e2)
        # 3) 仍失败
        if board_out is None:
            board_out = {"key": b["key"], "name": b["name"], "date": data_date,
                         "error": "东财限流且兜底源暂不可用，稍后重试（明早定时不受影响）"}
        result["boards"].append(board_out)

    try:
        with open(cache_dir / f"sectors_{today_tag}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass
    return result


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_sectors(cfg)
    for b in r["boards"]:
        src = b.get("source", "ERROR")
        if "error" in b:
            print(b["name"], f"[{src}] ERR", b["error"]); continue
        print(f"== {b['name']} [{src}] 共{b['total']} ==")
        for x in b.get("top_gain", [])[:5]:
            lead = x.get("leading_stock")
            extra = f" 领涨={lead}" if lead else ""
            print(f"  {x['name']} {x['change_pct']:+.2f}%{extra}")
