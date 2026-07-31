"""期货全市场数据：主力连续行情、持仓量、资金流入(估算)、涨跌幅排名、缠论机会筛选。

数据源：akshare futures_zh_daily_sina（新浪，单品种一次返回完整历史含持仓量）。
资金流入(估算) = (当日持仓量 - 前日持仓量) × 当日收盘价 × 合约乘数。
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from . import common
from . import llm
from . import chan as _chan


log = common.log


def fetch_main_daily(code: str, bars: int = 120) -> List[Dict[str, Any]]:
    """取某品种主力连续日线：[{date,open,high,low,close,volume,open_interest,settle}]"""
    import akshare as ak
    last_err = None
    for sym in (f"{code.lower()}0", f"{code.upper()}0", code.lower(), code.upper()):
        try:
            d = ak.futures_zh_daily_sina(symbol=sym)
            if d is None or len(d) == 0:
                continue
            d = d.rename(columns={"hold": "open_interest"})
            d = d.sort_values("date").tail(bars + 5)
            out = []
            for _, r in d.iterrows():
                try:
                    out.append({
                        "date": str(r["date"])[:10],
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "volume": float(r.get("volume", 0) or 0),
                        "open_interest": float(r.get("open_interest", 0) or 0),
                        "settle": float(r.get("settle", 0) or 0),
                    })
                except Exception:  # noqa
                    continue
            if out:
                return out
        except Exception as e:  # noqa
            last_err = e
            time.sleep(0.3)
    raise RuntimeError(f"无法获取 {code} 行情: {last_err}")


def fetch_main_minute(code: str, period: str = "30", bars: int = 40) -> List[Dict[str, Any]]:
    """取某品种主力连续分钟K线（默认30分钟）：[{datetime,open,high,low,close,volume,hold}]。"""
    import akshare as ak
    last_err = None
    for sym in (f"{code.lower()}0", f"{code.upper()}0", code.lower(), code.upper()):
        try:
            d = ak.futures_zh_minute_sina(symbol=sym, period=period)
            if d is None or len(d) == 0:
                continue
            d = d.sort_values("datetime").tail(bars + 10)
            out = []
            for _, r in d.iterrows():
                try:
                    out.append({
                        "datetime": str(r["datetime"]),
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "volume": float(r.get("volume", 0) or 0),
                        "hold": float(r.get("hold", 0) or 0),
                    })
                except Exception:  # noqa
                    continue
            if out:
                return out
        except Exception as e:  # noqa
            last_err = e
            time.sleep(0.3)
    raise RuntimeError(f"无法获取 {code} 分钟行情: {last_err}")


def _resample(daily: List[Dict[str, Any]], rule: str) -> List[Dict[str, Any]]:
    """把日线 resample 成周线/月线。daily: [{date,open,high,low,close,volume,open_interest}] 升序。"""
    import pandas as pd
    df = pd.DataFrame(daily)
    if df.empty:
        return []
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "open_interest": "last"}
    try:
        r = df.resample(rule).agg(agg)
    except Exception:  # 兼容旧版 pandas 月线别名
        alt = {"ME": "M"}.get(rule, rule)
        r = df.resample(alt).agg(agg)
    r = r.dropna(subset=["close"])
    out = []
    for idx, row in r.iterrows():
        out.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]), "open_interest": float(row["open_interest"]),
        })
    return out


def _sma(vals: List[float], n: int) -> float:
    if not vals:
        return 0.0
    if len(vals) <= n:
        return sum(vals) / len(vals)
    return sum(vals[-n:]) / n


def _realized_volatility(data: List[Dict[str, Any]], n: int = 20) -> float:
    """20 日对数收益率年化波动率（%）。data 为日线升序，至少 n+1 根。"""
    if len(data) < n + 1:
        return 0.0
    import math
    closes = [float(d["close"]) for d in data[-(n + 1):]]
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] <= 0 or closes[i] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 3:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    if var <= 0:
        return 0.0
    return math.sqrt(var) * math.sqrt(252) * 100


def compute_level_structures(tf: dict) -> dict:
    """规则法给出各周期(30m/1h/daily/weekly/monthly)当前结构描述，供多级别联立速览。

    不依赖大模型，确定性、低成本：用收盘价相对长短均线的位置判方向，结合最近摆动与区间。
    """
    out = {}
    for key in ("30m", "1h", "daily", "weekly", "monthly"):
        bars = tf.get(key) or []
        if len(bars) < 2:
            out[key] = "数据不足"
            continue
        closes = [float(b["close"]) for b in bars]
        last = closes[-1]
        n = len(closes)
        ma_long = _sma(closes, min(20, n))
        ma_short = _sma(closes, min(5, n))
        if last > ma_long * 1.0015:
            direction = "多头"
        elif last < ma_long * 0.9985:
            direction = "空头"
        else:
            direction = "震荡"
        diff = closes[-1] - closes[-2]
        swing = "上涨" if diff > 0 else ("下跌" if diff < 0 else "走平")
        window = closes[-min(10, n):]
        lo, hi = min(window), max(window)
        if abs(hi - lo) >= 2:
            rng = f"{lo:.0f}–{hi:.0f}"
        else:
            rng = f"{lo:.2f}–{hi:.2f}"
        if direction == "震荡":
            desc = f"中枢震荡区间{rng}"
        elif ma_short > ma_long:
            desc = f"{direction}，近{min(5, n)}根{swing}"
        else:
            desc = f"{direction}，近{min(5, n)}根{swing}"
        out[key] = desc
    return out


def _rule_chan(tf: dict, report_date: str) -> Dict[str, Any]:
    """用标准缠论引擎(lib/chan)对 30分钟/1小时 K线做确定性买卖点判定，作为大模型兜底。

    返回与渲染兼容的 chan 字典（signal/point/trend/bias/signal_price/reason/has_signal/score），
    仅接受「当天」(与 report_date 同日) 出现的信号；无则返回 None。分钟K线需把 datetime 映射为 date。
    """
    best = None
    for lvl in ("30m", "1h"):
        bars = tf.get(lvl) or []
        if len(bars) < 40:
            continue
        bars2 = [dict(b, date=b.get("datetime") or b.get("date")) for b in bars]
        try:
            info = _chan.analyze(bars2)
        except Exception as e:  # noqa
            common.log.warning("规则缠论 %s 失败: %s", lvl, str(e)[:60])
            continue
        sig = info.get("signal")
        if sig not in ("buy", "sell"):
            continue
        sd = (info.get("signal_date") or "")[:10]
        if report_date and sd and sd != str(report_date)[:10]:
            continue  # 非当天信号，跳过
        cand = {
            "signal": sig, "point": info.get("point"),
            "trend": info.get("trend"), "bias": info.get("bias"),
            "signal_price": info.get("signal_price"),
            "reason": info.get("reason", ""),
            "signal_date": info.get("signal_date"),
            "has_signal": True, "fresh": True,
            "score": info.get("score", 0), "source": "rule",
        }
        if lvl == "30m":
            return cand  # 优先 30 分钟（最贴近当天操作级）
        best = cand
    return best


def _fetch_product(p: dict, bars: int,
                   levels_cfg: dict) -> Tuple[dict, Optional[dict]]:
    """抓取单个期货品种的日线+多级别K线，返回 (row, product_for_llm)。"""
    code, name, mult = p["code"], p["name"], p.get("mult", 10)
    try:
        data = fetch_main_daily(code, bars)
        if len(data) < 2:
            log.warning("期货 %s 数据不足: %d行", code, len(data))
            return {"code": code, "name": name, "error": "数据不足"}, None

        last, prev = data[-1], data[-2]
        change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
        oi = last["open_interest"]
        oi_change = oi - prev["open_interest"]
        oi_change_pct = (oi_change / prev["open_interest"] * 100) if prev["open_interest"] else 0
        inflow = oi_change * last["close"] * mult
        vol_daily = round((last["high"] - last["low"]) / last["close"] * 100, 2) if last["close"] > 0 else 0.0
        vol_5d = round(_realized_volatility(data, n=5), 2)
        row = {
            "code": code, "name": name, "mult": mult,
            "date": last["date"], "last_close": last["close"],
            "change_pct": round(change_pct, 2),
            "open_interest": oi, "oi_change": int(oi_change),
            "oi_change_pct": round(oi_change_pct, 2),
            "volume": last["volume"],
            "inflow": round(inflow, 0), "chan": {},
            "vol_daily": vol_daily,
            "vol_5d": vol_5d,
        }

        product_for_llm: Optional[dict] = None
        try:
            tf: dict = {}
            tf["30m"] = fetch_main_minute(code, period=levels_cfg["30m"]["period"],
                                          bars=levels_cfg["30m"]["bars"])
            tf["1h"] = fetch_main_minute(code, period=levels_cfg["1h"]["period"],
                                         bars=levels_cfg["1h"]["bars"])
            tf["daily"] = data[-levels_cfg["daily"]["bars"]:]
            tf["weekly"] = _resample(data, "W-FRI")[-levels_cfg["weekly"]["bars"]:]
            tf["monthly"] = _resample(data, "ME")[-levels_cfg["monthly"]["bars"]:]
            product_for_llm = {
                "code": code, "name": name, "timeframes": tf,
                "levels_struct": compute_level_structures(tf),
                "change_pct": round(change_pct, 2), "inflow": round(inflow, 0),
                "last_date": last["date"],
            }
            log.info("期货 %s(%s) 多级别K线 30m=%d 1h=%d daily=%d",
                     code, name, len(tf["30m"]), len(tf["1h"]), len(tf["daily"]))
        except Exception as e:  # noqa
            log.warning("期货 %s 多级别行情失败: %s", code, str(e)[:60])

        log.info("期货 %s(%s) %s 涨%.2f%% OI=%.0f 流入=%.0f万",
                 code, name, last["date"], change_pct, oi, inflow / 1e4)
        return row, product_for_llm
    except Exception as e:  # noqa
        log.warning("期货 %s 失败: %s", code, str(e)[:80])
        return {"code": code, "name": name, "error": str(e)[:80]}, None


def get_futures_overview(cfg: dict) -> dict:
    prods = cfg["futures"]["products"]
    top_n = cfg["futures"].get("top_n", 12)
    bars = cfg["futures"].get("chan_history_bars", 120)
    levels_cfg = (cfg["futures"].get("chan_levels") or {
        "30m": {"period": "30", "bars": 40},
        "1h": {"period": "60", "bars": 40},
        "daily": {"bars": 80},
        "weekly": {"bars": 40},
        "monthly": {"bars": 18},
    })
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_tag = datetime.now().strftime("%Y%m%d")

    rows: List[Dict[str, Any]] = []
    products_for_llm: List[Dict[str, Any]] = []

    # 并发抓取54个品种（8线程，控制对新浪的请求速率）
    log.info("并发抓取 %d 个期货品种（8线程）...", len(prods))
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(_fetch_product, p, bars, levels_cfg): p for p in prods}
        for fut in as_completed(future_map):
            try:
                row, pfl = fut.result()
                rows.append(row)
                if pfl:
                    products_for_llm.append(pfl)
            except Exception as e:  # noqa
                p = future_map[fut]
                log.warning("期货 %s 未预期错误: %s", p.get("code"), e)
                rows.append({"code": p["code"], "name": p["name"], "error": str(e)[:80]})

    # ---- 缠论分析：规则引擎先出全部当日信号（快、确定性，覆盖所有品种） ----
    rule_by_code = {}
    for p in products_for_llm:
        rc = _rule_chan(p["timeframes"], p.get("last_date"))
        if rc:
            rule_by_code[p["code"]] = rc
    # ---- 预构建 levels_struct 字典(O(1)查找，替代原 O(n) _lvl_of 闭包) ----
    struct_map = {p["code"]: (p.get("levels_struct") or {}) for p in products_for_llm}

    # ---- 并发调用 LLM 对"规则命中品种"补写多级别解读 ----
    signaled = [p for p in products_for_llm if p["code"] in rule_by_code]
    llm_by_code = {}
    if signaled and llm.is_enabled(cfg):
        log.info("并发调用 LLM 对 %d 个规则命中品种补写缠论解读...", len(signaled))
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures_llm = {}
            for p in signaled:
                code = p["code"]
                rc = rule_by_code[code]
                futures_llm[pool.submit(
                    llm.interpret_chan_signal,
                    cfg, p["name"], code, rc.get("point"), rc.get("signal"),
                    rc.get("trend"), rc.get("bias"), p.get("levels_struct")
                )] = (code, rc)
            for fut in as_completed(futures_llm):
                code, rc = futures_llm[fut]
                try:
                    reason = fut.result()
                    if reason:
                        llm_by_code[code] = {
                            "signal": rc.get("signal"), "point": rc.get("point"),
                            "trend": rc.get("trend"), "bias": rc.get("bias"),
                            "reason": reason
                        }
                except Exception as e:  # noqa
                    log.warning("LLM 补写 %s 解读失败，回退规则: %s", code, e)
    # ---- 合并：信号/买卖点以规则引擎为准，LLM 仅补写 趋势/背驰/理由 叙述 ----
    for r in rows:
        code = r.get("code")
        if code in rule_by_code:
            r["chan"] = dict(rule_by_code[code])
            if code in llm_by_code:
                lc = llm_by_code[code]
                r["chan"]["trend"] = lc.get("trend") or r["chan"].get("trend")
                r["chan"]["bias"] = lc.get("bias") or r["chan"].get("bias")
                r["chan"]["reason"] = lc.get("reason") or r["chan"].get("reason")
        if r.get("chan"):
            r["chan"]["levels"] = struct_map.get(code, {})
            r["chan"]["source"] = "rule+llm" if code in llm_by_code else "rule"
    # ---- 多级别结构速览：按当日涨跌幅前 N 的品种，含各周期联立结构（无论是否有当日信号） ----
    ok_for_struct = sorted([r for r in rows if "error" not in r],
                           key=lambda x: abs(x["change_pct"]), reverse=True)[:12]
    structs = []
    for r in ok_for_struct:
        code = r["code"]
        if code in rule_by_code:
            chan = dict(rule_by_code[code])
            chan["levels"] = struct_map.get(code, {})
            if code in llm_by_code:
                chan["trend"] = llm_by_code[code].get("trend") or chan.get("trend")
                chan["bias"] = llm_by_code[code].get("bias") or chan.get("bias")
                chan["reason"] = llm_by_code[code].get("reason") or chan.get("reason")
        else:
            chan = {"levels": struct_map.get(code, {}), "has_signal": False,
                    "signal": "none", "point": "None"}
        structs.append({"name": r["name"], "code": code,
                        "change_pct": r["change_pct"], "chan": chan})
    log.info("缠论分析完成：规则信号 %d 个，其中 %d 个由 LLM 补写解读，结构速览 %d 个",
             len(rule_by_code), len(llm_by_code), len(structs))

    ok = [r for r in rows if "error" not in r]
    by_change = sorted(ok, key=lambda x: x["change_pct"], reverse=True)
    by_inflow = sorted(ok, key=lambda x: x["inflow"], reverse=True)
    by_volatility = sorted([r for r in ok if (r.get("vol_5d") or 0) > 0],
                           key=lambda x: x["vol_5d"], reverse=True)
    opp = sorted([r for r in ok if r.get("chan", {}).get("score", 0) > 0],
                 key=lambda x: x["chan"]["score"], reverse=True)
    latest_date = max((r["date"] for r in ok), default="")


    result = {
        "section": "futures", "date": latest_date,
        "count_ok": len(ok), "count_total": len(rows),
        "by_change": by_change[:top_n],
        "by_change_desc": list(reversed(by_change))[:top_n],
        "by_inflow": by_inflow[:top_n],
        "by_outflow": list(reversed(by_inflow))[:top_n],
        "by_volatility": by_volatility[:top_n],
        "opportunities": opp, "all": ok,
        "chan_structures": structs,
    }
    try:
        with open(cache_dir / f"futures_{today_tag}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass
    return result


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_futures_overview(cfg)
    print(json.dumps({
        "date": r["date"], "ok": r["count_ok"], "total": r["count_total"],
        "top_change": [(x["name"], x["change_pct"]) for x in r["by_change"][:5]],
        "top_inflow": [(x["name"], round(x["inflow"]/1e8, 2)) for x in r["by_inflow"][:5]],
        "top_volatility": [(x["name"], x["vol_5d"]) for x in r["by_volatility"][:5]],
        "opps": [(x["name"], x["chan"].get("point"), x["chan"].get("score"))
                 for x in r["opportunities"][:8]],
    }, ensure_ascii=False, indent=2))
