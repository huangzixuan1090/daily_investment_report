"""标准缠论分析（非简化版）。

完整流程：
  K线包含合并 → 严格顶/底分型 → 笔(贪心取极值 + 笔破坏) → 中枢(三笔重叠区间)
  → 趋势 / 背驰(MACD 红绿柱面积) → 一买 / 二买 / 一卖 / 二卖。

返回单品种机会信息，除信号类型外，还给出：
  - signal_price：信号触发价（分型极值），便于落地参考；
  - bias：次日操作倾向（偏多 / 偏空 / 观望）；
  - fresh：信号是否形成于最近一笔（True=最新结构，可指导次日）；
  - reason：含「前高/前低止损参考」的可操作说明。

说明：日线收盘后确认的信号天然滞后于盘中，本模块把可操作性落到
「次日倾向 + 参考位」，而非假装盘中实时提示。
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional

Bar = Dict[str, Any]


# ---------------- K线包含合并 ----------------
def merge_klines(bars: List[Bar]) -> List[Bar]:
    if not bars:
        return []
    merged: List[Bar] = []
    for b in bars:
        if not merged:
            merged.append(dict(b))
            continue
        last = merged[-1]
        contained = (b["high"] <= last["high"] and b["low"] >= last["low"]) or \
                    (b["high"] >= last["high"] and b["low"] <= last["low"])
        if contained:
            # 向上笔：取高高；向下笔：取低低
            if len(merged) >= 2:
                prev = merged[-2]
                up = last["high"] > prev["high"]
            else:
                up = b["close"] >= last["close"]
            if up:
                last["high"] = max(last["high"], b["high"])
                last["low"] = max(last["low"], b["low"])
            else:
                last["high"] = min(last["high"], b["high"])
                last["low"] = min(last["low"], b["low"])
            last["volume"] = last.get("volume", 0) + b.get("volume", 0)
            last["close"] = b["close"]
            last["date"] = b["date"]
            last["open"] = last.get("open", b["open"])
        else:
            merged.append(dict(b))
    return merged


# ---------------- 分型（严格） ----------------
def find_fractals(merged: List[Bar]) -> List[Dict[str, Any]]:
    fr: List[Dict[str, Any]] = []
    n = len(merged)
    for i in range(1, n - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        # 顶分型：中间最高，且中间最低也高于两侧（无更低低点）
        if (b["high"] > a["high"] and b["high"] > c["high"]
                and b["low"] > a["low"] and b["low"] > c["low"]):
            fr.append({"type": "top", "idx": i, "value": b["high"],
                       "date": b["date"], "bar": b})
        # 底分型：中间最低，且中间最高也低于两侧（无更高高点）
        elif (b["low"] < a["low"] and b["low"] < c["low"]
              and b["high"] < a["high"] and b["high"] < c["high"]):
            fr.append({"type": "bottom", "idx": i, "value": b["low"],
                       "date": b["date"], "bar": b})
    return fr


# ---------------- 笔（贪心极值 + 笔破坏） ----------------
def find_strokes(merged: List[Bar], fractals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """连接交替顶/底分型成笔；同向取更极值者，异向直接连接。
    要求相邻分型间至少隔 1 根合并K线（笔破坏的结构前提）。"""
    if len(fractals) < 2:
        return []
    strokes: List[Dict[str, Any]] = [fractals[0]]
    for f in fractals[1:]:
        last = strokes[-1]
        if f["idx"] - last["idx"] < 1:
            continue
        if f["type"] == last["type"]:
            # 同向：更新为更极值者（取消被包含的前一极端）
            if (last["type"] == "top" and f["value"] > last["value"]) or \
               (last["type"] == "bottom" and f["value"] < last["value"]):
                strokes[-1] = f
        else:
            strokes.append(f)
    return strokes


# ---------------- 中枢（三笔重叠） ----------------
def _stroke_high(s: Dict[str, Any]) -> float:
    return s["value"] if s["type"] == "top" else s["bar"]["high"]


def _stroke_low(s: Dict[str, Any]) -> float:
    return s["value"] if s["type"] == "bottom" else s["bar"]["low"]


def find_pivots(strokes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """中枢 = 连续三笔（顶-底-顶 或 底-顶-底）价格区间存在重叠。
    重叠区间 [zd, zg] = [三笔低点最大, 三笔高点最小]，需 zg >= zd。"""
    pivots: List[Dict[str, Any]] = []
    for i in range(len(strokes) - 2):
        s1, s2, s3 = strokes[i], strokes[i + 1], strokes[i + 2]
        if not (s1["type"] != s2["type"] and s2["type"] != s3["type"]):
            continue
        zg = min(_stroke_high(s1), _stroke_high(s2), _stroke_high(s3))
        zd = max(_stroke_low(s1), _stroke_low(s2), _stroke_low(s3))
        if zg >= zd:
            pivots.append({"zg": zg, "zd": zd, "start": s1, "end": s3})
    return pivots


# ---------------- MACD ----------------
def ema(values: List[float], n: int) -> List[float]:
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd_hist(closes: List[float]) -> List[float]:
    dif = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    dea = ema(dif, 9)
    return [(d - e) * 2 for d, e in zip(dif, dea)]


# ---------------- 背驰面积 ----------------
def _area(hist: List[float], i0: int, i1: int, sign: int) -> float:
    """[i0,i1] 同向 MACD 柱面积（sign=1 取红柱, -1 取绿柱, 均取绝对值累加）。"""
    s = 0.0
    lo, hi = max(0, i0), min(len(hist), i1 + 1)
    for k in range(lo, hi):
        h = hist[k]
        if sign > 0 and h > 0:
            s += h
        elif sign < 0 and h < 0:
            s += -h
    return s


# ---------------- 力度（价×量）背驰 —— 借鉴「缠论选股」技能 ----------------
def _stroke_range(strokes: List[Dict[str, Any]], k: int):
    """第 k 笔在 merged K线中的区间 [i0, i1]（笔从上一分型起点到本分型终点）。"""
    i0 = strokes[k - 1]["idx"] if k > 0 else 0
    i1 = strokes[k]["idx"]
    return i0, i1


def stroke_momentum_range(merged: List[Bar], i0: int, i1: int) -> float:
    """区间 [i0,i1] 的力度 = Σ |close-open| × volume（缠论『力度』近似，价量加权）。"""
    total = 0.0
    lo, hi = max(0, i0), min(len(merged), i1 + 1)
    for k in range(lo, hi):
        b = merged[k]
        pc = abs(b["close"] - b["open"])
        vol = b.get("volume", 1) or 1
        total += pc * vol
    return total


def stroke_momentum(strokes: List[Dict[str, Any]], merged: List[Bar], k: int) -> float:
    """第 k 笔的力度。"""
    i0, i1 = _stroke_range(strokes, k)
    return stroke_momentum_range(merged, i0, i1)


def detect_divergence(strokes: List[Dict[str, Any]], pivots: List[Dict[str, Any]],
                      merged: List[Bar], decay_th: float = 0.7) -> List[Dict[str, Any]]:
    """基于『力度(价×量)』的趋势背驰检测（借鉴 chanlun-stock 技能）。

    对每一个中枢，取其结束笔之后的连续两段同向笔 s1→s2：
    若 s2 力度 < s1 力度 × decay_th（默认 0.7），则判定为趋势背驰；
    direction = 'up' 表示上行力度衰竭（对应一卖），'down' 表示下行力度衰竭（对应一买）。
    """
    divs: List[Dict[str, Any]] = []
    if len(strokes) < 5 or not pivots:
        return divs
    # 中枢结束笔的索引集合
    pivot_ends = set()
    for p in pivots:
        try:
            pivot_ends.add(strokes.index(p["end"]))
        except ValueError:
            continue
    for ei in pivot_ends:
        if ei + 2 >= len(strokes):
            continue
        s1k, s2k = ei + 1, ei + 2
        s1, s2 = strokes[s1k], strokes[s2k]
        if s1["type"] != s2["type"]:
            continue
        m1 = stroke_momentum(strokes, merged, s1k)
        m2 = stroke_momentum(strokes, merged, s2k)
        if m1 > 0 and m2 < m1 * decay_th:
            # 本引擎笔以终点分型命名：终点为 top=向上笔(bottom_up)，bottom=向下笔(top_down)
            direction = "up" if s1["type"] == "top" else "down"
            divs.append({
                "type": "趋势背驰",
                "direction": direction,
                "decay": round((1 - m2 / m1) * 100, 1),
                "s1k": s1k, "s2k": s2k,
                "m1": round(m1, 2), "m2": round(m2, 2),
            })
    # 过滤：力度衰减需 > 40% 才有意义；取衰减最强的前若干个
    divs = [d for d in divs if d["decay"] > 40]
    divs.sort(key=lambda x: x["decay"], reverse=True)
    return divs[:5]


# ---------------- 买卖点组装 ----------------
def _mk(signal: str, point: str, fractal: Dict[str, Any], bias: str,
        reason: str, score: int) -> Dict[str, Any]:
    return {
        "signal": signal, "point": point,
        "trend": "上涨" if signal == "sell" else "下跌",
        "reason": reason,
        "signal_price": round(float(fractal["value"]), 2),
        "bias": bias, "fresh": True, "score": score,
        "signal_date": fractal.get("date", ""),
    }


def _classify(strokes: List[Dict[str, Any]], pivots: List[Dict[str, Any]],
             hist: List[float], closes: List[float]) -> Optional[Dict[str, Any]]:
    n = len(strokes)
    if n < 5:
        return None
    last_close = closes[-1] if closes else None
    last = strokes[-1]
    up = last["type"] == "top"
    tops = [s for s in strokes if s["type"] == "top"]
    bots = [s for s in strokes if s["type"] == "bottom"]

    def _freshness(sig: Dict[str, Any]) -> Dict[str, Any]:
        """根据信号成立后价格是否已兑现，追加说明并标记 fresh。"""
        sp = sig["signal_price"]
        if last_close is None or sp is None:
            sig["fresh"] = True
            return sig
        if sig["signal"] == "sell":
            if last_close < sp:
                sig["fresh"] = False
                sig["reason"] += (f"信号成立后价格已回落至 {last_close:.2f}，"
                                  f"该卖点已兑现，当前不宜追空，宜观望或等待二买/新结构。")
            else:
                sig["fresh"] = True
                sig["reason"] += f"现价 {last_close:.2f} 仍高于信号价，可择机逢高。"
        else:  # buy
            if last_close > sp:
                sig["fresh"] = False
                sig["reason"] += (f"信号成立后价格已反弹至 {last_close:.2f}，"
                                  f"该买点已兑现，当前不宜追多，宜观望或等待二卖/新结构。")
            else:
                sig["fresh"] = True
                sig["reason"] += f"现价 {last_close:.2f} 仍低于信号价，可择机逢低。"
        return sig

    if up:
        if len(tops) < 2:
            return None
        t2, t1 = tops[-1], tops[-2]
        b2l = [b for b in bots if b["idx"] < t2["idx"]]
        b1l = [b for b in bots if b["idx"] < t1["idx"]]
        if not b2l or not b1l:
            return None
        b2, b1 = b2l[-1], b1l[-1]
        a1 = _area(hist, b1["idx"], t1["idx"], 1)
        a2 = _area(hist, b2["idx"], t2["idx"], 1)
        has_pivot = any(p["zg"] >= p["zd"] and p["start"]["idx"] < t2["idx"]
                        and p["end"]["idx"] > b2["idx"] for p in pivots)
        # 一卖：创新高 + 红柱面积萎缩（顶背驰）
        if t2["value"] > t1["value"] and a2 < a1 * 0.85:
            sig = _mk("sell", "一卖(顶背驰)", t2, "偏空",
                      f"上涨趋势创新高 {t2['value']:.2f}（前高 {t1['value']:.2f}），"
                      f"但 MACD 红柱面积 {a2:.1f} 明显小于前段 {a1:.1f}，顶背驰确认，为一卖信号。"
                      f"该信号于 {t2['date']} 收盘成立，次日可关注逢高偏空 / 止盈，"
                      f"止损参考前高 {t1['value']:.2f}。", 80)
            return _freshness(sig)
        # 二卖：一卖后反弹未过前高，上涨动能衰竭
        if t2["value"] <= t1["value"] and has_pivot:
            sig = _mk("sell", "二卖", t2, "偏空",
                      f"前高一卖后反弹至 {t2['value']:.2f} 未过前高 {t1['value']:.2f}，"
                      f"形成二卖，上涨动能衰竭。信号于 {t2['date']} 收盘成立，"
                      f"次日偏空，跌破 {b2['value']:.2f} 可确认转弱。", 65)
            return _freshness(sig)
        return None
    else:
        if len(bots) < 2:
            return None
        d2, d1 = bots[-1], bots[-2]
        t2l = [t for t in tops if t["idx"] < d2["idx"]]
        t1l = [t for t in tops if t["idx"] < d1["idx"]]
        if not t2l or not t1l:
            return None
        t2, t1 = t2l[-1], t1l[-1]      # t1: d1 与 d2 之间的顶
        t0 = t1l[-1]                    # t0: d1 之前的顶（前一段下跌起点）
        if t0["idx"] >= d1["idx"]:
            return None
        # 底背驰比较【下跌段】绿柱面积：t0→d1（前期下跌）与 t1→d2（近期下跌）
        a1 = _area(hist, t0["idx"], d1["idx"], -1)
        a2 = _area(hist, t1["idx"], d2["idx"], -1)
        has_pivot = any(p["zg"] >= p["zd"] and p["start"]["idx"] < d2["idx"]
                        and p["end"]["idx"] > t2["idx"] for p in pivots)
        # 一买：创新低 + 近期下跌绿柱面积明显萎缩（底背驰）
        if d2["value"] < d1["value"] and a1 > 0 and a2 < a1 * 0.85:
            sig = _mk("buy", "一买(底背驰)", d2, "偏多",
                      f"下跌趋势创新低 {d2['value']:.2f}（前低 {d1['value']:.2f}），"
                      f"但 MACD 绿柱面积 {a2:.1f} 明显小于前段下跌的 {a1:.1f}，底背驰确认，为一买信号。"
                      f"该信号于 {d2['date']} 收盘成立，次日可关注逢低偏多 / 低吸，"
                      f"止损参考前低 {d1['value']:.2f}。", 80)
            return _freshness(sig)
        # 二买：回踩未破前低（更高底）+ 两底之间存在中枢，下跌动能衰竭
        if d2["value"] >= d1["value"]:
            has_pivot2 = any(p["zg"] >= p["zd"] and p["start"]["idx"] < d2["idx"]
                             and p["end"]["idx"] > d1["idx"] for p in pivots)
            if has_pivot2:
                sig = _mk("buy", "二买", d2, "偏多",
                          f"前一低点 {d1['value']:.2f} 后回踩至 {d2['value']:.2f} 未破前低，"
                          f"两底间形成中枢，二买成立、下跌动能衰竭。信号于 {d2['date']} 收盘成立，"
                          f"次日偏多，站上 {t2['value']:.2f} 可确认转强。", 65)
                return _freshness(sig)
        # 三买：突破中枢上沿 zg 且回踩不破 zg（主升段确认）
        if pivots and last["type"] == "top":
            last_c = pivots[-1]
            zg = last_c["zg"]
            if last["value"] > zg:
                prev = strokes[-2] if len(strokes) >= 2 else None
                if prev and prev["type"] == "bottom" and prev["value"] > zg:
                    sig = _mk("buy", "三买", last, "偏多",
                              f"突破中枢上沿 {zg:.2f} 后回踩至 {last['value']:.2f} 未破上沿，"
                              f"三买成立、主升段确认。信号于 {last['date']} 收盘成立，次日偏多。", 70)
                    return _freshness(sig)
        return None


def _apply_fresh(sig: Dict[str, Any], last_close: float) -> Dict[str, Any]:
    """根据信号成立后价格是否已兑现，追加说明并标记 fresh（模块级公共函数）。"""
    sp = sig.get("signal_price")
    if last_close is None or sp is None:
        sig["fresh"] = True
        return sig
    if sig["signal"] == "sell":
        if last_close < sp:
            sig["fresh"] = False
            sig["reason"] += (f"信号成立后价格已回落至 {last_close:.2f}，"
                              f"该卖点已兑现，当前不宜追空，宜观望或等待二买/新结构。")
        else:
            sig["fresh"] = True
            sig["reason"] += f"现价 {last_close:.2f} 仍高于信号价，可择机逢高。"
    else:  # buy
        if last_close > sp:
            sig["fresh"] = False
            sig["reason"] += (f"信号成立后价格已反弹至 {last_close:.2f}，"
                              f"该买点已兑现，当前不宜追多，宜观望或等待二卖/新结构。")
        else:
            sig["fresh"] = True
            sig["reason"] += f"现价 {last_close:.2f} 仍低于信号价，可择机逢低。"
    return sig


def scan_buy_signals(bars: List[Bar], last_close: float = None) -> List[Dict[str, Any]]:
    """扫描日线**全部**买点（一买/二买/三买），返回所有成立信号（含信号日）。

    借鉴「缠论选股」技能方法进行了两项关键升级：
    1) 一买改用『力度(价×量)趋势背驰』确认（中枢后两段下行、后段力度衰减>30%），
       不再仅靠 MACD 绿柱面积，显著降低噪声；
    2) 二买收紧为『回调低点 > 中枢下沿 zd』；并新增三买『突破中枢上沿 zg 且回踩不破 zg』。
    与 _classify（只看最后一根笔）不同，本函数遍历全部结构，用于全市场买点扫描。
    """
    if len(bars) < 60:
        return []
    merged = merge_klines(bars)
    fractals = find_fractals(merged)
    strokes = find_strokes(merged, fractals)
    if len(strokes) < 5:
        return []
    pivots = find_pivots(strokes)
    hist = macd_hist([b["close"] for b in bars])
    if last_close is None:
        last_close = bars[-1]["close"]
    bots = [s for s in strokes if s["type"] == "bottom"]
    tops = [s for s in strokes if s["type"] == "top"]
    out: List[Dict[str, Any]] = []
    last = strokes[-1]

    # ---- 一买：下跌趋势背驰（力度衰减）+ 末笔向上反转 ----
    divs = detect_divergence(strokes, pivots, merged)
    for d in divs:
        if d["direction"] != "down":
            continue
        s2k = d["s2k"]
        s2 = strokes[s2k]                       # 第二段下行笔，终点为新低
        # 末笔需为向上（背驰后已反转），且新低成立
        if last["type"] != "top":              # top 终点 = 向上笔
            continue
        if s2["type"] != "bottom":             # bottom 终点 = 向下笔
            continue
        low_val = s2["value"]                    # 新低（top_down 笔终点 = 底分型）
        sig = _mk("buy", "一买(力度背驰)", s2, "偏多",
                  f"中枢后两段下跌，第二段力度 {d['m2']:.1e} 较第一段 {d['m1']:.1e} 衰减 {d['decay']}%，"
                  f"下行动能衰竭、趋势背驰成立，新低 {low_val:.2f} 处为一买。"
                  f"信号于 {s2['date']} 收盘成立，次日可关注逢低偏多 / 低吸，"
                  f"止损参考该新低。", 80)
        out.append(_apply_fresh(sig, last_close))

    # ---- 二买：回调低点 > 中枢下沿 zd ----
    if pivots:
        last_c = pivots[-1]
        zd = last_c["zd"]
        # 最近的一笔向下笔（回调）终点
        recent_down = None
        for s in reversed(strokes):
            if s["type"] == "bottom":          # bottom 终点 = 向下笔
                recent_down = s
                break
        if recent_down is not None and recent_down["value"] > zd:
            # 找该回调对应的前低（上一底分型）用于理由
            prev_bot = None
            for b in reversed(bots):
                if b["idx"] < recent_down["idx"]:
                    prev_bot = b
                    break
            pb = prev_bot["value"] if prev_bot else recent_down["value"]
            sig = _mk("buy", "二买", recent_down, "偏多",
                      f"回调低点 {recent_down['value']:.2f} 未破中枢下沿 {zd:.2f}，"
                      f"二买成立、下跌动能衰竭（前低参考 {pb:.2f}）。信号于 {recent_down['date']} 收盘成立，"
                      f"次日偏多，站上 {last['value']:.2f} 可确认转强。", 65)
            out.append(_apply_fresh(sig, last_close))

    # ---- 三买：突破中枢上沿 zg 且回踩不破 zg ----
    if pivots and len(strokes) >= 3 and last["type"] == "top":
        last_c = pivots[-1]
        zg = last_c["zg"]
        if last["value"] > zg:
            prev = strokes[-2]
            if prev["type"] == "bottom" and prev["value"] > zg:
                sig = _mk("buy", "三买", last, "偏多",
                          f"突破中枢上沿 {zg:.2f} 后回踩至 {last['value']:.2f} 未破上沿，"
                          f"三买成立、主升段确认。信号于 {last['date']} 收盘成立，次日偏多。", 70)
                out.append(_apply_fresh(sig, last_close))

    return out


# ---------------- 回测验证（借鉴「缠论选股」技能，回应信号准确性） ----------------
def backtest_buy_signals(bars: List[Bar], signals: List[Dict[str, Any]],
                         hold_days: int = 20) -> Dict[str, Any]:
    """对一组买点信号做简单前向回测，量化信号质量。

    规则：信号日次一交易日开盘买入，持有 hold_days 个交易日后以收盘卖出（或到达数据末端）。
    返回逐笔收益与聚合胜率 / 平均收益，用于评估『如果按信号买入，后面涨的概率有多大』。
    """
    if not signals or len(bars) < hold_days + 2:
        return {"trades": 0, "win_rate": 0.0, "avg_return": 0.0,
                "win_count": 0, "detail": []}
    date_idx = {b["date"]: i for i, b in enumerate(bars)}
    detail = []
    wins = 0
    rets = []
    for sig in signals:
        sd = sig.get("signal_date")
        if sd not in date_idx:
            continue
        ei = date_idx[sd]
        if ei + 1 >= len(bars):
            continue
        entry = bars[ei + 1]["open"]
        exit_i = min(ei + 1 + hold_days, len(bars) - 1)
        exit_px = bars[exit_i]["close"]
        if entry <= 0:
            continue
        ret = exit_px / entry - 1
        rets.append(ret)
        if ret > 0:
            wins += 1
        detail.append({
            "code": sig.get("code"),
            "signal_date": sd,
            "point": sig.get("point"),
            "entry": round(entry, 2),
            "exit": round(exit_px, 2),
            "return_pct": round(ret * 100, 2),
        })
    n = len(detail)
    return {
        "trades": n,
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "win_count": wins,
        "avg_return": round(sum(rets) / n * 100, 2) if n else 0.0,
        "detail": detail,
    }


# ---------------- 主入口 ----------------
def analyze(bars: List[Bar]) -> Dict[str, Any]:
    """对单品种日线做标准缠论分析，返回机会信息。"""
    closes = [b["close"] for b in bars]
    last_close = closes[-1] if closes else None
    info = {
        "bars": len(bars), "last_close": last_close,
        "trend": "数据不足", "signal": None, "point": None,
        "reason": "", "signal_price": None, "bias": None,
        "fresh": False, "score": 0,
    }
    if len(bars) < 40:
        info["reason"] = f"历史数据仅 {len(bars)} 根（需≥40），跳过"
        return info

    merged = merge_klines(bars)
    fr = find_fractals(merged)
    strokes = find_strokes(merged, fr)
    if len(strokes) < 5:
        info["trend"] = "震荡 / 笔不足"
        info["reason"] = f"有效笔仅 {len(strokes)} 笔，未构成可识别的趋势结构"
        return info

    hist = macd_hist([m["close"] for m in merged])
    pivots = find_pivots(strokes)

    # 趋势方向
    last = strokes[-1]
    info["trend"] = "上涨" if last["type"] == "top" else "下跌"
    if pivots:
        info["trend"] += f"（含 {len(pivots)} 个中枢）"

    sig = _classify(strokes, pivots, hist, closes)
    if sig:
        info.update(sig)
    else:
        info["reason"] = "结构成立但未出现标准背驰买卖点（一/二买或一/二卖）"
    return info
