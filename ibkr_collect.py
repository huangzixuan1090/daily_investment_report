#!/usr/bin/env python3
"""IBKR 实盘持仓采集（只读）：拉持仓 → 行情快照(昨收价) → 昨日新闻 → 本地 Ollama 中文总结。

运行环境：trading_bot/.venv（含 ib_insync）。
安全：纯只读——connect / positions / reqMktData(行情快照) / disconnect；绝不调用下单或账户修改接口。
用法：
  python ibkr_collect.py --out reports/holdings.json --date 2026-07-15
  python ibkr_collect.py --standalone-html reports/holdings_preview.html --date 2026-07-15
"""
from __future__ import annotations
import sys, ssl, os, json, re, argparse
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    _SH = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa
    _SH = timezone(timedelta(hours=8))

# 仅用于抓公开新闻 RSS（trading_bot/.venv 缺系统根证书）
ssl._create_default_https_context = ssl._create_unverified_context

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/Users/michaelhuang/code/trading_bot/src")
from trader import IBKRTrader  # noqa: E402


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 本地 Ollama（零云端 token） ────────────────────────────────────────────
def ollama_chat(cfg: dict, prompt: str, num_predict: int = 600) -> str:
    llm = cfg.get("llm") or {}
    url = (llm.get("base_url") or "http://localhost:11434").rstrip("/") + "/api/chat"
    model = llm.get("model") or "qwen2.5:7b"
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
        "messages": [
            {"role": "system",
             "content": "你是严谨的中文财经助手，只依据给定材料总结，不编造、不补充未知信息。"},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return (json.loads(r.read().decode("utf-8")).get("message") or {}).get("content", "").strip()
    except Exception as e:
        return f"[本地模型总结失败: {e}]"


# ── 新闻抓取 + 昨日过滤 ──────────────────────────────────────────────────
_JUNK_TITLE = ("META_TITLE_QUOTE", "TITLE_QUOTE", "Yahoo Finance", "‑ Yahoo",
                "Reuters", "‑ Benzinga", "‑ CNBC")


def clean_news(items: list) -> list:
    """去掉 Google News 占位/无信息量标题并去重。"""
    out, seen = [], set()
    for n in items:
        t = n.get("title", "").strip()
        low = t.lower()
        if any(j.lower() in low for j in _JUNK_TITLE):
            continue
        if t in seen:
            continue
        seen.add(t)
        # 去掉标题里挂的「 - 媒体名」尾巴，避免重复噪音
        t_clean = re.sub(r"\s*[—\-–]\s*(Yahoo Finance|Benzinga|CNBC|Reuters|Barron's"
                         r"|Bloomberg|MarketBeat|TradingView|Seeking Alpha|TipRanks"
                         r"|The Motley Fool|24/7 Wall St\.|Trefis|simplywall\.st)\s*$",
                         "", t, flags=re.I).strip()
        n = dict(n); n["title"] = t_clean or t
        out.append(n)
    return out


def build_summary_prompt(sym: str, name_cn: str, name_en: str,
                         data_date: str, news: list) -> str:
    """构建「详细、有洞察」的中文总结 prompt（本地 Ollama 用）。"""
    news_text = "\n".join(
        f"{i+1}. 【{n.get('pub', '')[:16]}】{n['title']}\n   {n['desc'][:400]}"
        for i, n in enumerate(news))
    return (
        f"你是一名严谨的中文财经分析师。以下是股票 {sym}（{name_cn}，{name_en}）"
        f"在 {data_date} 当日的相关新闻（英文标题与摘要原文，按时间排序）。\n\n"
        f"请按以下结构，用**详细、有洞察力**的中文输出，不要写笼统套话：\n\n"
        f"【逐条分析】（对每条新闻分别写）\n"
        f"- 事实概述：这条新闻具体讲了什么？涉及哪些主体、关键数字、时间点？"
        f"（用你自己的话中文复述，不要只机翻标题）\n"
        f"- 影响逻辑：这条消息将通过什么路径影响该公司股价？"
        f"（例如影响哪块业务 / 收入 / 成本 / 监管 / 市场情绪；是一次性事件还是趋势性变化）\n"
        f"- 影响倾向与置信度：利好 / 利空 / 中性，并标注「高 / 中 / 低」置信度，给出具体理由。\n\n"
        f"【综合研判】\n"
        f"- 当日整体市场情绪（偏多 / 偏空 / 分歧），并说明依据。\n"
        f"- 关键催化剂（推动上涨的因素）与关键风险（打压股价的因素）。\n"
        f"- 对持仓者的启示：基于当日信息，下一步最值得关注的信号（不做买卖建议）。\n\n"
        f"严格要求：\n"
        f"- 只依据下面提供的新闻内容；不编造、不引入你已知的外部信息、不预测具体涨跌幅。\n"
        f"- 「事实概述」严格来自新闻标题；若需基于标题做业务推演，请明确标注为「分析推断」。\n"
        f"- 语言专业、具体、有分析深度，避免「通常意味着」「可能带来影响」这类空话堆砌。\n\n"
        f"新闻原文：\n{news_text}"
    )


def fetch_news(query: str, n: int = 12) -> list:
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=en-US&gl=US&ceid=US:en")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    root = ET.fromstring(raw)
    out = []
    for it in root.findall(".//item")[:n]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", it.findtext("description") or "")).strip()
        pub = (it.findtext("pubDate") or "").strip()
        out.append({"title": title, "link": link, "desc": desc, "pub": pub})
    return clean_news(out)


def news_on_date(items: list, data_date: str) -> list:
    """只保留发布日期（转 Asia/Shanghai）等于 data_date 的新闻；无则空。"""
    try:
        target = datetime.strptime(data_date, "%Y-%m-%d").date()
    except Exception:
        return items
    out = []
    for n in items:
        pub = n.get("pub")
        if not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(_SH)
        except Exception:
            continue
        if dt.date() == target:
            out.append(n)
    return out


# ── 行情快照（昨收价）；用户有行情订阅，多数据类型重试 ───────────────────
# 依次尝试：1=实时 / 3=延时 / 4=延时冻结（含上一交易日官方收盘）。
# 若 IBKR API 行情未订阅（Error 10089），再走外部兜底。
def get_market_data(ib, contract, retries: int = 1):
    for mdt in (1, 3, 4):
        for attempt in range(retries):
            try:
                ib.reqMarketDataType(mdt)
                ib.reqMktData(contract, "", False, False)  # 流式（非 snapshot），冻结态可读 close
                ib.sleep(2)
                tk = ib.ticker(contract)
                close = tk.close
                if close is None or close != close:
                    close = tk.last
                if close is None or close != close:
                    close = tk.marketPrice()
            except Exception as e:
                sys.stderr.write(f"  [行情] {contract.symbol} mdt={mdt} 第{attempt+1}次失败: {e}\n")
                close = None
            finally:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
            if close is not None and close == close and close > 0:
                return float(close), f"ibkr(mdt={mdt})"
            ib.sleep(1)
    return None, None


# ── 外部兜底行情（Yahoo Finance，仅用于 IBKR API 行情未订阅时取昨收） ──────
def get_external_close(symbol: str, currency: str):
    ysym = symbol
    if currency != "USD":
        ysym = symbol + ".HK"  # 港股等跨币种：按后缀补充（9606 -> 9606.HK）
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
           f"?interval=1d&range=5d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        meta = json.loads(raw)["chart"]["result"][0]["meta"]
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if prev:
            return float(prev), f"yahoo({ysym})"
    except Exception as e:
        sys.stderr.write(f"  [外部行情] {symbol} 失败: {e}\n")
    return None, None


# ── 美股板块涨跌（11 个 SPDR 行业 ETF，前一交易日）──────────────────────────
# 用 IBKR 历史日线(reqHistoricalData)计算最新已完成交易日的涨跌幅，替代被限流的 Yahoo。
SECTOR_ETFS = [
    ("XLK", "科技", "Technology"), ("XLV", "医疗健康", "Health Care"),
    ("XLI", "工业", "Industrials"), ("XLB", "原材料", "Materials"),
    ("XLY", "可选消费", "Consumer Discretionary"),
    ("XLP", "必需消费", "Consumer Staples"), ("XLE", "能源", "Energy"),
    ("XLF", "金融", "Financials"), ("XLU", "公用事业", "Utilities"),
    ("XLRE", "房地产", "Real Estate"),
    ("XLC", "通信服务", "Communication Services"),
]


def fetch_sector_changes(ib) -> dict:
    """用 IBKR 历史日线取 11 个行业 ETF 的前一交易日涨跌幅。返回 {ok, date, sectors[]}。

    纯只读；历史日线通常不需实时行情订阅（delayed 数据类型即可）。
    """
    from ib_insync import Stock  # 局部导入，避免顶层依赖顺序问题
    out = {"ok": False, "date": None, "sectors": [], "source": "ibkr", "error": None}
    try:
        ib.reqMarketDataType(3)  # 3=delayed（历史数据一般不受实时订阅限制）
    except Exception:
        pass
    sectors = []
    trade_date = None
    for sym, cn, en in SECTOR_ETFS:
        c = Stock(sym, "SMART", "USD", primaryExchange="ARCA")
        try:
            ib.qualifyContracts(c)
            bars = ib.reqHistoricalData(
                c, endDateTime="", durationStr="8 D", barSizeSetting="1 day",
                whatToShow="TRADES", useRTH=True, formatDate=1)
        except Exception as e:  # noqa
            sys.stderr.write(f"  [板块] {sym} 历史数据失败: {e}\n")
            continue
        if not bars or len(bars) < 2:
            continue
        last, prev = bars[-1], bars[-2]
        if not prev.close:
            continue
        pct = (last.close - prev.close) / prev.close * 100
        trade_date = str(last.date)
        sectors.append({
            "symbol": sym, "name_cn": cn, "name_en": en,
            "price": round(float(last.close), 2),
            "prev_close": round(float(prev.close), 2),
            "change": round(float(last.close - prev.close), 2),
            "change_pct": round(float(pct), 2),
            "currency": "USD", "date": str(last.date),
        })
    sectors.sort(key=lambda x: (x.get("change_pct") or -999), reverse=True)
    out["sectors"] = sectors
    out["date"] = trade_date
    out["ok"] = bool(sectors)
    if not sectors:
        out["error"] = "IBKR 板块历史数据获取失败（可能未连接或无历史权限）"
    return out


# ── 主采集 ───────────────────────────────────────────────────────────────
def collect(cfg: dict, data_date: str) -> dict:
    ibkr = cfg.get("ibkr") or {}
    port = ibkr.get("port", 7496)
    client_id = ibkr.get("client_id", 123)
    meta = ibkr.get("holdings_meta") or {}

    result = {"ok": False, "error": None, "as_of": data_date, "generated_at": "",
              "holdings": [], "sectors": None}
    try:
        t = IBKRTrader(port=port, client_id=client_id)
        t.connect()
        # 先抓美股板块前一交易日涨跌幅（在干净连接上、持仓慢循环之前，避免被拖累/挂起）
        try:
            result["sectors"] = fetch_sector_changes(t.ib)
            n = len((result["sectors"] or {}).get("sectors", []))
            print(f"✓ IBKR 板块涨跌: {n}/{len(SECTOR_ETFS)} 个")
        except Exception as e:  # noqa
            sys.stderr.write(f"  [板块] 采集异常: {e}\n")
            result["sectors"] = {"ok": False, "sectors": [], "source": "ibkr",
                                 "error": str(e)[:120]}
        # 再取持仓 + 行情快照（账户无实时订阅时此段较慢，但已不阻塞板块）
        positions = t.get_positions()
        # 行情快照
        for p in positions:
            c = p.contract
            try:
                t.ib.qualifyContracts(c)
            except Exception:
                pass
            close, src = get_market_data(t.ib, c)
            if close is None:
                close, src = get_external_close(p.contract.symbol,
                                                getattr(p.contract, "currency", "USD") or "USD")
            c._close = close
            c._src = src or "none"
        t.disconnect()
    except Exception as e:
        result["error"] = f"IBKR 连接/持仓获取失败: {e}"
        return result

    holdings = []
    for p in positions:
        sym = p.contract.symbol
        m = meta.get(sym, {})
        name_cn = m.get("name_cn", sym)
        name_en = m.get("name_en", sym)
        news_query = m.get("news_query", f"{sym} stock")
        currency = getattr(p.contract, "currency", "USD") or "USD"
        avg_cost = p.avgCost
        shares = p.position
        close = getattr(p.contract, "_close", None)
        price_src = getattr(p.contract, "_src", "none")

        if close is not None:
            market_value = shares * close
            pnl = (close - avg_cost) * shares if currency == "USD" else None
            pnl_pct = (close / avg_cost - 1) * 100 if (currency == "USD" and avg_cost) else None
        else:
            market_value = None
            pnl = None
            pnl_pct = None

        # 新闻：只抓昨天
        raw_news = fetch_news(news_query)
        news = news_on_date(raw_news, data_date)

        # 有新闻才做中文总结
        summary = None
        if news:
            prompt = build_summary_prompt(sym, name_cn, name_en, data_date, news)
            summary = ollama_chat(cfg, prompt, num_predict=1100)

        holdings.append({
            "symbol": sym, "name_cn": name_cn, "name_en": name_en,
            "currency": currency, "shares": shares, "avg_cost": avg_cost,
            "close": close, "price_src": price_src, "market_value": market_value,
            "pnl": pnl, "pnl_pct": pnl_pct,
            "news": news, "summary": summary,
        })

    result["ok"] = True
    result["holdings"] = holdings
    result["generated_at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    return result


# ── 独立预览 HTML（深色主题，无账户概览） ────────────────────────────────
def render_standalone_html(data: dict) -> str:
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    as_of = data.get("as_of", "")
    if not data.get("ok"):
        body = f"<p class='muted'>⚠️ {esc(data.get('error') or '持仓获取失败')}</p>"
    else:
        rows = ""
        for h in data["holdings"]:
            cur = h["currency"]
            if h["close"] is not None:
                src_tag = ""
                if h.get("price_src") and h["price_src"].startswith("yahoo"):
                    src_tag = " <span class='tag'>外部</span>"
                close = f"{h['close']:,.2f} {cur}{src_tag}"
            else:
                close = '<span class="muted">-</span>'
            mv = f"{h['market_value']:,.0f} {cur}" if h["market_value"] is not None else '<span class="muted">-</span>'
            if h["pnl"] is not None:
                color = "#d8392b" if h["pnl"] >= 0 else "#2ba471"
                pnl = (f"<td class='num' style='color:{color}'>{h['pnl']:+,.0f} {cur}</td>"
                       f"<td class='num' style='color:{color}'>{h['pnl_pct']:+.2f}%</td>")
            else:
                pnl = "<td class='num muted'>-</td><td class='num muted'>-</td>"
            rows += (f"<tr><td><b>{h['symbol']}</b><br><span class='sub'>{esc(h['name_cn'])}</span></td>"
                     f"<td class='num'>{h['shares']:,.0f}</td>"
                     f"<td class='num'>{h['avg_cost']:,.2f}</td>"
                     f"<td class='num'>{close}</td><td class='num'>{mv}</td>{pnl}</tr>")
        blocks = ""
        for h in data["holdings"]:
            if not h["news"]:
                continue
            items = "".join(
                f"<li><a href='{esc(n['link'])}' target='_blank' rel='noopener'>{esc(n['title'])}</a>"
                f"<span class='src'> {esc(n['pub'][:25])}</span></li>" for n in h["news"])
            summary = h["summary"] or '<span class="muted">（无总结）</span>'
            blocks += (f"<div class='card'><h3>{h['symbol']} · {esc(h['name_cn'])}</h3>"
                       f"<div class='summary'>{summary}</div><ul class='news'>{items}</ul></div>")
        if not blocks:
            blocks = "<p class='muted'>昨日（" + as_of + "）无相关持仓股新闻。</p>"
        body = (f"<table><tr><th>代码/名称</th><th class='num'>股数</th><th class='num'>成本价</th>"
                f"<th class='num'>昨收价</th><th class='num'>市值</th><th class='num'>盈亏</th><th class='num'>盈亏%</th></tr>"
                f"{rows}</table><h2>持仓股新闻（{as_of} 当日）</h2>{blocks}")

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>实盘持仓与新闻 {as_of}</title>
 <style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}}
 .tag{{font-size:10px;background:#3a2d12;color:#f0b429;border:1px solid #6b5512;border-radius:6px;padding:1px 5px;margin-left:4px;vertical-align:middle}}
 .wrap{{max-width:920px;margin:0 auto}}
 h1{{font-size:22px;margin:0 0 4px}} .meta{{color:#8a8f98;font-size:13px;margin-bottom:20px}}
 h2{{font-size:17px;border-left:4px solid #4f8cff;padding-left:8px;margin:28px 0 12px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th,td{{border-bottom:1px solid #23262d;padding:8px 10px;text-align:left}}
 th{{color:#8a8f98;font-weight:600}} td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
 .sub{{color:#8a8f98;font-size:12px}}
 .card{{background:#171a21;border:1px solid #23262d;border-radius:10px;padding:16px;margin-bottom:14px}}
 .card h3{{margin:0 0 10px;font-size:15px;color:#9fc1ff}}
 .summary{{background:#1d2230;border-radius:8px;padding:12px;font-size:14px;line-height:1.7;white-space:pre-wrap}}
 ul.news{{margin:12px 0 0;padding-left:18px;font-size:13px}}
 ul.news li{{margin:5px 0}} ul.news a{{color:#cfe0ff;text-decoration:none}}
 .src{{color:#6b7280;font-size:11px}} .muted{{color:#6b7280}}
 .foot{{color:#6b7280;font-size:12px;margin-top:24px;line-height:1.6}}
</style></head><body><div class="wrap">
 <h1>实盘持仓与新闻日报</h1>
 <div class="meta">数据日期 <b>{as_of}</b> 收盘 ｜ 持仓来源：IBKR 实盘（只读）｜ 行情：IBKR 快照（标「外部」者为 Yahoo 兜底）｜ 新闻：Google News（仅 {as_of} 当日）｜ 中文总结：本地 Ollama</div>
 {body}
 <div class="foot">持仓经本机 TWS 实盘（只读）实时拉取，行情通过 IBKR 快照获取昨收价，均未调用任何下单或账户修改接口。新闻来自 Google News 公开 RSS，由本地模型中文总结，仅依据所提供内容。港股等跨币种持仓的盈亏需汇率换算，本表仅展示原生货币市值。</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--out", help="输出 JSON 路径（集成用）")
    ap.add_argument("--standalone-html", help="输出独立预览 HTML 路径")
    ap.add_argument("--date", required=True, help="数据日期 YYYY-MM-DD（新闻过滤用）")
    ap.add_argument("--rerun-summary", action="store_true",
                    help="读 --out JSON，仅用新 prompt 重跑中文总结（不连 IBKR）")
    args = ap.parse_args()

    cfg = load_config(args.config)

    # 仅重跑总结（不连 IBKR，快速看新 prompt 效果）
    if args.rerun_summary and args.out:
        with open(args.out, encoding="utf-8") as f:
            data = json.load(f)
        for h in data.get("holdings", []):
            if h.get("news"):
                prompt = build_summary_prompt(
                    h["symbol"], h.get("name_cn", ""), h.get("name_en", ""),
                    data.get("as_of", args.date), h["news"])
                h["summary"] = ollama_chat(cfg, prompt, num_predict=1100)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)
        print(f"✓ 仅重跑总结完成: {args.out} (持仓 {len(data.get('holdings', []))} 只)")
        if args.standalone_html:
            html = render_standalone_html(data)
            with open(args.standalone_html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"✓ 已重渲染 HTML: {args.standalone_html}")
        return

    data = collect(cfg, args.date)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)
        print(f"✓ 已写出 JSON: {args.out} (ok={data['ok']}, 持仓 {len(data.get('holdings', []))} 只)")
    if args.standalone_html:
        html = render_standalone_html(data)
        with open(args.standalone_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✓ 已写出 HTML: {args.standalone_html}")


if __name__ == "__main__":
    main()
