"""美股模块：板块涨跌（GICS 行业 ETF 代理）、微软/英伟达/阿里个股涨跌、当日新闻。

数据源：
- 行情：yfinance（自动处理 Yahoo Finance cookie/crumb 认证，云端不被 429 封禁）
- 新闻：yfinance .news（内置，无需额外 API key）

板块用覆盖 AI/半导体/新能源/生物科技等热门赛道的详细主题 ETF 作涨跌代理，按涨跌幅排名。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

from lib import common

# 详细主题/概念 ETF：覆盖 AI、半导体、新能源、生物科技等热门赛道
SECTOR_ETFS = [
    # 科技细分
    ("SOXX", "半导体", "Semiconductors"),
    ("SMH",  "芯片制造", "Chip Manufacturing"),
    ("SOXQ", "费城半导体", "PHLX Semis"),
    ("WCLD", "云计算", "Cloud Computing"),
    ("BOTZ", "机器人/AI", "Robotics & AI"),
    ("AIQ",  "人工智能", "AI & Big Data"),
    ("ROBO", "机器人自动化", "Robotics Automation"),
    ("HACK", "网络安全", "Cybersecurity"),
    ("CIBR", "网络安全2", "Cybersecurity 2"),
    ("IGV",  "软件", "Software"),
    ("SKYY", "云软件", "Cloud Software"),
    # 半导体供应链
    ("IPAX", "半导体设备", "Semis Equipment"),
    ("FTXL", "科技精选", "Tech Select"),
    # 清洁能源/新能源
    ("ICLN", "清洁能源", "Clean Energy"),
    ("TAN",  "太阳能", "Solar Energy"),
    ("FAN",  "风能", "Wind Energy"),
    ("LIT",  "锂电/电池", "Lithium & Battery"),
    ("DRIV", "电动车", "EV & Driving"),
    ("KARS", "电动汽车2", "EV 2"),
    # 生物科技/医疗
    ("XBI",  "生物科技", "Biotech"),
    ("IBB",  "生物制药", "BioPharma"),
    ("ARKG", "基因组学", "Genomics"),
    ("IHI",  "医疗器械", "Medical Devices"),
    # 金融/加密
    ("FINX", "金融科技", "FinTech"),
    ("BKCH", "区块链", "Blockchain"),
    ("BLOK", "区块链2", "Blockchain 2"),
    # 消费/其他
    ("ONLN", "在线零售", "Online Retail"),
    ("ESPO", "游戏", "Video Games"),
    ("META", "元宇宙", "Metaverse"),
    ("UFO",  "航天", "Space"),
    ("JETS", "航空", "Airlines"),
    ("XME",  "金属矿业", "Metals & Mining"),
    ("REMX", "稀土", "Rare Earth"),
    ("MOO",  "农业", "Agriculture"),
    ("PHO",  "水资源", "Water"),
]

DEFAULT_TRACKED = [
    {"symbol": "MSFT", "name": "微软", "news_query": "Microsoft stock"},
    {"symbol": "NVDA", "name": "英伟达", "news_query": "NVIDIA stock"},
    {"symbol": "BABA", "name": "阿里巴巴", "news_query": "Alibaba stock"},
]

_ET = ZoneInfo("America/New_York")


def _fetch_yahoo_quotes(symbols: list[str]) -> dict | None:
    """用 yfinance 批量取行情，自动处理 Yahoo cookie/crumb，云端不被 429 封。"""
    try:
        tickers = yf.Tickers(" ".join(symbols))
        out: dict = {}
        for sym in symbols:
            try:
                t = tickers.tickers[sym.upper()]
                info = t.fast_info
                last = getattr(info, "last_price", None)
                prev = getattr(info, "previous_close", None)
                if last is None or prev is None:
                    continue
                chg = last - prev
                pct = chg / prev * 100 if prev else 0
                out[sym.upper()] = {
                    "symbol": sym.upper(),
                    "price": round(float(last), 2),
                    "prev_close": round(float(prev), 2),
                    "change": round(float(chg), 2),
                    "change_pct": round(float(pct), 2),
                    "currency": getattr(info, "currency", "USD"),
                    "name": sym.upper(),
                    "market_cap": getattr(info, "market_cap", None),
                    "market_time": int(time.time()),
                }
            except Exception as e:
                common.log.warning("yfinance(%s) 失败: %s", sym, e)
        return out or None
    except Exception as e:
        common.log.warning("yfinance 批量请求失败: %s", e)
        return None


def _news_yfinance(symbol: str, limit: int = 6) -> list:
    """用 yfinance 取个股新闻，内置，无需额外 API key。"""
    try:
        t = yf.Ticker(symbol)
        items = t.news or []
        out = []
        for item in items[:limit]:
            content = item.get("content") or {}
            title = content.get("title") or item.get("title") or ""
            link = (content.get("canonicalUrl") or {}).get("url") or item.get("link") or ""
            pub = content.get("pubDate") or item.get("providerPublishTime") or ""
            summary = re.sub(r"<[^>]+>", "", content.get("summary") or "")[:160]
            out.append({
                "title": title,
                "link": link,
                "published": str(pub),
                "published_cn": pub[:16] if isinstance(pub, str) else "",
                "summary": summary,
            })
        return out
    except Exception as e:
        common.log.warning("yfinance news(%s) 失败: %s", symbol, e)
        return []


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
        news[sym] = _news_yfinance(sym)
    result["news"] = news

    result["ok"] = bool(stocks) or any(news.values())
    if not result["ok"]:
        result["error"] = "美股行情与新闻均获取失败。"
    elif not result["quotes_ok"]:
        result["error"] = "行情价格暂缺（Yahoo 限流），新闻正常获取；价格将于恢复后补全。"
    return result
