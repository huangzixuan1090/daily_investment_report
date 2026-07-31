#!/usr/bin/env python3
"""每日市场报告主流程：抓取(期货/缠论/板块/博主) → 渲染HTML → 发邮件。

用法:
  python daily_report.py            # 抓取+渲染+发送
  python daily_report.py --no-send  # 只抓取+渲染，不发邮件（测试用）
  python daily_report.py --from-cache bundle.json  # 用已有bundle渲染
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lib import common, futures, sectors, bloggers, render, mail, llm, etf, global_markets, us_stocks, wechat

TZ_CN = timezone(timedelta(hours=8))

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _weekday_cn(d: str) -> str:
    try:
        return _WEEKDAYS[datetime.strptime(d, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def _data_date_label(data_date: str) -> str:
    """相对生成时刻，给数据日期一个友好标签。"""
    today = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    if data_date == today:
        return "当日收盘"
    return "前一交易日"


def _cache_path(cfg: dict, section: str) -> Path:
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    tag = datetime.now(TZ_CN).strftime("%Y%m%d")
    return cache_dir / f"{section}_{tag}.json"


def _resolve_data_date(bundle: dict) -> str:
    """从期货结果取权威数据日期（最新交易日），回退到生成日。"""
    d = (bundle.get("futures") or {}).get("date")
    if not d:
        d = bundle.get("generated_at", "")[:10] or datetime.now(TZ_CN).strftime("%Y-%m-%d")
    return d


def _fetch_holdings(cfg: dict, data_date: str, reuse_cache: bool = False):
    """通过 trading_bot/.venv 跑 ibkr_collect.py（只读连实盘），返回持仓 JSON。失败则回退为跳过。"""
    ibkr = cfg.get("ibkr") or {}
    if not ibkr.get("enabled", False):
        return None
    venv_py = ibkr.get("venv_python")
    if not venv_py or not Path(venv_py).exists():
        common.log.warning("ibkr.venv_python 未配置或不存在，跳过持仓模块")
        return None
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"holdings_{datetime.now(TZ_CN):%Y%m%d}.json"
    # 复用缓存：持仓缓存已存在则跳过 IBKR 实时连接（更快，也便于 TWS 关闭时兜底）
    if reuse_cache and out_path.exists():
        try:
            with open(out_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("ok") or cached.get("holdings"):
                common.log.info("复用持仓缓存 %s（跳过 IBKR 实时连接）", out_path.name)
                return cached
        except Exception as e:  # noqa
            common.log.warning("持仓缓存读取失败，将重新采集: %s", e)
    script = common.ROOT / ibkr.get("script", "ibkr_collect.py")
    if not script.exists():
        common.log.warning("ibkr 采集脚本不存在: %s，跳过持仓模块", script)
        return {"ok": False, "error": f"采集脚本缺失: {script.name}", "holdings": []}
    try:
        common.log.info("调用 ibkr_collect（trading_bot venv）抓取实盘持仓 ...")
        subprocess.run(
            [venv_py, str(script), "--out", str(out_path), "--date", data_date],
            check=True, timeout=120, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa
        common.log.warning("持仓采集失败: %s", e)
        return {"ok": False, "error": f"采集失败: {e}", "holdings": []}


def fetch_bundle(cfg: dict, reuse_cache: bool = False) -> dict:
    log = common.log
    tag = datetime.now(TZ_CN).strftime("%Y%m%d")
    bundle = {
        "generated_at": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "config_recipients": cfg.get("recipients", []),
    }

    def get_section(name, fetcher):
        if reuse_cache:
            p = _cache_path(cfg, name)
            if p.exists():
                log.info("复用缓存 %s", p.name)
                return json.load(open(p, encoding="utf-8"))
        return fetcher()

    # 1) 先抓期货（拿权威数据日期 = 最新交易日）
    log.info("=== 抓取期货 ===")
    bundle["futures"] = get_section("futures", lambda: futures.get_futures_overview(cfg))
    data_date = _resolve_data_date(bundle)
    bundle["data_date"] = data_date
    bundle["data_weekday"] = _weekday_cn(data_date)
    bundle["data_date_label"] = _data_date_label(data_date)
    log.info("数据日期 = %s (%s, %s)", data_date, bundle["data_weekday"], bundle["data_date_label"])

    # 2) 板块/ETF/全球/美股/博主/公众号/持仓 — 互相独立，并发抓取
    log.info("=== 并发抓取各模块 ===")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=7) as pool:
        f_sec  = pool.submit(lambda: get_section("sectors", lambda: sectors.get_sectors(cfg, data_date=data_date)))
        f_etf  = pool.submit(lambda: get_section("etf", lambda: etf.get_etf(cfg, data_date=data_date)))
        f_gm   = pool.submit(lambda: get_section("global_markets", lambda: global_markets.get_global_markets(cfg, data_date=data_date)))
        f_us   = pool.submit(lambda: get_section("us_stocks", lambda: us_stocks.get_us_stocks(cfg, data_date=data_date)))
        f_blog = pool.submit(lambda: get_section("bloggers", lambda: bloggers.get_bloggers(cfg, data_date=data_date)))
        f_wc   = pool.submit(lambda: get_section("wechat", lambda: wechat.get_wechat_articles(cfg, data_date=data_date)))
        f_hold = pool.submit(lambda: _fetch_holdings(cfg, data_date, reuse_cache=reuse_cache))
        bundle["sectors"]       = f_sec.result()
        bundle["etf"]           = f_etf.result()
        bundle["global_markets"]= f_gm.result()
        bundle["us_stocks"]     = f_us.result()
        bundle["bloggers"]      = f_blog.result()
        bundle["wechat"]        = f_wc.result()
        bundle["holdings"]      = f_hold.result()

    # 美股板块兜底：Yahoo 被限流/数据不全时，用 IBKR 实时连接返回的板块涨跌补全（两者 schema 一致）。
    # 触发条件：IBKR 板块更全（数量多于 Yahoo）或 Yahoo 行情失败。
    _us = bundle.get("us_stocks") or {}
    _ibkr_sec = (bundle.get("holdings") or {}).get("sectors") or {}
    _yahoo_secs = _us.get("sectors") or []
    if _ibkr_sec.get("ok"):
        _filled = _ibkr_sec.get("sectors", [])
        if _filled and (len(_filled) > len(_yahoo_secs) or not _us.get("quotes_ok")):
            _us["sectors"] = _filled
            _us["sector_source"] = "ibkr"
            _us["sector_date"] = _ibkr_sec.get("date")
            _us["ok"] = True
            common.log.info("美股板块改用 IBKR 数据兜底（%d 个，Yahoo 仅 %d 个）",
                            len(_filled), len(_yahoo_secs))

    # 本地 Ollama 生成博主总结 + 市场总评 + 美股概览（不消耗云端 token；不可用时回退）
    llm.fill_llm_texts(cfg, bundle)
    return bundle


def save_outputs(cfg: dict, bundle: dict, html: str) -> tuple[Path, Path]:
    out_dir = common.ROOT / cfg["paths"].get("output_dir", "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(TZ_CN).strftime("%Y%m%d")
    bundle_path = out_dir / f"bundle_{tag}.json"
    html_path = out_dir / f"daily_report_{tag}.html"
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, default=str, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return bundle_path, html_path


def main():
    ap = argparse.ArgumentParser(description="每日市场报告")
    ap.add_argument("--no-send", action="store_true", help="只生成不发邮件")
    ap.add_argument("--reuse-cache", action="store_true", help="优先用今日缓存，缺失才抓取")
    ap.add_argument("--from-cache", metavar="JSON", help="用已有 bundle 渲染，不重新抓取")
    args = ap.parse_args()

    cfg = common.load_config()

    if args.from_cache:
        with open(args.from_cache, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        # 确保有数据日期字段（兼容旧缓存）
        if "data_date" not in bundle:
            bundle["data_date"] = _resolve_data_date(bundle)
            bundle["data_weekday"] = _weekday_cn(bundle["data_date"])
            bundle["data_date_label"] = _data_date_label(bundle["data_date"])
        # 若缓存里缺总结/总评且 Ollama 可用，就地补齐（本地、零云端 token）
        llm.fill_llm_texts(cfg, bundle)
    else:
        bundle = fetch_bundle(cfg, reuse_cache=args.reuse_cache)

    common.log.info("=== 渲染HTML ===")
    html = render.render_report(bundle)
    bundle_path, html_path = save_outputs(cfg, bundle, html)
    common.log.info("已保存: %s", html_path)

    if not args.no_send:
        common.log.info("=== 发送邮件 ===")
        subject = f"每日市场报告 {datetime.now(TZ_CN):%Y-%m-%d}"
        ok = mail.send_report(cfg, html_path, subject)
        if ok:
            common.log.info("发送结果: 成功")
        else:
            common.log.error("发送结果: 失败！请先 `agently-cli auth login` 续期，"
                             "再 `./run.sh --from-cache %s` 补发", bundle_path)
    else:
        common.log.info("--no-send，跳过发送")

    print(f"\n报告: {html_path}")
    print(f"数据: {bundle_path}")
    return html_path


if __name__ == "__main__":
    main()
