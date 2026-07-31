"""博主观点：用 twitter-cli 拉取指定博主前一天推文。

抓取后由 lib.llm.fill_llm_texts 用本机 Ollama 生成本地中文总结（entry["summary"]），
不消耗云端 token；若 Ollama 不可用则 summary 留空，渲染时回退展示原文。"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any

from . import common

log = common.log
TZ_CN = timezone(timedelta(hours=8))


def _twitter_bin() -> str:
    # 优先 venv 里的 twitter；其次系统 twitter
    cand = Path(sys.executable).parent / "twitter"
    if cand.exists():
        return str(cand)
    return "twitter"


def _run_twitter(handle: str, max_posts: int, out_file: Path, tw_cfg: dict) -> str:
    env = dict(os.environ)
    if tw_cfg.get("auth_token"):
        env["TWITTER_AUTH_TOKEN"] = tw_cfg["auth_token"]
    if tw_cfg.get("ct0"):
        env["TWITTER_CT0"] = tw_cfg["ct0"]
    bin_ = _twitter_bin()
    cmd = [bin_, "user-posts", handle, "--max", str(max_posts), "--full-text", "--json", "-o", str(out_file)]
    log.info("拉取推文: %s", " ".join(cmd[:4]))
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return ""
    if r.returncode != 0:
        log.warning("twitter-cli 返回 %d: %s", r.returncode, r.stderr[:300])
        return ""
    return out_file.read_text(encoding="utf-8") if out_file.exists() else r.stdout


def _parse_posts(raw: str) -> List[Dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa
        return []
    items = data if isinstance(data, list) else data.get("tweets") or data.get("posts") or data.get("data") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # 文本：兼容 full_text / text / content
        text = it.get("full_text") or it.get("text") or it.get("content") or it.get("note_tweet") or ""
        # 时间：兼容驼峰 createdAtISO / createdAt 以及下划线形式
        created = (it.get("createdAtISO") or it.get("createdAt") or it.get("created_at")
                   or it.get("time") or it.get("created_at_str") or it.get("date") or "")
        tid = it.get("id") or it.get("tweet_id") or it.get("id_str") or ""
        # 互动量：twitter-cli 把 likes/retweets/views 嵌在 metrics 里
        metrics = it.get("metrics") or {}
        likes = it.get("favorite_count") or it.get("likes") or metrics.get("likes") or 0
        reposts = it.get("retweet_count") or it.get("retweets") or metrics.get("retweets") or 0
        views = it.get("view_count") or it.get("views") or metrics.get("views") or 0
        url = it.get("url") or (f"https://x.com/i/status/{tid}" if tid else "")
        if text:
            out.append({"id": str(tid), "text": text, "created": str(created),
                        "likes": int(likes or 0), "reposts": int(reposts or 0),
                        "views": int(views or 0), "url": url})
    return out


def _parse_time(created: str):
    """尽力解析推文时间，返回 datetime 或 None。"""
    if not created:
        return None
    s = created.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%a %b %d %H:%M:%S %z %Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_CN)
            return dt
        except Exception:  # noqa
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_CN)
        return dt
    except Exception:  # noqa
        return None


def get_bloggers(cfg: dict, data_date: str = None) -> dict:
    tw_cfg = cfg.get("twitter", {})
    lookback = tw_cfg.get("lookback_days", 1)
    max_posts = tw_cfg.get("max_posts", 40)
    cache_dir = common.ROOT / cfg["paths"].get("cache_dir", "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_tag = datetime.now(TZ_CN).strftime("%Y%m%d")

    # 目标日期：优先用报告数据日期（即该交易日当天的推文）；
    # 未提供时回退为「生成时刻往前 lookback 天」。
    if data_date:
        target_days = [data_date]
    else:
        target_days = []
        for i in range(1, lookback + 1):
            target_days.append((datetime.now(TZ_CN) - timedelta(days=i)).strftime("%Y-%m-%d"))

    has_auth = bool(tw_cfg.get("auth_token") and tw_cfg.get("ct0"))
    result = {"section": "bloggers", "has_auth": has_auth, "target_days": target_days,
              "data_date": data_date or "", "bloggers": []}

    for b in cfg.get("bloggers", []):
        entry = {"name": b["name"], "handle": b["handle"], "posts": [], "error": None}
        if not has_auth:
            entry["error"] = "未配置 Twitter Cookie(auth_token/ct0)，跳过"
            result["bloggers"].append(entry)
            continue
        out_file = cache_dir / f"twitter_{b['handle']}_{today_tag}.json"
        raw = _run_twitter(b["handle"], max_posts, out_file, tw_cfg)
        posts = _parse_posts(raw)
        # 按目标日期过滤
        filtered = []
        for p in posts:
            dt = _parse_time(p["created"])
            if dt is None:
                continue
            d_cn = dt.astimezone(TZ_CN).strftime("%Y-%m-%d")
            if d_cn in target_days:
                p["date_cn"] = d_cn
                filtered.append(p)
        filtered.sort(key=lambda x: x.get("likes", 0), reverse=True)
        entry["posts"] = filtered
        entry["count"] = len(filtered)
        log.info("博主 @%s: 目标日%s 命中 %d 条", b["handle"], target_days, len(filtered))
        result["bloggers"].append(entry)

    try:
        with open(cache_dir / f"bloggers_{today_tag}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:  # noqa
        pass
    return result


if __name__ == "__main__":
    cfg = common.load_config()
    r = get_bloggers(cfg)
    print(json.dumps({"has_auth": r["has_auth"], "target_days": r["target_days"],
                      "summary": [(b["name"], b.get("count", 0), b.get("error")) for b in r["bloggers"]]},
                     ensure_ascii=False, indent=2))
