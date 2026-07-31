"""美股模块：板块涨跌（GICS 行业 ETF 代理）、微软/英伟达/阿里个股涨跌、当日新闻。

数据源（均不依赖被限流的东财）：
- 行情：Yahoo Finance 批量 quote 接口
  (query1/query2.finance.yahoo.com/v7/finance/quote?symbols=...)，单次请求拿全部标的，
  并在 429 时限流退避、双主机互备。
- 新闻：Google News RSS (news.google.com/rss/search?q=<公司关键词>)，免费、无需鉴权、聚合广。

板块用 11 个 GICS 行业 SPDR ETF 作涨跌代理，按涨跌幅排名。
"""
from __future__ import annotations

import email.utils
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from lib import common

# 11 个 GICS 行业 SPDR ETF：作为美股各板块涨跌的代理
SECTOR_ETFS = [
    ("XLK", "科技", "Technology"),
    ("XLV", "医疗健康", "Health Care"),
    ("XLI", "工业", "Industrials"),
    ("XLB", "原材料", "Materials"),
    ("XLY", "可选消费", "Consumer Discretionary"),
    ("XLP", "必需消费", "Consumer Staples"),
    ("XLE", "能源", "Energy"),
    ("XLF", "金融", "Financials"),
    ("XLU", "公用事业", "Utilities"),
    ("XLRE", "房地产", "Real Estate"),
    ("XLC", "通信服务", "Communication Services"),
]

DEFAULT_TRACKED = [
    {"symbol": "MSFT", "name": "微软", "news_query": "Microsoft stock"},
    {"symbol": "NVDA", "name": "英伟达", "news_query": "NVIDIA stock"},
    {"symbol": "BABA", "name": "阿里巴巴", "news_query": "Alibaba stock"},
]

_ET = ZoneInfo("America/New_York")


def _get_json(url: str, tries: int = 4):
    """GET JSON，遇 429 退避重试，遇其它错误短时重试。"""
    hdr = {"User-Agent": common.UA, "Accept": "application/json"}
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status != 200:
                    if r.status == 429:
                        time.sleep(6 + i * 4)
                        last = RuntimeError("HTTP 429")
                        continue
                    raise RuntimeError(f"HTTP {r.status}")
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:  # noqa
            last = e
            if "429" not in str(e):
                time.sleep(1.5)
    raise last or RuntimeError(f"GET {url} failed")


def _fetch_yahoo_chart(symbols: list[str]) -> dict | None:
    """兜底：逐标的用 chart 接口取近 5 日日线，算最新涨跌。"""
    out: dict = {}
    for sym in symbols:
        try:
            d = _get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                f"?range=5d&interval=1d", tries=2)
            res = (d.get("chart") or {}).get("result") or []
            if not res:
                continue
            meta = res[0]["meta"]
            closes = [c for c in res[0]["indicators"]["quote"][0]["close"] if c is not None]
            if len(closes) < 2:
                continue
            prev, last = closes[-2], closes[-1]
            chg, pct = last - prev, (last - prev) / prev * 100
            out[sym.upper()] = {
                "symbol": sym.upper(),
                "price": round(float(last), 2),
                "prev_close": round(float(prev), 2),
                "change": round(float(chg), 2),
                "change_pct": round(float(pct), 2),
                "currency": meta.get("currency", "USD"),
                "name": meta.get("shortName") or sym,
                "market_cap": meta.get("marketCap"),
                "market_time": int(meta.get("regularMarketTime", 0)),
            }
        except Exception as e:  # noqa
            common.log.warning("Yahoo chart(%s) 失败: %s", sym, e)
    return out or None


def _fetch_yahoo_quotes(symbols: list[str]) -> dict | None:
    """批量取行情：query1/query2 的 /v7/finance/quote 均失败后，兜底用 chart 接口。"""
    syms = ",".join(symbols)
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            d = _get_json(f"https://{host}/v7/finance/quote?symbols={syms}")
            res = (d.get("quoteResponse") or {}).get("result") or []
            if res:
                out = {}
                for q in res:
                    sym = (q.get("symbol") or "").upper()
                    prev = q.get("regularMarketPreviousClose")
                    last = q.get("regularMarketPrice")
                    chg_pct = q.get("regularMarketChangePercent")
                    if last is None or prev is None:
                        continue
                    out[sym] = {
                        "symbol": sym,
                        "price": round(float(last), 2),
                        "prev_close": round(float(prev), 2),
                        "change": round(float(last) - float(prev), 2),
                        "change_pct": round(float(chg_pct), 2) if chg_pct is not None else None,
                        "currency": q.get("currency", "USD"),
                        "name": q.get("shortName") or sym,
                        "market_cap": q.get("marketCap"),
                        "market_time": int(q.get("regularMarketTime", 0)),
                    }
                if out:
                    return out
        except Exception as e:  # noqa
            common.log.warning("Yahoo quote(%s) 失败: %s", host, e)
    # 兜底：chart 端点（批量 quote 被限流时仍可能可用）
    common.log.warning("批量 quote 接口失败，尝试 chart 端点兜底")
    return _fetch_yahoo_chart(symbols)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _news_google(query: str, trade_date: datetime.date, limit: int = 6) -> list:
    """Google News RSS 取个股/公司新闻，过滤到最新美股交易日（含盘后）。"""
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": common.UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            xml = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa
        common.log.warning("Google News(%s) 获取失败: %s", query, e)
        return []
    try:
        root = ET.fromstring(xml)
    except Exception as e:  # noqa
        common.log.warning("Google News(%s) XML 解析失败: %s", query, e)
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        desc = _strip_html(it.findtext("description") or "")
        dt = None
        if pub:
            try:
                dt = email.utils.parsedate_to_datetime(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(_ET)
            except Exception:
                dt = None
        out.append({
            "title": title,
            "link": link,
            "published": pub,
            "published_cn": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
            "et_date": dt.date().isoformat() if dt else "",
            "summary": desc[:160],
        })
    # 只保留交易日当天及以后（含盘后），按时间倒序
    recent = [n for n in out if n["et_date"] and n["et_date"] >= trade_date.isoformat()]
    if len(recent) < 3:
        recent = sorted(out, key=lambda x: x["published"] or "", reverse=True)[:limit]
    seen = set()
    uniq = []
    for n in recent:
        if n["title"] in seen:
            continue
        seen.add(n["title"])
        uniq.append(n)
        if len(uniq) >= limit:
            break
    return uniq


def get_us_stocks(cfg: dict, data_date=None) -> dict:
    us_cfg = cfg.get("us_stocks", {}) or {}
    tracked = us_cfg.get("tracked") or DEFAULT_TRACKED

    result = {
        "ok": False,
        "quotes_ok": False,
        "date": None,
        "date_label": "",
        "sectors": [],
        "sector_source": "yahoo",
        "sector_date": None,
        "stocks": [],
        "news": {},
        "error": None,
    }

    # 所有需要行情的代码：板块 ETF + 个股
    sector_syms = [s[0] for s in SECTOR_ETFS]
    stock_syms = [t["symbol"].upper() for t in tracked]
    all_syms = sector_syms + stock_syms

    quotes = _fetch_yahoo_quotes(all_syms)
    if not quotes:
        # Yahoo 临时限流(429)通常会在一两分钟内恢复，睡眠后重试一次
        common.log.warning("美股行情首次获取失败，45s 后重试一次...")
        time.sleep(45)
        quotes = _fetch_yahoo_quotes(all_syms)
    result["quotes_ok"] = bool(quotes)

    # —— 板块 ——
    sectors = []
    if quotes:
        for sym, cn, en in SECTOR_ETFS:
            q = quotes.get(sym)
            if q:
                sectors.append({
                    "symbol": sym, "name_cn": cn, "name_en": en,
                    "price": q["price"], "change": q["change"],
                    "change_pct": q["change_pct"], "currency": q["currency"],
                })
    sectors.sort(key=lambda x: (x.get("change_pct") or 0), reverse=True)
    result["sectors"] = sectors

    # —— 个股（始终按 config 构建；行情缺失时价格留空，新闻不受影响）——
    stocks = []
    trade_ts = 0
    for t in tracked:
        sym = t["symbol"].upper()
        nq = t.get("news_query", f"{sym} stock")
        q = quotes.get(sym) if quotes else None
        if q:
            trade_ts = max(trade_ts, q["market_time"])
            stocks.append({
                "symbol": sym,
                "name_cn": t.get("name", ""),
                "name_en": q["name"],
                "price": q["price"],
                "change": q["change"],
                "change_pct": q["change_pct"],
                "currency": q["currency"],
                "market_cap": q["market_cap"],
                "news_query": nq,
            })
        else:
            stocks.append({
                "symbol": sym,
                "name_cn": t.get("name", ""),
                "name_en": "",
                "price": None, "change": None, "change_pct": None,
                "currency": "USD", "market_cap": None, "news_query": nq,
            })
    result["stocks"] = stocks

    # 交易日（ET）
    if trade_ts:
        trade_dt = datetime.fromtimestamp(trade_ts, tz=timezone.utc).astimezone(_ET)
    else:
        trade_dt = datetime.now(tz=_ET) - timedelta(days=1)
    result["date"] = trade_dt.date().isoformat()
    wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][trade_dt.weekday()]
    result["date_label"] = f"{trade_dt.date()}（{wk}，美股交易日）"

    # —— 新闻（独立于行情，按 config 抓取）——
    news = {}
    for t in tracked:
        sym = t["symbol"].upper()
        news[sym] = _news_google(t.get("news_query", f"{sym} stock"), trade_dt.date())
    result["news"] = news

    result["ok"] = bool(stocks) or any(news.values())
    if not result["ok"]:
        result["error"] = "美股行情与新闻均获取失败。"
    elif not result["quotes_ok"]:
        result["error"] = "行情价格暂缺（Yahoo 限流），新闻正常获取；价格将于恢复后补全。"
    return result
