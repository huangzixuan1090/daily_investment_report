"""把数据 bundle 渲染成 HTML 报告。涨红跌绿（中国惯例），浅色主题。"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any
from . import common

TZ_CN = timezone(timedelta(hours=8))

CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
  color:#222;background:#f5f6f8;margin:0;padding:20px}
.wrap{max-width:820px;margin:0 auto;background:#fff;border-radius:12px;
  overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.06)}
.hd{background:linear-gradient(135deg,#1f6feb,#6f42c1);color:#fff;padding:22px 26px}
.hd h1{margin:0;font-size:20px;letter-spacing:1px}
.hd .sub{margin-top:6px;font-size:13px;opacity:.9}
.bd{padding:20px 26px 28px}
h2{font-size:16px;border-left:4px solid #6f42c1;padding-left:10px;margin:26px 0 12px}
h3{font-size:14px;color:#555;margin:16px 0 8px}
h4{font-size:13px;color:#777;margin:12px 0 6px;padding-top:8px;border-top:1px dashed #eee}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:6px}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid #eef0f3}
th{background:#f8f9fb;color:#666;font-weight:600}
td.l,th.l{text-align:left}
tr:hover td{background:#fafbff}
.up{color:#d8392b}.down{color:#2ba471}.muted{color:#999}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px}
.tag-buy{background:#fdecec;color:#d8392b}.tag-sell{background:#e8f7f0;color:#2ba471}
.tag-info{background:#eef1ff;color:#4a6cf7}
.card{background:#f8f9fb;border:1px solid #eef0f3;border-radius:8px;padding:12px 14px;margin:8px 0}
.note{font-size:12px;color:#888;line-height:1.6}
.post{border-left:3px solid #d0d4da;padding:8px 12px;margin:8px 0;background:#fafbfc;border-radius:0 6px 6px 0}
.post .meta{font-size:11px;color:#999;margin-bottom:4px}
.post .txt{font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.ft{padding:14px 26px;font-size:11px;color:#aaa;border-top:1px solid #eef0f3}
.opp{background:#fff7e6;border-color:#ffd591}
.flow-bar{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 12px}
.flow-bar .fi{flex:1;min-width:84px;text-align:center;background:#f8f9fb;border:1px solid #eef0f3;border-radius:8px;padding:8px 4px}
.flow-bar .fi .n{font-size:17px;font-weight:600;display:block;color:#333}
.flow-bar .fi .l{font-size:11px;color:#888;margin-top:2px}
.flow-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:8px 0}
@media(max-width:640px){.flow-grid{grid-template-columns:1fr}}
.fcard{background:#fff;border:1px solid #eef0f3;border-radius:10px;padding:11px 13px}
.fcard h4{margin:0 0 4px;padding:0;border:0;font-size:13px;color:#333}
.fcard .desc{font-size:11.5px;color:#888;margin-bottom:7px;line-height:1.5}
.fcard table{margin:0}
.buy{border-left:4px solid #2ba471}
.cover{border-left:4px solid #d97706}
.sell{border-left:4px solid #d8392b}
.wash{border-left:4px solid #4a6cf7}
.fana{font-size:11.5px;color:#667;line-height:1.6;margin-top:7px;background:#fafbfc;border-radius:6px;padding:6px 8px}
.chan-card{font-size:12.5px;line-height:1.55;border-left:4px solid #6f42c1;padding:12px 14px}
.chan-card.buy{border-left-color:#2ba471}.chan-card.sell{border-left-color:#d8392b}
.chan-card .title{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.chan-card .name{font-weight:600;font-size:14px;color:#222}
.chan-card .code{font-size:11px;color:#999;margin-left:4px}
.chan-card .kv{display:flex;flex-wrap:wrap;gap:4px 16px;margin:6px 0}
.chan-card .kv span{white-space:nowrap}
.chan-card .kv .k{color:#888}.chan-card .kv .v{color:#222;font-weight:500}
.chan-card .reason{margin-top:8px;padding:8px 10px;background:#f8f9fb;border-radius:6px;font-size:12px;color:#444;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.chan-card .chart-cap{font-size:11px;color:#999;margin-top:8px}
.chan-card .chart{width:100%;max-width:700px;height:auto;margin-top:6px;border:1px solid #eef0f3;border-radius:6px;background:#fff;display:block}
@media(max-width:640px){
  body{padding:10px}
  .wrap{border-radius:0}
  .bd{padding:14px}
  .chan-card{padding:10px 12px}
}
.heatmap-wrap{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 14px}
.hm-cell{display:flex;align-items:center;justify-content:center;border-radius:5px;
  font-size:11px;font-weight:600;color:#fff;cursor:default;text-align:center;
  padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  line-height:1.3;transition:opacity .15s}
.hm-cell:hover{opacity:.85}
"""


def _c(v) -> str:
    return common.color_by_pct(v)


def _row_change_cell(v):
    if v is None:
        return '<td class="muted">-</td>'
    return f'<td style="color:{_c(v)}">{v:+.2f}%</td>'


def _money_cell(v):
    return f'<td>{common.fmt_signed_money(v)}</td>'


def futures_table(rows, cols):
    head = {"name": "品种", "last_close": "最新价", "change_pct": "涨跌幅",
            "open_interest": "持仓量(手)", "inflow": "资金流入(估)", "volume": "成交量"}
    h = "<table><tr><th class='l'>品种</th>"
    for c in cols:
        h += f"<th>{head.get(c,c)}</th>"
    h += "</tr>"
    for r in rows:
        h += "<tr><td class='l'>" + f"{r['name']}<span class='muted'> {r['code']}</span></td>"
        for c in cols:
            v = r.get(c)
            if c == "change_pct":
                h += _row_change_cell(v)
            elif c == "inflow":
                h += _money_cell(v)
            elif c == "open_interest":
                h += f"<td>{v:,.0f}</td>" if v is not None else '<td class="muted">-</td>'
            elif c == "volume":
                h += f"<td>{v:,.0f}</td>" if v is not None else '<td class="muted">-</td>'
            else:
                h += f"<td>{v}</td>"
        h += "</tr>"
    return h + "</table>"


# ── 期货/ETF「资金异动」四象限卡片（参考 fund_flow 全景图样式）──
# 资金流入(增仓) / 资金流出(减仓) × 涨 / 跌  → 四象限
def _quad(change_pct, flow):
    up = (change_pct or 0) >= 0
    inn = (flow or 0) >= 0
    if up and inn: return "in_up"
    if up and not inn: return "out_up"
    if (not up) and inn: return "in_down"
    return "out_down"


_FUT_FLOW = {
    "in_up":   ("增仓上行", "资金主动增仓推涨 · 多头信心最强", "buy"),
    "out_up":  ("减仓上行", "持仓减少价格上涨 · 空头回补/空头平仓驱动反弹", "cover"),
    "in_down": ("增仓下行", "资金主动增仓打压 · 空头主导、跌势延续或启动", "sell"),
    "out_down":("减仓下行", "持仓减少价格下跌 · 多头获利了结/止损出逃", "wash"),
}
_ETF_FLOW = {
    "in_up":   ("主力净流入上行", "资金主动做多推动上涨", "buy"),
    "out_up":  ("主力净流出上涨", "被动反弹/散户追涨、主力撤离", "cover"),
    "in_down": ("主力净流入下跌", "逢低吸纳/逆势吸筹", "sell"),
    "out_down":("主力净流出下行", "资金出逃、趋势偏弱", "wash"),
}


def _flow_bar(meta, counts):
    h = '<div class="flow-bar">'
    for k in ("in_up", "out_up", "in_down", "out_down"):
        label = meta[k][0]
        h += (f'<div class="fi"><span class="n">{counts.get(k,0)}</span>'
              f'<span class="l">{label}</span></div>')
    return h + "</div>"


def _emoji(k):
    return {"in_up": "🟢", "out_up": "🟡", "in_down": "🔴", "out_down": "🔵"}.get(k, "")


def volatility_table(rows):
    """期货波动率排名：当天波动率(当日振幅) 与 近5日年化波动率（%）。"""
    if not rows:
        return '<div class="note">波动率数据不足。</div>'
    h = ("<table><tr><th class='l'>品种</th><th>最新价</th><th>涨跌幅</th>"
         "<th>当天波动率</th><th>近5日波动率</th></tr>")
    for r in rows:
        vd = r.get("vol_daily")
        v5 = r.get("vol_5d")
        vd_cell = f"{vd:.2f}%" if vd is not None else '<span class="muted">-</span>'
        v5_cell = f"{v5:.2f}%" if v5 is not None else '<span class="muted">-</span>'
        h += (f"<tr><td class='l'>{r['name']}<span class='muted'> {r['code']}</span></td>"
              f"<td>{r.get('last_close', '-')}</td>"
              f"{_row_change_cell(r.get('change_pct'))}"
              f"<td>{vd_cell}</td><td>{v5_cell}</td></tr>")
    return h + "</table>"


def futures_flow_cards(rows):
    """期货资金异动四象限：增仓上行/减仓上行/增仓下行/减仓下行。"""
    if not rows:
        return '<div class="note">期货行情数据不足。</div>'
    qs = {k: [] for k in ("in_up", "out_up", "in_down", "out_down")}
    for r in rows:
        if "error" in r:
            continue
        qs[_quad(r.get("change_pct"), r.get("inflow"))].append(r)
    h = _flow_bar(_FUT_FLOW, {k: len(v) for k, v in qs.items()})
    h += '<div class="flow-grid">'
    for k in ("in_up", "out_up", "in_down", "out_down"):
        title, desc, cls = _FUT_FLOW[k]
        items = qs[k]
        h += (f'<div class="fcard {cls}"><h4>{_emoji(k)} {title} '
              f'<span style="font-weight:400;color:#999">（{len(items)}个）</span></h4>'
              f'<div class="desc">{desc}</div>')
        if not items:
            h += '<div class="fana">本类暂无品种。</div></div>'
            continue
        items = sorted(items, key=lambda x: abs(x.get("inflow") or 0), reverse=True)
        h += ("<table><tr><th class='l'>品种</th><th>涨跌幅</th><th>资金流入(万)</th>"
              "<th>增减仓(手)</th><th>增减仓%</th></tr>")
        for r in items[:8]:
            cp = r.get("change_pct")
            inf = (r.get("inflow") or 0) / 1e4
            oic = r.get("oi_change") or 0
            oip = r.get("oi_change_pct") or 0
            h += (f"<tr><td class='l'>{r['name']}<span class='muted'> {r['code']}</span></td>"
                  f"{_row_change_cell(cp)}"
                  f"<td>{'+' if inf >= 0 else ''}{inf:,.0f}</td>"
                  f"<td>{'+' if oic >= 0 else ''}{oic:,.0f}</td>"
                  f"<td>{'+' if oip >= 0 else ''}{oip:.2f}%</td></tr>")
        h += "</table>"
        top = sorted(items, key=lambda x: abs(x.get("inflow") or 0), reverse=True)[:2]
        bits = [f"共 {len(items)} 个品种。"]
        for r in top:
            cp = r.get("change_pct")
            inf = (r.get("inflow") or 0) / 1e4
            bits.append(f"{r['name']} {cp:+.2f}%、资金{'流入' if inf >= 0 else '流出'}{abs(inf):,.0f}万")
        h += f"<div class='fana'>{'；'.join(bits)}。</div></div>"
    return h + "</div>"


def _merge_etf(etf):
    seen = {}
    for kind in ("top_gain", "top_loss", "top_inflow", "top_outflow"):
        for x in (etf.get(kind) or []):
            seen[x["code"]] = x
    return list(seen.values())


def etf_flow_cards(etf):
    """ETF 资金异动四象限：主力净流入/流出 × 涨 / 跌。"""
    rows = _merge_etf(etf) if isinstance(etf, dict) else (etf or [])
    if not rows:
        return '<div class="note">ETF 行情数据不足。</div>'
    qs = {k: [] for k in ("in_up", "out_up", "in_down", "out_down")}
    for r in rows:
        if "error" in r:
            continue
        qs[_quad(r.get("change_pct"), r.get("main_inflow"))].append(r)
    h = _flow_bar(_ETF_FLOW, {k: len(v) for k, v in qs.items()})
    h += '<div class="flow-grid">'
    for k in ("in_up", "out_up", "in_down", "out_down"):
        title, desc, cls = _ETF_FLOW[k]
        items = qs[k]
        h += (f'<div class="fcard {cls}"><h4>{_emoji(k)} {title} '
              f'<span style="font-weight:400;color:#999">（{len(items)}只）</span></h4>'
              f'<div class="desc">{desc}</div>')
        if not items:
            h += '<div class="fana">本类暂无 ETF。</div></div>'
            continue
        items = sorted(items, key=lambda x: abs(x.get("main_inflow") or 0), reverse=True)
        h += ("<table><tr><th class='l'>ETF</th><th>涨跌幅</th><th>主力净流入</th>"
              "<th>净流入占比</th></tr>")
        for r in items[:8]:
            cp = r.get("change_pct")
            mi = r.get("main_inflow") or 0
            mip = r.get("main_inflow_pct")
            mip_cell = (f"{'+' if (mip or 0) >= 0 else ''}{mip:.2f}%"
                        if mip is not None else '<span class="muted">-</span>')
            h += (f"<tr><td class='l'>{r['name']}<span class='muted'> {r['code']}</span></td>"
                  f"{_row_change_cell(cp)}"
                  f"<td>{common.fmt_signed_money(mi)}</td>"
                  f"<td>{mip_cell}</td></tr>")
        h += "</table>"
        top = sorted(items, key=lambda x: abs(x.get("main_inflow") or 0), reverse=True)[:2]
        bits = [f"共 {len(items)} 只。"]
        for r in top:
            cp = r.get("change_pct")
            mi = r.get("main_inflow") or 0
            bits.append(f"{r['name']} {cp:+.2f}%、主力{'净流入' if mi >= 0 else '净流出'}{common.fmt_signed_money(abs(mi))}")
        h += f"<div class='fana'>{'；'.join(bits)}。</div></div>"
    return h + "</div>"


def chan_table(opps):
    if not opps:
        return '<div class="note">全市场主力合约暂未发现明确的缠论买卖点信号（背驰结构未成立）。</div>'
    h = ("<table><tr><th class='l'>品种</th><th>最新价</th><th>趋势</th>"
         "<th>信号</th><th>买卖点</th><th>倾向</th><th class='l'>分析理由</th></tr>")
    for r in opps:
        c = r.get("chan", {})
        sig = c.get("signal")
        tag = ""
        if sig == "buy":
            tag = '<span class="tag tag-buy">买</span>'
        elif sig == "sell":
            tag = '<span class="tag tag-sell">卖</span>'
        point = c.get("point") or "-"
        bias = c.get("bias") or "-"
        if bias == "偏多":
            bias_cell = '<td style="color:#2ba471">偏多</td>'
        elif bias == "偏空":
            bias_cell = '<td style="color:#d8392b">偏空</td>'
        else:
            bias_cell = '<td class="muted">观望</td>'
        h += (f"<tr><td class='l'>{r['name']}</td><td>{r.get('last_close')}</td>"
              f"<td>{c.get('trend','-')}</td><td>{tag or '-'}</td>"
              f"<td>{point}</td>{bias_cell}"
              f"<td class='l' style='text-align:left'>{c.get('reason','')}</td></tr>")
        # 多级别联立（30分钟→月线）背景描述
        lv = c.get("levels") or {}
        if lv:
            items = []
            for k in ("30m", "1h", "daily", "weekly", "monthly"):
                if lv.get(k):
                    _lbl = {"30m": "30分钟", "1h": "1小时", "daily": "日线",
                            "weekly": "周线", "monthly": "月线"}.get(k, k)
                    items.append(f"<b>{_lbl}</b>：{_esc(lv[k])}")
            if items:
                h += (f"<tr><td colspan='7' class='lvl-cell' style='background:#fafbfc;"
                      f"font-size:12px;color:#555;padding:6px 10px'>"
                      f"<span class='muted'>多级别联立：</span> {' ｜ '.join(items)}</td></tr>")
    return h + "</table>"


def chanlun_a_cards(opps, charts: dict = None):
    """卡片式渲染：名称、关键字段、分析理由分块，手机端也能看到完整理由。
    charts: {code: base64 data URI} 时，在卡片底部嵌入对应股价图（K线+MACD+成交量）。"""
    if not opps:
        return '<div class="note">本轮监控的 A股中暂未发现明确的日线缠论买卖点信号（背驰结构未成立）。</div>'
    charts = charts or {}
    parts = []
    for r in opps:
        sig = r.get("signal")
        cls = "chan-card card"
        if sig == "buy":
            cls += " buy"
        elif sig == "sell":
            cls += " sell"
        tag = ""
        if sig == "buy":
            tag = '<span class="tag tag-buy">买</span>'
        elif sig == "sell":
            tag = '<span class="tag tag-sell">卖</span>'
        if not r.get("fresh", True):
            tag += ' <span class="tag tag-info">已兑现</span>'
        sp = r.get("signal_price")
        sp_cell = f"{sp:.2f}" if isinstance(sp, (int, float)) else '<span class="muted">-</span>'
        sd = r.get("signal_date")
        age = r.get("signal_age")
        sd_cell = ""
        if sd:
            sd_cell = (f"{_esc(str(sd))} <span class='muted'>({age}天前)</span>"
                       if age is not None else _esc(str(sd)))
        bias = r.get("bias") or "-"
        bias_color = "#2ba471" if bias == "偏多" else ("#d8392b" if bias == "偏空" else "#999")
        reason = r.get("reason") or ""
        reason_html = f'<div class="reason">{_esc(reason)}</div>' if reason else ""
        chart_html = ""
        uri = charts.get(r.get("code"))
        if uri:
            chart_html = (f'<div class="chart-cap">日线走势（K线 / MACD / 成交量，前复权）</div>'
                          f'<img class="chart" src="{uri}" alt="{_esc(str(r.get("code","")))} 股价图">')
        parts.append(f"""<div class="{cls}">
<div class="title"><span class="name">{_esc(r.get('name',''))}<span class="code"> {_esc(str(r.get('code','')))}</span></span>{tag}</div>
<div class="kv">
  <span><span class="k">最新价</span> <span class="v">{r.get('last_close')}</span></span>
  <span><span class="k">趋势</span> <span class="v">{_esc(r.get('trend','-'))}</span></span>
  <span><span class="k">买卖点</span> <span class="v">{_esc(r.get('point') or '-')}</span></span>
  <span><span class="k">信号价</span> <span class="v">{sp_cell}</span></span>
  <span><span class="k">倾向</span> <span class="v" style="color:{bias_color}">{_esc(bias)}</span></span>
  <span><span class="k">信号日</span> <span class="v">{sd_cell}</span></span>
</div>
{reason_html}
{chart_html}
</div>""")
    return "\n".join(parts)


def render_chanlun_scan_html(result: dict, charts: dict = None) -> str:
    """全市场日线缠论买点扫描的独立报告页。只列买点（不含卖点对照）。
    charts: {code: base64 data URI}，在每只股票卡片下方嵌入股价图。"""
    p = result.get("params", {})
    wd = p.get("within_trading_days", 5)
    date = result.get("date", "—")
    opps = result.get("opportunities", [])
    bse_note = "" if p.get("include_bse") else "、剔除北交所(8/4/9)"
    st_note = "" if p.get("include_st") else "、剔除ST"

    summary = (f"扫描 <b>{result.get('count_total',0)}</b> 只 A股（沪深{bse_note}{st_note}），"
               f"成功 <b>{result.get('count_ok',0)}</b> 只，失败 {result.get('count_fail',0)} 只；"
               f"近 <b>{wd}</b> 个交易日内共出现日线缠论<b>买点</b>信号 <b>{result.get('count_buy_signals',0)}</b> 个，"
               f"涉及 <b>{result.get('count_buy',0)}</b> 只标的（已按股票去重，每只取最新买点），"
               f"其中 <b>{result.get('count_fresh',0)}</b> 只当前仍处于可操作区间（fresh）。"
               f"下方按「一买优先 → fresh 优先 → 信号最新」排序展示。")
    note = (f"数据日期 {date} ｜ 数据源 akshare 新浪日线（前复权）｜ 判定为 lib/chan 标准缠论引擎对日线的"
            f"一买(底背驰)/二买确定性识别，不依赖大模型。仅列「买点且信号形成于近 {wd} 个交易日」的标的；"
            f"「已兑现」表示信号价已被突破、当前不宜追。日线信号天然滞后于盘中，可操作性落到「次日倾向 + 参考位」。"
            f"2026-07-29 引擎升级（借鉴第三方「缠论选股」技能）：一买改用『力度(价×量)趋势背驰』确认，"
            f"二买/三买以中枢 zg/zd 严格判定，并附回测胜率量化信号质量，显著降低噪声。仅供研究，不构成投资建议。")
    wd_label = f"近 {wd} 个交易日" if wd <= 21 else "近一个月"
    parts = []
    parts.append(f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>全市场 A股 日线缠论买点扫描（{wd_label}）</title><style>{CSS}</style></head>
<body><div class='wrap'><div class='hd'><h1>全市场 A股 · 日线缠论买点扫描</h1>
<div class='sub'>{wd_label}内的日线级别买点机会 ｜ 数据日期 {date}</div></div>
<div class='bd'>
<div class='note'>{summary}</div>""")
    bt = result.get("backtest_agg") or {}
    if bt.get("trades"):
        parts.append(
            f"<div class='note' style='background:#eef7f0;border-left:3px solid #2ba471'>"
            f"<b>信号质量回测</b>（借鉴「缠论选股」技能方法：信号日次一交易日开盘买入、持有 20 个交易日）｜"
            f"覆盖 <b>{bt['stocks']}</b> 只信号股、<b>{bt['trades']}</b> 笔交易 ｜ "
            f"胜率 <b>{bt['win_rate']}%</b> ｜ 平均收益 <b>{bt['avg_return']}%</b>"
            f"（加权）。胜率越高代表这批买点信号的历史可靠性越好。</div>")
    parts.append(f"<h2>{wd_label}日线缠论买点机会</h2>")
    CAP = 60
    opps_shown = opps[:CAP]
    parts.append(chanlun_a_cards(opps_shown, charts=charts))
    if len(opps) > CAP:
        parts.append(f"<div class='note'>（共 {len(opps)} 只标的出现日线买点，按一买优先 / fresh 优先 / 信号最新排序，"
                     f"此处展示前 {CAP} 只；完整清单见缓存 cache/chanlun_a_scan_*.json）</div>")
    if not opps:
        parts.append(f"<div class='note'>{wd_label}全市场 {result.get('count_ok',0)} 只已分析 A股中，"
                     f"<b>未出现任何日线级别的一买(底背驰)/二买标准买点</b>。</div>")
    parts.append(f"<div class='note'>{note}</div>")
    parts.append("</div>")
    parts.append("<div class='ft'>由 lib/chan 标准缠论引擎自动扫描生成。信号基于日线收盘确认，天然滞后于盘中；可操作性落到「次日倾向 + 参考位」。仅供研究参考，不构成任何投资建议。</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def chan_structures_table(structs):
    """多级别结构速览：按当日涨跌幅前 N 的品种，展示 30分钟→月线 的联立结构。"""
    if not structs:
        return ""
    h = (f"<h3>多级别结构速览（按当日涨跌幅前 {len(structs)} 名，30分钟→月线联立）</h3>"
         "<table><tr><th class='l'>品种</th><th>涨跌幅</th><th class='l'>30分钟</th>"
         "<th class='l'>1小时</th><th class='l'>日线</th><th class='l'>周线</th>"
         "<th class='l'>月线</th></tr>")
    for s in structs:
        c = s.get("chan", {})
        cp = s.get("change_pct") or 0
        cp_cell = _row_change_cell(cp)
        lv = c.get("levels") or {}
        has_sig = c.get("has_signal")

        def _cell(k):
            v = (lv.get(k) or "").strip()
            if not v:
                return '<td class="l muted" style="font-size:12px">—</td>'
            style = ("font-size:12px;color:#555;"
                     "background:#fffaf0" if (k in ("30m", "1h") and has_sig) else "font-size:12px;color:#555")
            return f'<td class="l" style="{style}">{_esc(v)}</td>'

        tag = (' <span class="tag tag-buy">买</span>' if c.get("signal") == "buy"
               else ' <span class="tag tag-sell">卖</span>' if c.get("signal") == "sell" else "")
        h += (f"<tr><td class='l'>{_esc(s['name'])}{tag}</td>{cp_cell}"
              f"{_cell('30m')}{_cell('1h')}{_cell('daily')}{_cell('weekly')}{_cell('monthly')}</tr>")
    return h + "</table>"
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>板块</th><th>涨跌幅</th><th>主力净流入</th>"
         "<th>成交额</th><th>振幅</th></tr>")
    for r in rows:
        cp = r.get("change_pct")
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"{_row_change_cell(cp)}{_money_cell(r.get('main_inflow'))}"
              f"<td>{common.fmt_money(r.get('amount'))}</td>"
              f"<td>{common.pct(r.get('amplitude'))}</td></tr>")
    return h + "</table>"


def heatmap_html(rows, title: str = "", max_cells: int = 60) -> str:
    """把板块列表渲染成按涨跌幅着色的热力图，格子大小与涨跌幅绝对值成比例。"""
    if not rows:
        return f'<div class="note">{title}暂无数据。</div>'
    # 按涨跌幅降序排列
    rows = sorted(rows, key=lambda x: (x.get("change_pct") or 0), reverse=True)[:max_cells]
    max_abs = max((abs(r.get("change_pct") or 0) for r in rows), default=1) or 1

    def _color(v):
        if v is None: return "#aaa"
        t = min(abs(v) / max_abs, 1.0)
        if v > 0:
            r2 = int(180 + 75 * t); g2 = int(50 - 30 * t); b2 = int(50 - 30 * t)
        elif v < 0:
            r2 = int(50 - 30 * t); g2 = int(160 + 60 * t); b2 = int(50 - 30 * t)
        else:
            return "#999"
        return f"rgb({r2},{g2},{b2})"

    def _size(v):
        # min 52px, max 130px
        t = min(abs(v or 0) / max_abs, 1.0) if max_abs else 0
        return max(52, int(52 + 78 * t))

    cells = []
    for r in rows:
        cp = r.get("change_pct")
        name = _esc(r.get("name", ""))
        pct_str = f"{cp:+.2f}%" if cp is not None else "-"
        sz = _size(cp)
        bg = _color(cp)
        tooltip = pct_str
        # A 股概念板块：显示领涨股
        lead = r.get("leading_stock") or r.get("name_en") or ""
        if lead:
            tooltip += f" · {lead}"
        cells.append(
            f'<div class="hm-cell" style="background:{bg};width:{sz}px;height:{sz}px" title="{_esc(tooltip)}">'
            f'{name}<br><span style="font-size:10px;opacity:.9">{pct_str}</span></div>'
        )
    h = f'<h4>{_esc(title)}</h4><div class="heatmap-wrap">{"".join(cells)}</div>'
    return h


def sina_sector_table(rows):
    """新浪兜底行业板：板块 / 涨跌幅 / 领涨股。"""
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>板块</th><th>涨跌幅</th><th class='l'>领涨股</th></tr>")
    for r in rows:
        cp = r.get("change_pct")
        lead = r.get("leading_stock") or "-"
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"{_row_change_cell(cp)}<td class='l'>{_esc(lead)}</td></tr>")
    return h + "</table>"


def sector_table(rows):
    """东方财富板块榜：板块 / 涨跌幅 / 主力净流入 / 净流入占比 / 成交额 / 振幅。"""
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>板块</th><th>涨跌幅</th><th>主力净流入</th>"
         "<th>净流入占比</th><th>成交额</th><th>振幅</th></tr>")
    for r in rows:
        cp = r.get("change_pct")
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"{_row_change_cell(cp)}{_money_cell(r.get('main_inflow'))}"
              f"<td>{common.pct(r.get('main_inflow_pct'))}</td>"
              f"<td>{common.fmt_money(r.get('amount'))}</td>"
              f"<td>{common.pct(r.get('amplitude'))}</td></tr>")
    return h + "</table>"


def etf_table(rows):
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>ETF</th><th>涨跌幅</th><th>主力净流入</th>"
         "<th>净流入占比</th><th>成交额</th></tr>")
    for r in rows:
        cp = r.get("change_pct")
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"{_row_change_cell(cp)}{_money_cell(r.get('main_inflow'))}"
              f"<td>{common.pct(r.get('main_inflow_pct'))}</td>"
              f"<td>{common.fmt_money(r.get('amount'))}</td></tr>")
    return h + "</table>"


def etf_section(etf):
    if not etf:
        return ""
    edate = etf.get("date") or ""
    src = "东方财富" if etf.get("source") == "eastmoney" else "—"
    parts = [f"<h3>ETF 表现 · 涨跌幅与资金流入/流出（来源：{src}，数据日期 {edate}）</h3>"]
    if "error" in etf:
        parts.append(f"<div class='note'>⚠️ {etf['error']}</div>")
        return "".join(parts)
    parts.append("<div class='note'>覆盖沪深全市场 ETF（宽基/行业/主题/商品等）；按【涨跌幅 × 主力净流入/流出】分为四象限（资金流入上行 / 资金流出上行 / 资金流入下行 / 资金流出下行），左侧色条表示资金多空含义（绿=资金推涨、红=资金出逃）。资金流入=主力净流入（东方财富口径）。</div>")
    parts.append(etf_flow_cards(etf))
    return "".join(parts)


def blogger_section(b):
    h = f"<h3>@{b.get('handle')} · {b.get('name')}</h3>"
    if b.get("error"):
        h += f'<div class="note">⚠️ {b["error"]}</div>'
        return h
    if b.get("summary"):
        h += (f'<div class="card"><b>观点总结：</b>'
              f'<div style="margin-top:6px;white-space:pre-wrap">{_esc(b["summary"])}</div></div>')
        return h
    posts = b.get("posts", [])
    if not posts:
        h += '<div class="note">目标日期内无推文。</div>'
        return h
    # 无总结（Ollama 不可用）：按用户要求不展示原文，仅提示
    h += '<div class="note">观点总结暂未生成（本地模型不可用），原文不展示。</div>'
    return h


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def bond_table(rows):
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>债券</th><th>收益率</th><th>涨跌(bp)</th><th>涨跌幅</th></tr>")
    for r in rows:
        y = r.get("yield")
        chg = r.get("change")
        chg_pct = r.get("change_pct")
        y_cell = f"{y:.4f}%" if y is not None else '<span class="muted">-</span>'
        # 收益率变化通常用 bp（1% = 100bp）
        bp_cell = f"{chg*100:+.2f}" if chg is not None else '<span class="muted">-</span>'
        cp_cell = f"{chg_pct:+.2f}%" if chg_pct is not None else '<span class="muted">-</span>'
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"<td>{y_cell}</td><td>{bp_cell}</td>"
              f"<td style='color:{_c(chg_pct)}'>{cp_cell}</td></tr>")
    return h + "</table>"


def fx_table(rows):
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>货币对</th><th>最新价</th><th>涨跌</th><th>涨跌幅</th><th>方向</th></tr>")
    for r in rows:
        p = r.get("price")
        chg = r.get("change")
        chg_pct = r.get("change_pct")
        # 对美元在前的货币对（USDJPY/USDCNY/USDCHF/USDCAD），上涨=美元走强；其他=美元在后的（EUR/GBP/AUD）上涨=美元走弱
        key = r.get("key", "")
        dollar_first = key in ("USDJPY", "USDCNY", "USDCHF", "USDCAD")
        if chg_pct is None:
            direction = "—"
        elif chg_pct > 0:
            direction = "美元走强" if dollar_first else "美元走弱"
        elif chg_pct < 0:
            direction = "美元走弱" if dollar_first else "美元走强"
        else:
            direction = "持平"
        h += (f"<tr><td class='l'>{r['name']}</td>"
              f"<td>{p:.4f}</td>"
              f"<td>{chg:+.4f}</td>"
              f"<td style='color:{_c(chg_pct)}'>{chg_pct:+.2f}%</td>"
              f"<td>{direction}</td></tr>")
    return h + "</table>"


def global_markets_section(gm, section: str = "all"):
    if not gm or not gm.get("ok"):
        err = (gm or {}).get("error", "全球债券与货币数据获取失败")
        return (f"<div class='note'>⚠️ {err}</div>")
    parts = []
    if section in ("all", "bonds"):
        parts.append(f"<div class='note'>数据日期：{gm.get('date','—')} ｜ 来源：Yahoo Finance（美债），akshare（中债）。</div>")
        parts.append("<h3>全球债券收益率</h3>")
        parts.append(bond_table(gm.get("bonds", [])))
    if section in ("all", "fx"):
        parts.append(f"<div class='note'>数据日期：{gm.get('date','—')} ｜ 来源：Yahoo Finance（美元指数/主要汇率）。</div>")
        parts.append("<h3>全球货币表现</h3>")
        parts.append(fx_table(gm.get("fx", [])))
    return "\n".join(parts)

    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>板块</th><th class='l'>英文名</th>"
         "<th>ETF</th><th>最新价</th><th>涨跌幅</th></tr>")
    for r in rows:
        h += (f"<tr><td class='l'>{r['name_cn']}</td><td class='l muted'>{r['name_en']}</td>"
              f"<td class='l muted'>{r['symbol']}</td>"
              f"<td>{r.get('price')}</td>{_row_change_cell(r.get('change_pct'))}</tr>")
    return h + "</table>"


def us_stock_render(rows):
    if not rows:
        return '<div class="note">无数据</div>'
    h = ("<table><tr><th class='l'>名称</th><th class='l'>代码</th>"
         "<th>最新价</th><th>涨跌额</th><th>涨跌幅</th><th>市值</th></tr>")
    for r in rows:
        cap = common.fmt_money(r.get("market_cap"))
        cur = r.get("currency", "USD")
        chg = r.get("change")
        price = r.get("price")
        price_cell = f"{price} {cur}" if price is not None else '<span class="muted">-</span>'
        chg_cell = f"{chg:+.2f}" if chg is not None else '<span class="muted">-</span>'
        chg_cell = f"<td style='color:{_c(chg)}'>{chg_cell}</td>" if chg is not None else "<td class='muted'>-</td>"
        h += (f"<tr><td class='l'>{r.get('name_cn') or ''}"
              f"<span class='muted'> {r.get('name_en','')}</span></td>"
              f"<td class='l muted'>{r['symbol']}</td>"
              f"<td>{price_cell}</td>{chg_cell}"
              f"{_row_change_cell(r.get('change_pct'))}<td>{cap}</td></tr>")
    return h + "</table>"


def us_news_render(items, symbol):
    if not items:
        return '<div class="note">当日暂无相关新闻。</div>'
    h = []
    for n in items:
        meta = n.get("published_cn", "")
        title = _esc(n.get("title", ""))
        link = _esc(n.get("link", ""))
        h.append(f'<div class="post"><div class="meta">{meta}</div>'
                 f'<div class="txt"><a href="{link}" target="_blank" rel="noopener">{title}</a></div></div>')
    return "\n".join(h)


def us_sector_render(rows):
    if not rows:
        return '<div class="note">当日板块行情数据暂缺，价格将于数据源恢复后补全。</div>'
    h = ("<table><tr><th class='l'>板块</th><th>代码</th><th>最新价</th>"
         "<th>涨跌额</th><th>涨跌幅</th></tr>")
    for r in rows:
        cur = r.get("currency", "USD")
        price = r.get("price")
        price_cell = f"{price:,.2f} {cur}" if price is not None else '<span class="muted">-</span>'
        chg = r.get("change")
        chg_cell = f"{chg:+.2f}" if chg is not None else '<span class="muted">-</span>'
        chg_cell = (f"<td style='color:{_c(chg)}'>{chg_cell}</td>"
                    if chg is not None else "<td class='muted'>-</td>")
        name = f"{r.get('name_cn','')} <span class='muted'>{r.get('name_en','')}</span>"
        h += (f"<tr><td class='l'>{name}</td><td class='muted'>{r.get('symbol','')}</td>"
              f"<td>{price_cell}</td>{chg_cell}{_row_change_cell(r.get('change_pct'))}</tr>")
    return h + "</table>"


def us_stocks_section(us):
    if not us or not us.get("ok"):
        err = (us or {}).get("error")
        return f"<div class='note'>⚠️ 美股数据获取失败（{_esc(err or '未知错误')}），已跳过本模块。</div>"
    h = []
    src = "IBKR 实时行情" if us.get("sector_source") == "ibkr" else "Yahoo Finance"
    date_label = us.get("sector_date") or us.get("date_label", "—")
    fallback = "（Yahoo 限流，已用 IBKR 兜底）" if us.get("sector_source") == "ibkr" else ""
    h.append(f"<div class='note'>数据日期：{_esc(str(date_label))} ｜ 行情来源 {_esc(src)}{fallback}，"
             f"板块以 11 个 GICS 行业 SPDR ETF 为代理。</div>")
    if not us.get("quotes_ok") and us.get("sector_source") != "ibkr":
        h.append('<div class="note">⚠️ 行情价格暂缺（数据源限流），价格将于恢复后补全。</div>')
    h.append("<h3>美股板块涨跌排名</h3>")
    h.append(us_sector_render(us.get("sectors", [])))
    return "\n".join(h)


def holdings_section(h):
    """⑨ 我的持仓（IBKR 实盘）：持仓表 + 仅昨日的持仓股新闻中文总结。无账户概览。"""
    if not h or not h.get("ok"):
        err = (h or {}).get("error")
        return (f"<h2>⑨ 我的持仓（IBKR 实盘）</h2>"
                f"<div class='note'>⚠️ 实盘持仓获取失败（{_esc(err or '未知错误')}），"
                f"已跳过本模块。需本机 TWS 实盘运行且启用 API 连接。</div>")
    as_of = h.get("as_of", "")
    holdings = h.get("holdings", [])
    if not holdings:
        return (f"<h2>⑨ 我的持仓（IBKR 实盘）</h2>"
                f"<div class='note'>当前账户无持仓。</div>")
    out = [f"<h2>⑨ 我的持仓（IBKR 实盘）</h2>"]
    out.append(f"<div class='note'>数据日期：{as_of} 收盘 ｜ 持仓经本机 TWS 实盘以只读方式实时拉取；"
               f"昨收价通过 IBKR 行情快照获取；新闻来自 Google News（仅 {as_of} 当日），"
               f"中文总结由本地 Ollama 生成。全程未调用任何下单接口。</div>")
    rows = ""
    for x in holdings:
        cur = x.get("currency", "USD")
        close = x.get("close")
        src_tag = ' <span class="muted">[外部]</span>' if str(x.get("price_src", "")).startswith("yahoo") else ""
        close_cell = f"<td>{close:,.2f} {cur}{src_tag}</td>" if close is not None else '<td class="muted">-</td>'
        mv = x.get("market_value")
        mv_cell = f"<td>{mv:,.0f} {cur}</td>" if mv is not None else '<td class="muted">-</td>'
        pnl = x.get("pnl")
        if pnl is not None:
            pnl_cell = f"<td style='color:{_c(x.get('pnl_pct'))}'>{pnl:+,.0f} {cur}</td>"
            pct_cell = _row_change_cell(x.get("pnl_pct"))
        else:
            pnl_cell = "<td class='muted'>-</td>"
            pct_cell = "<td class='muted'>-</td>"
        rows += (f"<tr><td class='l'><b>{x['symbol']}</b> <span class='muted'>{_esc(x.get('name_cn',''))}</span></td>"
                 f"<td>{x['shares']:,.0f}</td><td>{x['avg_cost']:,.2f}</td>"
                 f"{close_cell}{mv_cell}{pnl_cell}{pct_cell}</tr>")
    out.append("<table><tr><th class='l'>代码/名称</th><th>股数</th><th>成本价</th>"
               "<th>昨收价</th><th>市值</th><th>盈亏</th><th>盈亏%</th></tr>"
               f"{rows}</table>")
    any_news = False
    for x in holdings:
        news = x.get("news") or []
        if not news:
            continue
        any_news = True
        nm = x.get("name_cn") or x["symbol"]
        out.append(f"<div class='note' style='margin-top:14px'><b>{nm}（{x['symbol']}）</b> {as_of} 当日新闻</div>")
        if x.get("summary"):
            out.append(f'<div class="card" style="white-space:pre-wrap">{_esc(x["summary"])}</div>')
        for n in news:
            out.append(f'<div class="post"><div class="meta">{_esc(n.get("pub","")[:25])}</div>'
                       f'<div class="txt"><a href="{_esc(n.get("link",""))}" target="_blank" rel="noopener">'
                       f'{_esc(n.get("title",""))}</a></div></div>')
    if not any_news:
        out.append(f"<div class='note' style='margin-top:12px'>{as_of} 当日无相关持仓股新闻。</div>")
    return "\n".join(out)


def render_report(bundle: dict) -> str:
    now = datetime.now(TZ_CN)
    fut = bundle.get("futures", {})
    sec = bundle.get("sectors", {})
    blg = bundle.get("bloggers", {})
    etf = bundle.get("etf")
    gm = bundle.get("global_markets", {})
    us = bundle.get("us_stocks", {})

    data_date = bundle.get("data_date") or fut.get("date") or ""
    data_weekday = bundle.get("data_weekday") or ""
    data_label = bundle.get("data_date_label") or ""

    parts = [f"<!doctype html><html><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>每日市场报告 {data_date}</title><style>{CSS}</style></head><body>"
             f"<div class='wrap'>"]

    # 头部
    commentary = bundle.get("commentary", "")
    dstr = f"{data_date}（{data_weekday}）" if data_weekday else (data_date or "—")
    parts.append(
        f"<div class='hd'><h1>每日市场报告</h1>"
        f"<div class='sub'>行情数据日期：{dstr} ｜ {data_label} ｜ 生成于 {now:%Y-%m-%d %H:%M} (CST)</div></div>"
        f"<div class='bd'>")
    parts.append(f"<div class='note'>本报告汇总 <b>{dstr}</b>（{data_label}）收盘后的期货市场、"
                 f"股市概念板块热力图（A股/美股）、全球债券与货币市场、X 博主当日公开观点。"
                 f"各模块数据来源与日期见对应小节标注。</div>")
    if commentary:
        parts.append(f"<div class='card' style='white-space:pre-wrap'>{_esc(commentary)}</div>")

    # 期货
    parts.append("<h2>① 期货市场</h2>")
    parts.append(f"<div class='note'>数据日期：{fut.get('date','—')} ｜ 覆盖主力连续合约 "
                 f"{fut.get('count_ok',0)}/{fut.get('count_total',0)} 个；"
                 f"按【涨跌幅 × 资金流入/流出（=增仓/减仓）】分为四象限（资金流入上行 / 资金流出上行 / 资金流入下行 / 资金流出下行），左侧色条表示资金多空含义（绿=资金推涨、红=资金出逃）。资金流入=持仓量变化×收盘价×合约乘数（估算口径）。</div>")
    parts.append(futures_flow_cards(fut.get("all", [])) or '<div class="note">期货行情数据不足。</div>')
    parts.append("<h3>波动率排名（近 5 日波动率前 12）</h3>")
    parts.append("<div class='note'>当天波动率 = 当日振幅 (最高价-最低价)/收盘价（%）；近5日波动率 = 最近 5 个交易日对数收益率的年化标准差（%，按 √252 年化）。数值越高表示该品种波动越剧烈。</div>")
    parts.append(volatility_table(fut.get("by_volatility", [])))

    # 缠论（期货）
    parts.append("<h2>② 缠论机会筛选（期货）</h2>")
    parts.append(f"<div class='note'>数据日期：{fut.get('date','—')} ｜ 本模块对全部品种做<b>多级别联立分析</b>（30分钟/1小时为操作级，日线/周线/月线定方向与背景）："
                 f"<b>买卖信号</b>由确定的标准缠论引擎（笔→中枢→MACD背驰，lib/chan）对所有品种判定【当天】30分钟/1小时买卖点（快、确定性、不漏）；"
                 f"本地大模型(qwen2.5:14b)仅对引擎命中的重点品种补写多级别趋势/背驰/理由叙述，提升可读性。"
                 f"所有信号均仅取【当天】形成的买卖点（禁止引用当天之前的旧日期）。以「大级别方向 + 小级别买卖点」指导操作。"
                 f"下方「多级别结构速览」按当日涨跌幅前 12 名展示各品种 30分钟→月线 的联立结构（无论是否有当日买卖点）。仅作参考，不构成投资建议。</div>")
    parts.append(chan_table(fut.get("opportunities", [])))
    if fut.get("chan_structures"):
        parts.append(chan_structures_table(fut.get("chan_structures", [])))

    # 板块 — 概念热力图
    parts.append("<h2>③ 股市概念板块热力图</h2>")
    parts.append(f"<div class='note'>数据来源：东方财富概念板块（m:90 t:3），数据日期 {data_date}。"
                 "格子大小与涨跌幅绝对值成比例；颜色越深涨跌幅越大；悬停查看领涨股。</div>")
    concept_board = next((b for b in sec.get("boards", []) if b.get("key") == "concept"), None)
    if concept_board and "error" not in concept_board:
        bdate = concept_board.get("date") or data_date or ""
        # 合并涨跌，展示全部
        all_rows = (concept_board.get("top_gain") or []) + (concept_board.get("top_loss") or [])
        # 去重
        seen = set(); uniq = []
        for r in all_rows:
            if r.get("name") not in seen:
                seen.add(r.get("name")); uniq.append(r)
        parts.append(heatmap_html(uniq, title=f"A股概念板块（{bdate}）", max_cells=80))
    elif concept_board and "error" in concept_board:
        parts.append(f"<div class='note'>⚠️ {_esc(concept_board['error'])}</div>")
    else:
        parts.append('<div class="note">A股概念板块数据暂缺。</div>')
    parts.append(etf_section(etf))

    # 美股概念热力图
    us_sectors = us.get("sectors", [])
    if us_sectors:
        udate = us.get("date_label") or us.get("date") or ""
        parts.append(heatmap_html(
            [{"name": r.get("name_cn",""), "change_pct": r.get("change_pct"),
              "name_en": r.get("name_en",""), "leading_stock": r.get("symbol","")}
             for r in us_sectors],
            title=f"美股主题ETF（{udate}）", max_cells=60))
    else:
        parts.append('<div class="note">美股主题数据暂缺。</div>')

    # 全球债券
    parts.append(f"<h2>④ 全球债券市场分析</h2>")
    parts.append(global_markets_section(gm, section="bonds"))

    # 全球货币
    parts.append(f"<h2>⑤ 全球货币表现</h2>")
    parts.append(global_markets_section(gm, section="fx"))

    # 博主
    bday = blg.get("data_date") or data_date or ""
    parts.append(f"<h2>⑥ 博主观点（{bday} 当日推文）</h2>")
    blg_list = blg.get("bloggers", [])
    if not blg.get("has_auth"):
        parts.append('<div class="note">⚠️ 未配置 Twitter 登录 Cookie，博主观点暂未抓取。'
                     '请在 config.json 填入 twitter.auth_token 与 ct0 后自动生效。</div>')
    for b in blg_list:
        parts.append(blogger_section(b))

    parts.append("</div>")
    parts.append(
        f"<div class='ft'>行情数据日期：{dstr}（{data_label}）。本报告由每日市场Agent自动生成。"
        "期货资金流入为估算口径；期货缠论由本地大模型分析；板块数据来自东方财富/新浪，"
        "债券收益率来自 akshare、主要货币对来自新浪外汇，"
        "博主内容来自X公开推文，美股主题ETF行情来自 yfinance。仅供研究参考，不构成投资建议。</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


if __name__ == "__main__":
    import json, sys
    bundle = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
    print(render_report(bundle))
