"""缠论日线 A股机会筛选。

数据源：akshare stock_zh_a_daily（新浪日线，前复权 qfq）。
方法：复用 lib.chan 标准缠论引擎（笔→中枢→MACD 背驰）对每个标的的日线做确定性买卖点判定，
      不依赖大模型、成本低、可复现。按 score 排序输出机会列表（买/卖点 + 信号价 + 可操作理由）。

说明：本模块与期货缠论(30分钟/1小时)同源引擎，但作用于 A股日线级别，作为「日线级别」的
      中长线买卖点参考（一买/二买低吸、一卖/二卖止盈）。日线信号天然滞后于盘中，
      可操作性落到「次日倾向 + 参考位」。
"""
from __future__ import annotations
import json
import os
import glob
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from bisect import bisect_right

from . import common
from . import chan as _chan

log = common.log
TZ_CN = timezone(timedelta(hours=8))


def _clear_proxy() -> Dict[str, str]:
    """临时清除代理环境变量（本机透明代理会导致 akshare 部分接口失败）。"""
    saved = {}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        v = os.environ.pop(k, None)
        if v is not None:
            saved[k] = v
    return saved


def _restore_proxy(saved: Dict[str, str]) -> None:
    for k, v in saved.items():
        os.environ[k] = v


def _prefix(code: str) -> str:
    """6 位代码补全交易所前缀：6 开头→sh，0/3 开头→sz。"""
    code = str(code).strip()
    if code.lower().startswith(("sh", "sz")):
        return code.lower()
    if not code.isdigit():
        return code
    if code[0] == "6":
        return "sh" + code
    if code[0] in ("0", "3"):
        return "sz" + code
    return code  # 其他（如 8/4 北交所）原样，由接口决定是否支持


def _fetch_factor(sym: str, adjust: str) -> List[tuple]:
    """sina qfq/hfq 因子（eval 解析，无 V8，线程安全）。返回按日期升序的 [(date, factor), ...]。"""
    if adjust == "hfq":
        url = "https://finance.sina.com.cn/realstock/company/%s/hfq.js" % sym
    else:
        url = "https://finance.sina.com.cn/realstock/company/%s/qfq.js" % sym
    r = requests.get(url, timeout=20)
    txt = r.text.split("=")[1].split("\n")[0]
    d = eval(txt)  # noqa: sina 返回的因子文件是纯字典字面量，akshare 同款处理
    pairs = []
    for it in d.get("data", []):
        dd = it.get("d")
        ff = it.get("f")
        if not dd or str(dd).startswith("1900"):
            continue
        pairs.append((str(dd)[:10], float(ff)))
    pairs.sort(key=lambda x: x[0])
    return pairs


def fetch_daily(code: str, bars: int = 260, adjust: str = "qfq", clear_proxy: bool = True) -> List[Dict[str, Any]]:
    """取 A股日线（前复权 qfq）。直接调 sina KLine API + qfq 因子，绕过 akshare 的 V8(mini_racer) 解析，
    以便多线程并发扫描（V8 非线程安全会崩）。clear_proxy 仅兼容顺序调用场景。"""
    sym = _prefix(code)
    saved = _clear_proxy() if clear_proxy else {}
    try:
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        r = requests.get(url, params={"symbol": sym, "scale": 240, "ma": "no",
                                      "datalen": bars}, timeout=20)
        r.raise_for_status()
        arr = r.json()
        if not isinstance(arr, list) or not arr:
            raise RuntimeError(f"{code}({sym}) 无日线数据")
        rows = []
        for it in arr:
            try:
                rows.append({
                    "date": str(it.get("day", ""))[:10],
                    "open": float(it["open"]), "high": float(it["high"]),
                    "low": float(it["low"]), "close": float(it["close"]),
                    "volume": float(it.get("volume", 0) or 0),
                })
            except Exception:  # noqa
                continue
        rows.sort(key=lambda x: x["date"])
        rows = rows[-bars:]
        if adjust in ("qfq", "hfq") and rows:
            pairs = _fetch_factor(sym, adjust)
            if pairs:
                dates = [p[0] for p in pairs]
                factors = [p[1] for p in pairs]
                for row in rows:
                    i = bisect_right(dates, row["date"]) - 1
                    f = factors[i] if i >= 0 else 1.0
                    if adjust == "qfq":
                        for k in ("open", "high", "low", "close"):
                            row[k] = round(row[k] / f, 3)
                    else:
                        for k in ("open", "high", "low", "close"):
                            row[k] = round(row[k] * f, 3)
        return rows
    finally:
        if clear_proxy:
            _restore_proxy(saved)


# ---------------- 全市场扫描辅助 ----------------
def _signal_age_bars(data: List[Dict[str, Any]], signal_date: str):
    """信号形成日距离最后一根K线的交易天数（0=最后一根）。无信号/未匹配返回 None。"""
    if not signal_date:
        return None
    for i in range(len(data) - 1, -1, -1):
        if data[i]["date"] == signal_date:
            return len(data) - 1 - i
    return None


def _rec_from_info(code: str, name: str, data: List[Dict[str, Any]], info: dict) -> dict:
    """把引擎返回的 info 整理为统一记录（含 signal_date / signal_age）。"""
    sd = info.get("signal_date", "")
    age = _signal_age_bars(data, sd)
    return {
        "code": code, "name": name,
        "date": data[-1]["date"] if data else "",
        "last_close": info.get("last_close"),
        "trend": info.get("trend"),
        "signal": info.get("signal"),
        "point": info.get("point"),
        "bias": info.get("bias"),
        "signal_price": info.get("signal_price"),
        "signal_date": sd,
        "signal_age": age,
        "reason": info.get("reason"),
        "score": info.get("score", 0) or 0,
        "fresh": info.get("fresh", False),
        "has_signal": info.get("signal") in ("buy", "sell"),
    }


def _dedup_buys(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按股票去重（每只保留信号最新/最具操作性的买点），并排序：一买优先、fresh 优先、信号更新优先。"""
    best = {}
    for x in recs:
        c = x.get("code")
        cur = best.get(c)
        age = x.get("signal_age") if x.get("signal_age") is not None else 99
        if cur is None or age < (cur.get("signal_age") if cur.get("signal_age") is not None else 99):
            best[c] = x
    out = list(best.values())
    out.sort(key=lambda x: (0 if str(x.get("point", "")).startswith("一买") else 1,
                            x.get("fresh") is not True,
                            -(x.get("score", 0)),
                            x.get("signal_age") if x.get("signal_age") is not None else 99))
    return out


def _recs_from_buy_signals(code: str, name: str, data: List[Dict[str, Any]],
                           signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 scan_buy_signals 返回的多个买点信号，整理为统一记录（含 signal_age）。"""
    recs = []
    for s in signals:
        sd = s.get("signal_date", "")
        age = _signal_age_bars(data, sd)
        recs.append({
            "code": code, "name": name,
            "date": data[-1]["date"] if data else "",
            "last_close": data[-1]["close"] if data else None,
            "trend": "下跌",
            "signal": "buy", "point": s.get("point"),
            "bias": s.get("bias"), "signal_price": s.get("signal_price"),
            "signal_date": sd, "signal_age": age,
            "reason": s.get("reason"), "score": s.get("score", 0) or 0,
            "fresh": s.get("fresh", False), "has_signal": True,
        })
    return recs


def _analyze_buys(code: str, name: str, bars: int, adjust: str, clear_proxy: bool) -> dict:
    """单标的买点扫描（供线程池调用）：遍历全部底背驰结构，返回 {buy_signals:[recs], ...}。
    带一次重试。clear_proxy=False 时假定代理已全局清除。"""
    last_err = ""
    for attempt in range(2):
        try:
            data = fetch_daily(code, bars=bars, adjust=adjust, clear_proxy=clear_proxy)
            if len(data) < 40:
                return {"code": code, "name": name,
                        "error": f"日线仅 {len(data)} 根（需≥40）"}
            signals = _chan.scan_buy_signals(data, last_close=data[-1]["close"])
            recs = _recs_from_buy_signals(code, name, data, signals)
            # 回测验证信号质量（借鉴「缠论选股」技能）：信号日次开盘买入、持有 20 日
            bt = _chan.backtest_buy_signals(data, signals, hold_days=20)
            return {"code": code, "name": name,
                    "date": data[-1]["date"], "last_close": data[-1]["close"],
                    "buy_signals": recs, "backtest": bt}
        except Exception as e:  # noqa
            last_err = str(e)[:120]
            time.sleep(0.6)
    return {"code": code, "name": name, "error": last_err}


def _fetch_sina_spot_codes() -> List[tuple]:
    """用 sina 的 hs_a 节点分页拉全部 A股代码+名称（本环境 eastmoney/sse/szse 列表接口超时，
    sina 单标的日线与列表接口可用）。返回 [(code6, name), ...]，code 已去 sh/sz/bj 前缀。"""
    import requests
    base = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData")
    cnt_url = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeStockCount?node=hs_a")
    try:
        total = int(requests.get(cnt_url, timeout=20).text.strip().strip('"'))
    except Exception:
        total = 5600
    pages = (total + 79) // 80
    out: List[tuple] = []
    for page in range(1, pages + 1):
        for attempt in range(4):
            try:
                r = requests.get(base, params={"page": page, "num": 80, "sort": "symbol",
                                               "asc": 1, "node": "hs_a", "symbol": "",
                                               "_s_r_a": "page"}, timeout=20)
                data = r.json()
                if isinstance(data, list) and data:
                    for it in data:
                        sym = str(it.get("symbol", ""))
                        name = str(it.get("name", ""))
                        if len(sym) > 2 and sym[2:].isdigit():
                            out.append((sym[2:], name))
                    break
            except Exception as e:  # noqa
                if attempt == 3:
                    log.warning("sina 列表第 %d 页失败，跳过: %s", page, str(e)[:60])
                time.sleep(1.5)
    seen = set()
    uniq = []
    for c, n in out:
        if c not in seen:
            seen.add(c)
            uniq.append((c, n))
    return uniq


def _all_a_codes(include_st: bool = True, include_bse: bool = False) -> List[tuple]:
    """返回全市场 A股 (code, name) 列表（sina hs_a 节点，本环境可用的列表源）。
    当日清单缓存在 cache/a_codes_YYYYMMDD.json，避免每次重复拉取（约 2-3 分钟）。"""
    cache_dir = common.ROOT / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(TZ_CN).strftime("%Y%m%d")
    cache_path = cache_dir / f"a_codes_{tag}.json"
    if cache_path.exists():
        try:
            codes = json.loads(cache_path.read_text(encoding="utf-8"))
            log.info("复用缓存的 A股代码清单(%d只)", len(codes))
        except Exception:  # noqa
            codes = None
    else:
        codes = None
    if codes is None:
        codes = _fetch_sina_spot_codes()
        try:
            cache_path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa
            pass
    out = []
    for c, n in codes:
        if not include_st and "ST" in n.upper():
            continue
        # 北交所（8/4/9 开头，sina 中为 bj9xxxxx 等）默认剔除（沪深A股为主）
        if not include_bse and c and c[0] in ("8", "4", "9"):
            continue
        out.append((c, n))
    return out


def _analyze_one(code: str, name: str, bars: int, adjust: str, clear_proxy: bool) -> dict:
    """单标的分析（供线程池调用）。clear_proxy=False 时假定代理已全局清除。带一次重试。"""
    last_err = ""
    for attempt in range(2):
        try:
            data = fetch_daily(code, bars=bars, adjust=adjust, clear_proxy=clear_proxy)
            if len(data) < 40:
                return {"code": code, "name": name,
                        "error": f"日线仅 {len(data)} 根（需≥40）"}
            info = _chan.analyze(data)
            return _rec_from_info(code, name, data, info)
        except Exception as e:  # noqa
            last_err = str(e)[:120]
            time.sleep(0.6)
    return {"code": code, "name": name, "error": last_err}


def _agg_backtest(result: dict, bt_list: list) -> None:
    """汇总逐股回测结果，给出全市场信号质量（胜率 / 平均收益）。"""
    if not bt_list:
        result["backtest_agg"] = {"trades": 0, "win_rate": 0.0,
                                  "avg_return": 0.0, "stocks": 0}
        return
    total_trades = sum(b["trades"] for b in bt_list)
    total_wins = sum(b["win_count"] for b in bt_list)
    # 按交易笔数加权的平均收益，更贴近真实资金曲线
    wsum = sum(b["trades"] * b["avg_return"] for b in bt_list)
    avg_ret = wsum / total_trades if total_trades else 0.0
    result["backtest_agg"] = {
        "trades": total_trades,
        "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0.0,
        "avg_return": round(avg_ret, 2),
        "stocks": len(bt_list),
    }


def _save_scan_cache(cfg: dict, result: dict) -> None:
    try:
        cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        tag = datetime.now(TZ_CN).strftime("%Y%m%d")
        with open(cache_dir / f"chanlun_a_scan_{tag}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass


def scan_all_a_buy(cfg: dict, max_workers: int = 8, within_trading_days: int = 5,
                   limit: int = None, include_st: bool = True, include_bse: bool = False,
                   name_filter: str = None, resume: bool = True) -> dict:
    """全市场 A股并发扫描：只保留「日线级别缠论买点 + 信号形成于近 within_trading_days 个交易日」。

    - 全局清一次代理，worker 内不再各自清（避免多线程竞态）。
    - 失败标的自动跳过并计数；结果周期性落盘，长任务中途被杀也能保留部分进度。
    - resume=True 时，若今日已有扫描缓存，则跳过其中已分析的代码，只补扫剩余的（断点续扫）。
    """
    ca = (cfg.get("chanlun_a") or {})
    bars = int(ca.get("lookback_bars", 260))
    adjust = ca.get("adjust", "qfq")
    saved = _clear_proxy()
    # 防止个别 sina 请求无限挂起拖垮整轮扫描
    import socket
    _orig_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    try:
        codes = _all_a_codes(include_st=include_st, include_bse=include_bse)
    except Exception as e:  # noqa
        _restore_proxy(saved)
        return {"ok": False, "error": f"获取全市场清单失败: {e}", "all": [],
                "opportunities": [], "count_total": 0, "count_ok": 0, "count_buy": 0,
                "count_fail": 0}

    if name_filter:
        codes = [(c, n) for c, n in codes if name_filter in n or name_filter in c]
    if limit:
        codes = codes[:limit]

    # ---- 断点续扫：加载最近一次已有扫描缓存，跳过已分析代码（跨天也生效）----
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    tag = datetime.now(TZ_CN).strftime("%Y%m%d")
    cache_path = cache_dir / f"chanlun_a_scan_{tag}.json"
    if not cache_path.exists():
        # 当天缓存不存在时，找最近一次扫描缓存（可能跨天，用于被杀后继续）
        cands = sorted(glob.glob(str(cache_dir / "chanlun_a_scan_*.json")))
        if cands:
            cache_path = Path(cands[-1])
    prev_done = {}  # code -> record（已分析过的，直接复用）
    if resume and cache_path.exists():
        try:
            prev = json.loads(cache_path.read_text(encoding="utf-8"))
            for r in prev.get("all", []):
                c = r.get("code")
                # 仅复用「新版买点扫描」缓存（含 buy_signals 列表）；旧格式缓存不复用，强制重扫
                if c and isinstance(r.get("buy_signals"), list):
                    prev_done[c] = r
            if prev_done:
                log.info("断点续扫：复用缓存(%s)中已分析的 %d 只，仅补扫剩余代码",
                         cache_path.name, len(prev_done))
        except Exception:  # noqa
            prev_done = {}
    if prev_done:
        codes = [(c, n) for c, n in codes if c not in prev_done]

    result = {"ok": True, "mode": "all_market_buy", "date": "",
              "count_total": len(codes) + len(prev_done), "count_ok": 0,
              "count_buy": 0, "count_fail": 0,
              "opportunities": [], "all": [],
              "params": {"max_workers": max_workers,
                         "within_trading_days": within_trading_days,
                         "include_st": include_st, "include_bse": include_bse,
                         "resume": bool(prev_done)}}

    buy_recs = []  # 窗口内全部买点记录（可能同股票多条）
    bt_list = []    # 逐股回测结果（用于汇总信号质量）

    # 先把已分析的（续扫部分）合并进去
    for rec in prev_done.values():
        result["all"].append(rec)
        if rec.get("error"):
            result["count_fail"] += 1
        else:
            result["count_ok"] += 1
            bt = rec.get("backtest")
            if bt and bt.get("trades"):
                bt_list.append(bt)
            for bs in rec.get("buy_signals", []):
                if bs.get("signal_age") is not None and bs["signal_age"] <= within_trading_days:
                    buy_recs.append(bs)

    def _work(cn):
        return _analyze_buys(cn[0], cn[1], bars, adjust, False)

    total_plan = result["count_total"]
    if codes:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_work, cn): cn for cn in codes}
            done = 0
            for fut in as_completed(futs):
                done += 1
                r = fut.result()
                result["all"].append(r)
                if r.get("error"):
                    result["count_fail"] += 1
                    continue
                result["count_ok"] += 1
                bt = r.get("backtest")
                if bt and bt.get("trades"):
                    bt_list.append(bt)
                for bs in r.get("buy_signals", []):
                    age = bs.get("signal_age")
                    if age is not None and age <= within_trading_days:
                        buy_recs.append(bs)
                scanned = result["count_ok"] + result["count_fail"]
                if done % 250 == 0 or done == len(codes):
                    log.info("A股全市场缠论扫描进度 %d/%d（累计已分析 %d/%d，窗口内买点 %d）",
                             done, len(codes), scanned, total_plan,
                             len(buy_recs))
                    _save_scan_cache(cfg, result)

    _restore_proxy(saved)
    socket.setdefaulttimeout(_orig_timeout)
    # 按股票去重（每只保留最新买点），并排序：一买优先、fresh 优先、信号更新优先
    result["opportunities"] = _dedup_buys(buy_recs)
    result["count_buy"] = len(result["opportunities"])      # 去重后标的数
    result["count_buy_signals"] = len(buy_recs)             # 窗口内全部买点信号数
    result["count_fresh"] = sum(1 for x in buy_recs if x.get("fresh"))
    # 信号质量回测汇总（借鉴「缠论选股」技能）：逐股「信号日次开盘买入、持有20日」胜率
    _agg_backtest(result, bt_list)
    dates = [r.get("date", "") for r in result["all"] if r.get("date")]
    if dates:
        result["date"] = max(dates)
    _save_scan_cache(cfg, result)
    log.info("A股全市场缠论买点扫描完成：%d 只扫描，%d 成功，%d 失败，命中近%d日买点 %d 个",
             result["count_total"], result["count_ok"], result["count_fail"],
             within_trading_days, result["count_buy"])
    return result


def get_chanlun_a(cfg: dict) -> dict:
    ca = (cfg.get("chanlun_a") or {})
    if not ca.get("enabled", True):
        return {"ok": False, "disabled": True, "count_total": 0,
                "count_ok": 0, "opportunities": [], "all": [], "date": ""}
    top_n = int(ca.get("top_n", 20))
    bars = int(ca.get("lookback_bars", 260))
    adjust = ca.get("adjust", "qfq")
    sig_filter = (ca.get("signal") or "both").lower()
    stocks = ca.get("stocks") or []
    result = {"ok": True, "date": "", "count_total": len(stocks),
              "count_ok": 0, "opportunities": [], "all": []}
    for s in stocks:
        code = s.get("code")
        name = s.get("name", code)
        try:
            data = fetch_daily(code, bars=bars, adjust=adjust)
            if len(data) < 40:
                result["all"].append({"code": code, "name": name,
                                      "error": f"日线仅 {len(data)} 根（需≥40）"})
                continue
            info = _chan.analyze(data)
            rec = _rec_from_info(code, name, data, info)
            result["count_ok"] += 1
            result["all"].append(rec)
            if rec["has_signal"] and (sig_filter == "both" or sig_filter == rec["signal"]):
                result["opportunities"].append(rec)
            log.info("A股缠论 %s(%s) %s 信号=%s 分=%s",
                     code, name, data[-1]["date"], rec["signal"], rec["score"])
        except Exception as e:  # noqa
            result["all"].append({"code": code, "name": name, "error": str(e)[:80]})
            log.warning("A股缠论 %s 失败: %s", code, str(e)[:80])
        time.sleep(0.2)
    # 排序：分数降序；平局把「未兑现(fresh)」排前面
    result["opportunities"] = sorted(
        result["opportunities"],
        key=lambda x: (x.get("score", 0), 1 if x.get("fresh") else 0),
        reverse=True)[:top_n]
    dates = [r.get("date", "") for r in result["all"] if r.get("date")]
    if dates:
        result["date"] = max(dates)
    # 落盘缓存（与 daily_report 的 _cache_path 同 tag 格式，支持 --reuse-cache）
    try:
        cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        tag = datetime.now(TZ_CN).strftime("%Y%m%d")
        with open(cache_dir / f"chanlun_a_{tag}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass
    log.info("A股日线缠论筛选完成：%d/%d 只有效，命中 %d 个信号（筛选方向=%s）",
             result["count_ok"], result["count_total"],
             len(result["opportunities"]), sig_filter)
    return result


if __name__ == "__main__":
    import sys
    from lib import common as _c  # noqa
    cfg = common.load_config()
    r = get_chanlun_a(cfg)
    print(json.dumps({
        "ok": r["ok"], "date": r["date"],
        "count_ok": r["count_ok"], "count_total": r["count_total"],
        "opps": [(x["name"], x["code"], x.get("point"), x.get("signal"), x.get("score"))
                 for x in r["opportunities"]],
    }, ensure_ascii=False, indent=2))
