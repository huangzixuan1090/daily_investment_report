"""公众号文章总结监控：使用带会话的 requests 访问 Sogou 微信搜索，降低反爬概率。

说明：
- 微信公众号无公开免费 API。本模块使用 Sogou 微信搜索（type=2）按账号名检索。
- 与原 urllib 单次请求不同，这里改用 requests.Session：先访问 Sogou 首页建立 Cookie，
  再携带完整浏览器头执行搜索，显著降低被反爬/验证码拦截的概率。
- 保留「仅取前一天发布的文章」的过滤逻辑；若搜索结果中无法精确匹配到该账号自身文章，
  则回退到关键词相关文章并标记 indirect。
- 总结基于文章标题+摘要，由本地 Ollama 生成（见 lib/llm.summarize_wechat）。
"""
from __future__ import annotations
import html as _html
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from . import common

TZ_CN = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
log = common.log


class _SogouClient:
    """带会话的 Sogou 微信搜索客户端，先预热首页再搜索，降低反爬。"""

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        })
        self._warmed = False

    def _warmup(self) -> bool:
        if self._warmed:
            return True
        try:
            r = self.s.get("https://weixin.sogou.com/", timeout=15, allow_redirects=True)
            self._warmed = r.status_code == 200
            return self._warmed
        except Exception as e:  # noqa
            log.warning("Sogou 首页预热失败: %s", e)
            return False

    def get(self, url: str, timeout: int = 20) -> str:
        import requests
        self._warmup()
        r = self.s.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return _html.unescape(r.text)


_sogou = _SogouClient()


def _ts_to_date(ts: str) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=TZ_CN).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _parse_results(page: str) -> List[Dict[str, str]]:
    """解析 Sogou 结果页，返回文章列表（含来源账号）。"""
    blocks = re.findall(r'<div class="txt-box">(.*?)</div>\s*</div>', page, re.S)
    arts: List[Dict[str, str]] = []
    for b in blocks:
        m = re.search(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        link = m.group(1)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if not title:
            continue
        src = re.search(r'<span class="all-time-y2"[^>]*>(.*?)</span>', b, re.S)
        srcname = re.sub(r"<[^>]+>", "", src.group(1)).strip() if src else ""
        ts = re.search(r"timeConvert\('(\d+)'\)", b)
        date = _ts_to_date(ts.group(1)) if ts else ""
        sn = re.search(r'class="txt-info"[^>]*>(.*?)</p>', b, re.S)
        snip = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", sn.group(1))).strip() if sn else ""
        arts.append({
            "title": title,
            "url": ("https://weixin.sogou.com" + link) if link.startswith("/") else link,
            "date": date,
            "snippet": snip,
            "source": srcname,
        })
    return arts


def _source_match(src: str, account: str, aliases: List[str] = None) -> bool:
    """模糊匹配来源账号：与显示名或任一 alias 互相包含/去除后缀后相等。"""
    if not src:
        return False
    s = src.strip().lower()
    candidates = [account.strip().lower()]
    if aliases:
        candidates += [a.strip().lower() for a in aliases if a and a.strip()]
    for a in candidates:
        if s == a:
            return True
        if a in s or s in a:
            return True
        # 去掉常见后缀再比较
        s2 = re.sub(r"(的分享圈|official|公众号|订阅号|服务号)", "", s).strip()
        a2 = re.sub(r"(的分享圈|official|公众号|订阅号|服务号)", "", a).strip()
        if s2 == a2 or a2 in s2 or s2 in a2:
            return True
    return False


def _normalize_account(acc):
    """统一账号配置：字符串 -> {name, aliases}。"""
    if isinstance(acc, dict):
        name = acc.get("name", "").strip()
        aliases = [a.strip() for a in acc.get("aliases", []) if a and a.strip()]
        return {"name": name, "aliases": aliases}
    return {"name": str(acc).strip(), "aliases": []}


def _search_account(account: str, max_articles: int, aliases: List[str] = None,
                    data_date: str = None, lookback_days: int = 1) -> List[Dict[str, str]]:
    """检索某公众号文章，按优先级取结果。

    优先级（从高到低）：
    1. 来源匹配该账号（含 aliases）且在目标日期窗口内的文章；
    2. 来源匹配该账号但不在窗口内、落在最近 7 天的文章；
    3. 关键词相关且在目标日期窗口内的文章；
    4. 关键词相关且落在最近 7 天的文章。

    这样可避免「无关高排名文章把目标账号文章挤出」的问题。
    """
    aliases = aliases or []
    url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(account)
    try:
        page = _sogou.get(url, timeout=25)
    except Exception as e:  # noqa
        log.warning("公众号搜索 %s 失败: %s", account, e)
        return []
    if "antispider" in page or "请输入验证码" in page or "访问过于频繁" in page:
        log.warning("公众号搜索 %s 触发反爬/验证码", account)
        return []
    arts = _parse_results(page)
    if not arts:
        return []

    # 按日期倒序
    arts = sorted(arts, key=lambda x: x.get("date", ""), reverse=True)

    base = data_date or datetime.now(TZ_CN).strftime("%Y-%m-%d")
    try:
        base_dt = datetime.strptime(base, "%Y-%m-%d").date()
    except Exception:
        base_dt = datetime.now(TZ_CN).date()
    target_dates = [(base_dt - timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range(max(1, lookback_days))]
    cutoff = (base_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    owned = [a for a in arts if _source_match(a.get("source", ""), account, aliases)]
    others = [a for a in arts if a not in owned]

    # 优先级 1：账号自身 + 窗口内
    pool = [a for a in owned if a.get("date") in target_dates]
    # 优先级 2：账号自身 + 最近 7 天
    if not pool:
        pool = [a for a in owned if a.get("date") and a["date"] >= cutoff]
        if pool:
            log.info("公众号 %s 窗口 %s 无自身文章，回退到最近 7 天", account, target_dates)
    # 优先级 3：关键词相关 + 窗口内
    if not pool:
        pool = [a for a in others if a.get("date") in target_dates]
    # 优先级 4：关键词相关 + 最近 7 天
    if not pool:
        pool = [a for a in others if a.get("date") and a["date"] >= cutoff]
        if pool:
            log.info("公众号 %s 窗口 %s 无相关文章，回退到最近 7 天关键词结果", account, target_dates)

    # 去重
    seen = set()
    uniq = []
    for a in pool:
        if a["title"] in seen:
            continue
        seen.add(a["title"])
        uniq.append(a)
        if len(uniq) >= max_articles:
            break
    return uniq


def get_wechat_articles(cfg: dict, data_date: str = None, max_articles: int = None) -> dict:
    wc = cfg.get("wechat") or {}
    accounts = wc.get("accounts") or []
    if max_articles is None:
        max_articles = int(wc.get("max_articles", 6))
    lookback = int(wc.get("lookback_days", 1))
    if not accounts:
        return {"ok": False, "error": "未配置 wechat.accounts", "accounts": []}

    # 目标日期窗口：以报告数据日期为基准，向前回退 lookback_days 天
    base_date = data_date or datetime.now(TZ_CN).strftime("%Y-%m-%d")
    try:
        base = datetime.strptime(base_date, "%Y-%m-%d").date()
    except Exception:
        base = datetime.now(TZ_CN).date()
    target_dates = [(base - timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range(lookback)]

    out_accounts = []
    any_ok = False
    for acc in accounts:
        norm = _normalize_account(acc)
        name = norm["name"]
        aliases = norm["aliases"]
        manual_articles = norm.get("manual_articles") or []
        # 规范化手动文章：补默认 source/date，标记 manual
        manual_arts = []
        for ma in manual_articles:
            if not ma or not ma.get("title"):
                continue
            manual_arts.append({
                "title": str(ma.get("title", "")).strip(),
                "url": str(ma.get("url", "")).strip(),
                "date": str(ma.get("date", base_date)).strip(),
                "source": str(ma.get("source", name)).strip(),
                "snippet": str(ma.get("snippet", "")).strip(),
                "manual": True,
            })
        try:
            arts = _search_account(name, max_articles=max_articles, aliases=aliases,
                                   data_date=base_date, lookback_days=lookback)

            # 手动文章兜底：Sogou 索引延迟/遗漏时，合并用户粘贴的文章
            if manual_arts:
                combined = {a.get("url") or a.get("title"): a for a in manual_arts}
                for a in arts:
                    key = a.get("url") or a.get("title")
                    if key and key not in combined:
                        combined[key] = a
                arts = sorted(combined.values(), key=lambda x: x.get("date", ""), reverse=True)
                arts = arts[:max_articles]

            # 若没有任何一篇来源匹配该账号（含 aliases），标记为间接检索
            indirect = not any(_source_match(a.get("source", ""), name, aliases) for a in arts)
            # 标出哪些文章不在目标日期窗口内（回退所得）
            for a in arts:
                a["in_window"] = a.get("date") in target_dates
            out_accounts.append({"name": name, "articles": arts,
                                 "aliases": aliases,
                                 "manual_articles": manual_arts,
                                 "error": None, "indirect": indirect,
                                 "target_date": base_date,
                                 "target_dates": target_dates,
                                 "source": "sogou_session"})
            if arts:
                any_ok = True
            log.info("公众号 %s 抓取 %d 篇 (窗口 %s, indirect=%s, manual=%d, 机制=sogou_session)",
                     name, len(arts), target_dates, indirect, len(manual_arts))
        except Exception as e:  # noqa
            out_accounts.append({"name": name, "aliases": aliases,
                                 "manual_articles": manual_arts,
                                 "articles": [], "error": str(e)[:120],
                                 "target_date": base_date,
                                 "target_dates": target_dates})
        time.sleep(2)  # 降低 Sogou 请求频率

    # 写缓存（供 --reuse-cache 复用）
    try:
        cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        tag = datetime.now(TZ_CN).strftime("%Y%m%d")
        with open(cache_dir / f"wechat_{tag}.json", "w", encoding="utf-8") as f:
            json.dump({"ok": any_ok, "accounts": out_accounts, "error": None},
                      f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass

    return {"ok": any_ok, "accounts": out_accounts, "error": None}


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_wechat_articles(cfg)
    print(json.dumps({"ok": r["ok"], "accounts": [
        {"name": a["name"], "count": len(a["articles"]),
         "indirect": a.get("indirect"),
         "source": a.get("source")}
        for a in r["accounts"]
    ]}, ensure_ascii=False, indent=2))
