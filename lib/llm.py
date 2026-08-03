"""本地 LLM 总结：调用本机 Ollama（默认 qwen2.5:7b）生成博主观点总结与市场总评。

完全本地推理，不消耗任何云端 token。
- Ollama 未启用（config.llm.enabled=false）或不可达时，所有函数返回 None，
  调用方自动回退（博主展示原文、总评留空）。
- 仅使用标准库 urllib，无需额外依赖。
"""
from __future__ import annotations
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Optional
from . import common

log = common.log

_ollama_ok: Optional[bool] = None
_ollama_ok_lock = threading.Lock()


def _cfg(cfg: dict) -> dict:
    return cfg.get("llm") or {}


def _base_url(cfg: dict) -> str:
    return _cfg(cfg).get("base_url", "http://localhost:11434").rstrip("/")


def _model(cfg: dict) -> str:
    return _cfg(cfg).get("model", "qwen2.5:7b")


def _reachable(cfg: dict) -> bool:
    url = _base_url(cfg) + "/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception as e:  # noqa
        log.warning("Ollama 不可达 (%s): %s", _base_url(cfg), e)
        return False


def is_enabled(cfg: dict) -> bool:
    """本地 Ollama 是否可用（带缓存）"""
    global _ollama_ok
    if not _cfg(cfg).get("enabled", False):
        return False
    with _ollama_ok_lock:
        if _ollama_ok is None:
            _ollama_ok = _reachable(cfg)
        return _ollama_ok


def _api_enabled(cfg: dict) -> bool:
    """Claude API 是否已配置且可用"""
    api_cfg = _cfg(cfg).get("api") or {}
    return (api_cfg.get("enabled", False) and
            bool((api_cfg.get("api_key") or "").startswith("sk-")))


def _chat_local(cfg: dict, system: str, user: str,
                num_predict: int = 400, temperature: float = 0.3,
                timeout: int = 120) -> Optional[str]:
    """本地 Ollama 调用（不再盲目重试超时）"""
    url = _base_url(cfg) + "/api/chat"
    payload = {
        "model": _model(cfg),
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return (resp.get("message") or {}).get("content", "").strip() or None
        except (socket.timeout, TimeoutError):
            log.warning("Ollama 超时(第%d次)，放弃", attempt + 1)
            break  # 超时不重试，避免长时间卡死
        except Exception as e:  # noqa
            log.warning("Ollama 调用失败(第%d次): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(2)
    return None


def _chat_api(cfg: dict, system: str, user: str,
              num_predict: int = 400, temperature: float = 0.3) -> Optional[str]:
    """Claude API 调用（Haiku 4.5）"""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic 包未安装，pip install anthropic")
        return None

    api_cfg = _cfg(cfg).get("api") or {}
    api_key = api_cfg.get("api_key", "")
    if not api_key or api_key.startswith("YOUR_"):
        log.warning("Claude API key 未配置，回退到本地")
        return None

    model = api_cfg.get("model", "claude-haiku-4-5-20251001")
    base_url = api_cfg.get("base_url", "")
    try:
        client = anthropic.Anthropic(api_key=api_key, **({"base_url": base_url} if base_url else {}))
        msg = client.messages.create(
            model=model,
            max_tokens=num_predict,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # 跳过 ThinkingBlock，取第一个有 .text 的 block
        text = next((b.text for b in msg.content if hasattr(b, "text")), "")
        return text.strip() or None
    except Exception as e:
        log.warning("Claude API 调用失败: %s", e)
        return None


def _chat(cfg: dict, system: str, user: str,
          num_predict: int = 400, temperature: float = 0.3,
          timeout: int = 120, task: str = "default") -> Optional[str]:
    """路由到 API 或本地，API 失败自动降级"""
    routing = _cfg(cfg).get("task_routing") or {}
    backend = routing.get(task, "local")

    if backend == "api" and _api_enabled(cfg):
        result = _chat_api(cfg, system, user, num_predict, temperature)
        if result is not None:
            return result
        log.warning("API 失败，降级到本地 Ollama [task=%s]", task)

    return _chat_local(cfg, system, user, num_predict, temperature, timeout)


def interpret_chan_signal(cfg: dict, name: str, code: str,
                          point: str, signal: str, trend: str, bias: str,
                          levels_struct: dict) -> Optional[str]:
    """轻量补写：仅把规则引擎已算出的结论 + 多级别结构描述喂给 14B，写一段 ≤60 字缠论解读。

    与 analyze_chan_with_llm（传全量 K 线、易超时）不同，这里不传任何 K 线，prompt 极短，
    14B 可秒级返回。失败/超时返回 None（调用方保留规则引擎自带 reason，不影响信号）。
    """
    if not is_enabled(cfg):
        return None
    ls = levels_struct or {}
    struct_txt = "；".join(f"{k}:{v}" for k, v in ls.items() if v) or "（无）"
    sig_cn = "买点" if signal == "buy" else ("卖点" if signal == "sell" else "无明确买卖点")
    user = (
        f"品种：{name}({code})。规则缠论引擎判定：{sig_cn} = {point or 'None'}；"
        f"大级别方向趋势 = {trend or '—'}；多空倾向 = {bias or '—'}。\n"
        f"多级别结构速览：{struct_txt}。\n"
        f"请基于以上结论，用≤60字中文写一段缠论解读（说明该买卖点在大级别背景下的含义与操作含义），"
        f"只谈该品种、勿引用具体历史日期。"
    )
    system = ("你是缠论技术分析专家。根据已给出的规则引擎结论，用简洁中文写一段≤60字缠论操作解读，"
              "只谈该品种、不引用历史日期、不要 markdown、不要多余解释。")
    return _chat(cfg, system, user, num_predict=120, temperature=0.4, timeout=90,
                 task="interpret_chan_signal")


def _fmt_struct(items) -> str:
    """把模型返回的 levels 子结构（可能是 list[dict] 或 str）压成一句简短中文。"""
    if not items:
        return ""
    if isinstance(items, str):
        return items.strip()[:80]
    if isinstance(items, dict):
        return str(items)[:80]
    if not isinstance(items, list):
        return str(items)[:80]
    parts = []
    for it in items[:3]:
        if not isinstance(it, dict):
            parts.append(str(it)[:40])
            continue
        typ = it.get("类型") or it.get("type") or ""
        rng = it.get("上下界") or it.get("区间") or it.get("range")
        trend = it.get("趋势") or it.get("方向") or it.get("trend") or ""
        s = typ
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            s += f"({rng[0]}–{rng[1]})"
        if trend:
            s += trend
        if s:
            parts.append(s)
    return "; ".join(parts)[:80]


def _normalize_levels(lv) -> dict:
    """把模型返回的 levels（dict / list[dict]，结构可能嵌套）统一成 {30m,1h,daily,weekly,monthly} 字典。"""
    if not lv:
        return {}
    keymap = {"30分钟": "30m", "1小时": "1h", "日线": "daily", "日": "daily",
              "周线": "weekly", "周": "weekly", "月线": "monthly", "月": "monthly",
              "30m": "30m", "1h": "1h", "daily": "daily", "weekly": "weekly", "monthly": "monthly"}

    def _one(period, sub):
        if isinstance(sub, list):
            return _fmt_struct(sub)
        if isinstance(sub, str):
            return sub.strip()[:80]
        if isinstance(sub, dict):
            return str(sub)[:80]
        return ""

    if isinstance(lv, dict):
        out = {}
        for k, v in lv.items():
            nk = keymap.get(str(k).strip(), str(k).strip())
            out[nk] = _one(k, v)
        return out
    if isinstance(lv, list):
        out = {}
        for item in lv:
            if not isinstance(item, dict):
                continue
            period = sub = None
            for k, v in item.items():
                ks = str(k)
                if ks in ("周期", "级别", "时间周期", "level", "timeframe", "tf", "period"):
                    period = str(v)
                elif ks in ("结构", "结构描述", "描述", "desc", "description", "summary", "详情"):
                    sub = v
            if period:
                out[keymap.get(period, period)] = _one(period, sub)
        return out
    return {}


def analyze_chan_with_llm(cfg: dict, products: list[dict]) -> list[dict]:
    """用本地大模型（Ollama）对全部期货品种做多级别联立缠论分析（30分钟→1小时→日线→周线→月线），只输出当天信号。

    参数 products: 每个元素含 {code, name, timeframes(各周期K线), change_pct, inflow, last_date}。
    用线程池并发提交大模型以控制总耗时；模型以 30分钟/1小时 为操作级找买卖点，日/周/月 定方向背景，
    仅基于最新一个交易日(当天)的 30分钟/1小时 K线给出买卖点，禁止引用当天之前的旧日期。
    """
    import concurrent.futures as cf
    if not is_enabled(cfg):
        log.warning("Ollama 未启用，缠论大模型分析跳过")
        return []
    if not products:
        return []

    concurrency = int((cfg.get("futures") or {}).get("chan_concurrency", 6))
    concurrency = max(1, min(concurrency, 12))

    system = (
        "你是缠论（缠中说禅）技术分析专家，擅长多级别联立分析期货主力合约。\n"
        "分析框架：①K线包含合并；②严格顶/底分型；③笔（相邻反向分型，间隔≥1根）；"
        "④中枢（连续三笔重叠[zd,zg]）；⑤MACD红绿柱面积背驰；⑥买卖点。\n"
        "多级别联立：以 30分钟、1小时 为操作级别寻找买卖点；日线、周线、月线 用于判定大级别"
        "趋势方向与背景（如月线多头中、周线中枢震荡、日线回调等），高级别只作结构与方向背景。\n"
        "买卖点定义：下跌末端底背驰=一买；一买后回踩不破前低=二买（形成中亦可）；"
        "上涨末端顶背驰=一卖；一卖后反弹不过前高=二卖；中枢下沿底分型=类二买，上沿顶分型=类二卖。\n"
        "判定原则：只要最近走势出现较清晰的买卖点 setup（含形成中的二买/二卖、类二买/类二卖、中枢突破）就给信号，勿过度保守。\n"
        "【输出要求·必填】必须输出【一个对象数组(可含1条)】，且每条必须包含以下【全部】字段：\n"
        "  levels —— JSON对象，键固定为 30m/1h/daily/weekly/monthly，每值为一句≤18字中文结构描述"
        "（如'中枢震荡区间7498–7562'、'上涨一笔中'、'底背驰'、'回调一笔'）；五个周期都必须填写，禁止空缺、禁止嵌套数组；\n"
        "  trend（上涨/下跌/震荡）、bias（偏多/偏空/观望）、reason（有信号≤50字理由，无信号写'当日无明确买卖点'）。\n"
        "【买卖点】买卖点信号只能出现在【最新一个交易日(当天)】的 30分钟 或 1小时 K线；"
        "高级别(日/周/月)仅作背景，严禁把过去某日的旧买卖点当作当日信号输出（出现\"6月8日\"这类历史分析一律禁止）。"
        "若当天 30m/1h 出现买卖点，填 point/signal（按定义）；若无，point='None'、signal='none'。\n"
        "禁止输出空数组 []，禁止任何 markdown 与解释文字，只输出 JSON 数组。字段：\n"
        '[{"code":"品种代码","name":"品种名","signal":"buy|sell|none","point":"一买|二买|一卖|二卖|类二买|类二卖|None",'
        '"trend":"上涨|下跌|震荡","bias":"偏多|偏空|观望","signal_price":float,'
        '"reason":"≤50字中文理由(只谈当天30分钟/1小时信号;无信号可写\'当日无明确买卖点\')",'
        '"levels":{"30m":"结构描述","1h":"结构描述","daily":"结构描述","weekly":"结构描述","monthly":"结构描述"}}]'
    )

    LEVEL_ORDER = [("30m", "datetime", "30分钟"), ("1h", "datetime", "1小时"),
                   ("daily", "date", "日线"), ("weekly", "date", "周线"), ("monthly", "date", "月线")]
    LEVEL_BARS = {"30m": 24, "1h": 24, "daily": 30, "weekly": 15, "monthly": 8}

    def _serialize(p):
        tfs = p.get("timeframes") or {}
        blocks = [f"品种：{p['name']}({p['code']})  多级别缠论联立（30分钟/1小时为操作级，日/周/月为方向背景）",
                  f"数据日期：{p.get('last_date')}（30分钟/1小时K线最右侧即为该交易日）"]
        for lvl, dtkey, label in LEVEL_ORDER:
            bars = tfs.get(lvl) or []
            bars = bars[-LEVEL_BARS.get(lvl, 40):]
            if not bars:
                continue
            lines = [f"== {label}({lvl}) 最近{len(bars)}根(右=最新) =="]
            for b in bars:
                d = b.get(dtkey) or b.get("date") or b.get("datetime")
                lines.append(f"{d},{b['open']},{b['high']},{b['low']},{b['close']},{b.get('volume', 0)}")
            blocks.append("\n".join(lines))
        return "\n".join(blocks) + "\n---"

    def _chan_one(p: dict) -> list:
        """调用 LLM 分析单个品种，返回解析后的机会列表（兼容单对象/数组/代码块）。"""
        user = (_serialize(p) +
                "\n\n请对该品种做多级别联立分析（30分钟/1小时为操作级，日/周/月为方向背景），"
                "判断【当天】30分钟或1小时是否出现缠论买卖点 setup，并给出各周期 levels 具体结构。"
                "无论是否有当日买卖点，均须输出一个含 levels 的对象数组（可含1条）；无当日信号时 point=\"None\"、signal=\"none\"。"
                "仅输出 JSON 数组：")
        raw = _chat(cfg, system, user, num_predict=500, temperature=0.2,
                    task="analyze_chan_with_llm")
        if not raw:
            return []
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
        except Exception:
            try:
                i, j = text.find("["), text.rfind("]")
                data = json.loads(text[i:j + 1]) if i >= 0 and j > i else []
            except Exception as e2:
                log.warning("缠论 LLM JSON 解析失败[%s]: %s", p["code"], e2)
                return []
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    # 并发分析全部品种（线程池），降低总耗时
    def _build(p, o):
        point = str(o.get("point") or "").strip()
        signal = str(o.get("signal") or "").strip().lower()
        has_signal = False
        if signal in ("buy", "sell"):
            has_signal = True
        elif "买" in point:
            signal = "buy"
            has_signal = True
        elif "卖" in point:
            signal = "sell"
            has_signal = True
        if point in ("None", "none", "", "无"):
            point = "None"
        code = o.get("code") or o.get("symbol") or p["code"]
        score = 80 if ("一买" in point or "一卖" in point) else 65
        tf = p.get("timeframes") or {}
        last_bars = tf.get("30m") or tf.get("1h") or []
        last_close = last_bars[-1]["close"] if last_bars else None
        return {
            "code": code,
            "name": o.get("name") or p["name"],
            "last_close": last_close,
            "chan": {
                "signal": signal,
                "point": point,
                "trend": o.get("trend"),
                "bias": o.get("bias"),
                "signal_price": o.get("signal_price"),
                "reason": o.get("reason", ""),
                "levels": _normalize_levels(o.get("levels")),
                "has_signal": has_signal,
                "fresh": True,
                "score": score if has_signal else 0,
            }
        }

    opportunities = []
    total = len(products)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        future_to_p = {ex.submit(_chan_one, p): p for p in products}
        for fut in cf.as_completed(future_to_p):
            p = future_to_p[fut]
            done += 1
            try:
                opps = fut.result() or []
                for o in opps:
                    if isinstance(o, dict):
                        opportunities.append(_build(p, o))
            except Exception as e:  # noqa
                log.warning("缠论 LLM 分析 %s 失败: %s", p["code"], e)
            if done % 10 == 0 or done == total:
                nh = sum(1 for x in opportunities if x.get("chan", {}).get("has_signal"))
                log.info("缠论 LLM 进度 %d/%d，已发现 %d 个当日信号", done, total, nh)
    return opportunities

def summarize_blogger(cfg: dict, entry: dict) -> Optional[str]:
    """把博主当日推文（英文为主）提炼为详尽中文观点总结，明确态度立场。"""
    posts = [p for p in entry.get("posts", []) if (p.get("text") or "").strip()]
    if not posts:
        return None
    lines = []
    for p in posts[:15]:  # 从8条提升到15条，避免遗漏后续关键推文
        meta = p.get("date_cn", "")
        lines.append(f"[{meta}] {p['text'].strip()}")
    corpus = "\n\n".join(lines)
    name = entry.get("name", "")
    handle = entry.get("handle", "")
    system = ("你是资深金融市场研究员，熟悉美股、半导体、AI 算力、大宗商品与宏观。"
              "下列是 X 博主在目标日期的推文（英文为主）。请输出一段详尽的中文观点总结，要求：\n"
              "1) 先一句话点明博主当日总体立场与情绪（例如：坚定看多 AI 光互联、对估值泡沫有所警惕、"
              "明确偏空韩国股市等）——必须写出态度，不能只罗列事实；\n"
              "2) 按主题分 4-7 条（每条以 '- ' 开头）：每条先写博主的具体观点，"
              "再用『态度：看多/看空/观望/中性』标注其倾向与依据；\n"
              "3) 保留关键代码（$AAPL）、人名、公司名与关键数字；严禁编造文中没有的数据或观点；\n"
              "4) 若推文涉及操作含义（止盈、建仓、风险提示、目标价），明确点出博主的处理方式。\n"
              "请使用中文。")
    user = (f"博主：{name} (@{handle})\n\n"
            f"推文原文：\n{corpus}\n\n请输出该博主的观点总结（先总体立场，再分主题要点含态度标注）：")
    return _chat(cfg, system, user, num_predict=1200, temperature=0.3,
                 task="summarize_blogger")


def summarize_market(cfg: dict, bundle: dict) -> Optional[str]:
    """根据结构化行情数据写一段市场总评。"""
    fut = bundle.get("futures", {})
    parts: list[str] = []

    def _fmt(r):
        cp = r.get("change_pct") or 0
        inflow = r.get("inflow") or 0
        direction = "流入" if inflow >= 0 else "流出"
        return (f"{r['name']}(涨跌{cp:+.2f}%, 资金{direction}"
                f"{common.fmt_signed_money(inflow)})")

    for label, key in (("期货涨幅前", "by_change"), ("期货跌幅前", "by_change_desc"),
                       ("资金流入前", "by_inflow"), ("资金流出前", "by_outflow")):
        rows = fut.get(key, [])[:3]
        if rows:
            parts.append(label + ": " + "; ".join(_fmt(r) for r in rows))

    opps = fut.get("opportunities", [])
    if opps:
        op = "; ".join(f"{r['name']}({r.get('chan', {}).get('signal')}点"
                       f"{r.get('chan', {}).get('point')})" for r in opps)
        parts.append("缠论机会: " + op)

    sec = bundle.get("sectors", {})
    for b in sec.get("boards", []):
        # 同时支持 Eastmoney (top_gain) 和 Sina (top_gain) 格式
        tg = (b.get("top_gain") or [])[:3]
        if tg and tg[0].get("change_pct") is not None:
            parts.append(f"{b['name']}领涨: " + "; ".join(
                f"{r['name']}({r.get('change_pct', 0):+.2f}%)" for r in tg))

    # 添加 ETF 领涨数据
    etf_data = bundle.get("etf") or {}
    etf_top = (etf_data.get("by_change") or [])[:3]
    if etf_top and etf_top[0].get("change_pct") is not None:
        parts.append("ETF领涨: " + "; ".join(
            f"{e.get('name', e.get('code', ''))}({e['change_pct']:+.2f}%)"
            for e in etf_top))

    if not parts:
        return None
    corpus = "\n".join(parts)
    system = ("你是市场日报编辑。下方是结构化的当日行情数据，已含涨跌幅与资金流入/流出（正数=流入，负数=流出，单位：亿/万）。\n"
              "请据此写一段 150 字以内的中文总评，覆盖期货与股市板块主要特征与资金动向。严格规则：\n"
              "1) 只使用输入中明确给出的数字，严禁改动任何正负号，严禁编造输入中未出现的品种或数值；\n"
              "2) 不写任何投资建议或预测；\n"
              "3) 每条以 '- ' 开头分行。")
    user = f"数据日期：{bundle.get('data_date')}\n\n行情摘要：\n{corpus}\n\n总评："
    return _chat(cfg, system, user, num_predict=400, temperature=0.1,
                 task="summarize_market")


def summarize_us(cfg: dict, bundle: dict) -> Optional[str]:
    """根据美股板块/个股涨跌与当日新闻标题，写一段中文美股概览。"""
    us = bundle.get("us_stocks", {})
    if not us.get("ok"):
        return None
    parts: list[str] = []

    sectors = sorted([s for s in us.get("sectors", []) if s.get("change_pct") is not None],
                     key=lambda x: x.get("change_pct") or 0, reverse=True)
    if sectors:
        top = "; ".join(f"{r['name_cn']}({r['change_pct']:+.2f}%)" for r in sectors[:3])
        bot = "; ".join(f"{r['name_cn']}({r['change_pct']:+.2f}%)" for r in sectors[-3:])
        parts.append(f"板块领涨: {top}")
        parts.append(f"板块领跌: {bot}")
    for s in us.get("stocks", []):
        cp = s.get("change_pct")
        if cp is None:
            continue
        parts.append(f"{s.get('name_cn') or s['symbol']}({s['symbol']}) 涨跌{cp:+.2f}%")
    # 新闻要点（每只取最相关前 2 条标题，避免编造）
    for s in us.get("stocks", []):
        items = us.get("news", {}).get(s["symbol"], [])
        matched = [n for n in items if n.get("matched")]
        head = (matched or items)[:2]
        if head:
            titles = "; ".join(n.get("title", "") for n in head)
            parts.append(f"{s['symbol']} 当日新闻: {titles}")

    if not parts:
        return None
    corpus = "\n".join(parts)
    if not us.get("quotes_ok"):
        # 行情接口失败：corpus 中无涨跌数字，改用规则模板直接罗列新闻，避免模型臆测涨跌
        return _us_news_fallback(us)
    system = ("你是市场日报编辑。下方是结构化的当日美股数据，已含板块与个股涨跌幅（百分比，正=涨负=跌）。请使用中文。\n"
              "请写一段 120 字以内的中文美股概览，覆盖板块强弱与重点个股表现，并点出新闻主线。严格规则：\n"
              "1) 只使用输入中明确给出的数字与新闻标题，严禁改动正负号，严禁编造未出现的品种、数值或新闻；\n"
              "2) 不写投资建议或股价预测；\n"
              "3) 每条以 '- ' 开头分行。")
    user = f"数据日期：{us.get('date')}\n\n美股摘要：\n{corpus}\n\n概览："
    return _chat(cfg, system, user, num_predict=350, temperature=0.1,
                 task="summarize_us")


def _us_news_fallback(us: dict) -> str:
    """行情缺失时的兜底：仅罗列当日新闻标题，不做任何涨跌判断，零幻觉。"""
    lines = ["（美股行情数据暂缺，以下基于当日新闻）"]
    for s in us.get("stocks", []):
        items = us.get("news", {}).get(s["symbol"], []) or []
        head = items[:2]
        if head:
            titles = "；".join(n.get("title", "") for n in head)
            lines.append(f"- {s.get('name_cn') or s['symbol']}({s['symbol']})：{titles}")
    return "\n".join(lines)


def fill_llm_texts(cfg: dict, bundle: dict) -> dict:
    """并发补齐所有 LLM 摘要（博主/总评/美股/公众号）；本地+API 均不可用时跳过。"""
    import concurrent.futures as cf

    if not is_enabled(cfg) and not _api_enabled(cfg):
        return bundle

    # 收集待执行任务：(label, target_obj, key, callable)
    pending: list[tuple] = []

    for entry in bundle.get("bloggers", {}).get("bloggers", []):
        if not entry.get("summary") and entry.get("posts"):
            pending.append((f"@{entry.get('handle')}", entry, "summary",
                            lambda e=entry: summarize_blogger(cfg, e)))

    if not bundle.get("commentary"):
        pending.append(("market_commentary", bundle, "commentary",
                        lambda: summarize_market(cfg, bundle)))

    us = bundle.get("us_stocks") or {}
    if us.get("ok") and not us.get("us_summary"):
        pending.append(("us_summary", us, "us_summary",
                        lambda: summarize_us(cfg, bundle)))

    if not pending:
        return bundle

    log.info("LLM 并发生成 %d 个摘要...", len(pending))
    with cf.ThreadPoolExecutor(max_workers=min(6, len(pending))) as pool:
        futures = {pool.submit(fn): (label, obj, key)
                   for label, obj, key, fn in pending}
        for fut in cf.as_completed(futures):
            label, obj, key = futures[fut]
            try:
                result = fut.result()
                if result:
                    obj[key] = result
                    log.info("已生成: %s", label)
            except Exception as e:  # noqa
                log.warning("LLM 摘要失败 [%s]: %s", label, e)

    return bundle
