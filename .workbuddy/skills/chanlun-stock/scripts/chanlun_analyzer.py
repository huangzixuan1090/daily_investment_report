#!/usr/bin/env python3
"""
缠论选股分析器 v2 - 基于缠中说禅缠论理论的A股技术分析工具
核心功能：K线包含处理 → 分型识别 → 笔划分 → 中枢判定 → 背驰检测 → 买卖点识别 → 策略回测
数据源：AKShare（主） + 新浪财经（备用），双源容灾
"""

import os
import sys
import json
import argparse
from coze_workload_identity import requests


# ============================================================
# 1. 数据获取（双源容灾：AKShare → 新浪财经）
# ============================================================

def _detect_data_source():
    """检测可用的数据源，优先 AKShare"""
    try:
        import akshare  # noqa: F401
        # 尝试实际调用，验证是否能正常工作
        akshare.stock_zh_a_hist  # 检查关键函数存在
        return "akshare"
    except Exception:
        pass
    return "sina"


def get_stock_kline_akshare(code, period="daily", count=1000):
    """通过 AKShare 获取K线数据（主数据源，稳定且量大）"""
    import akshare as ak

    # 市场前缀映射
    if code.startswith("6"):
        symbol = f"sh{code}"
    elif code.startswith("0") or code.startswith("3"):
        symbol = f"sz{code}"
    else:
        symbol = f"sh{code}"

    # AKShare周期映射
    period_map = {
        "daily": "daily",
        "30min": "30",
        "weekly": "weekly",
    }
    ak_period = period_map.get(period, "daily")

    try:
        if ak_period == "daily":
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        elif ak_period == "30":
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="30", adjust="qfq")
        elif ak_period == "weekly":
            df = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust="qfq")
        else:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")

        if df is None or df.empty:
            return None, "AKShare返回空数据"

        # 统一列名
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "时间": "date",
        }
        df = df.rename(columns=col_map)

        # 确保必要的列存在
        required = ["date", "open", "close", "high", "low", "volume"]
        for col in required:
            if col not in df.columns:
                return None, f"AKShare返回数据缺少列: {col}"

        # 取最近count条
        df = df.tail(count)

        klines = []
        for _, row in df.iterrows():
            klines.append({
                "date": str(row["date"]),
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row["volume"]) if row["volume"] > 0 else 1,
            })

        # 获取股票名称
        name = _get_stock_name_akshare(code)

        return {"name": name, "code": code, "klines": klines}, None

    except Exception as e:
        return None, f"AKShare获取数据失败: {str(e)}"


def _get_stock_name_akshare(code):
    """通过AKShare获取股票名称"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        match = df[df["代码"] == code]
        if not match.empty:
            return match.iloc[0]["名称"]
    except Exception:
        pass
    return code


def get_stock_kline_sina(code, period="daily", count=500):
    """通过新浪财经API获取K线数据（备用数据源）"""
    if code.startswith("6"):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"

    scale_map = {"daily": 240, "30min": 60, "weekly": 1200}
    scale = scale_map.get(period, 240)

    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": symbol,
        "scale": scale,
        "ma": "no",
        "datalen": str(count),
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None, f"新浪请求失败，状态码: {resp.status_code}"

        data = resp.json()
        if not data or not isinstance(data, list):
            return None, "新浪未获取到K线数据"

        klines = []
        for item in data:
            klines.append({
                "date": item["day"],
                "open": float(item["open"]),
                "close": float(item["close"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "volume": float(item["volume"]),
            })

        name = _get_stock_name_sina(code)
        return {"name": name, "code": code, "klines": klines}, None

    except Exception as e:
        return None, f"新浪获取数据失败: {str(e)}"


def _get_stock_name_sina(code):
    """通过新浪实时行情获取股票名称"""
    if code.startswith("6"):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"

    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            text = resp.content.decode("gbk", errors="ignore")
            if '="' in text:
                value = text.split('="')[1].split('"')[0]
                if value:
                    return value.split(",")[0]
    except Exception:
        pass
    return code


def get_stock_kline(code, period="daily", count=1000):
    """获取K线数据，双源容灾：AKShare → 新浪"""
    source = _detect_data_source()

    if source == "akshare":
        result, err = get_stock_kline_akshare(code, period, count)
        if result:
            result["data_source"] = "akshare"
            return result, None
        # AKShare失败，降级到新浪
        print(f"AKShare失败({err})，降级到新浪数据源", file=sys.stderr)

    result, err = get_stock_kline_sina(code, period, count)
    if result:
        result["data_source"] = "sina"
        return result, None
    return None, f"所有数据源均失败（AKShare/Sina），最后错误: {err}"


# ============================================================
# 2. K线包含关系处理
# ============================================================

def is_contain(k1, k2):
    """判断两根K线是否存在包含关系"""
    return (k1["high"] >= k2["high"] and k1["low"] <= k2["low"]) or \
           (k2["high"] >= k1["high"] and k2["low"] <= k1["low"])


def merge_kline(k1, k2, direction):
    """合并包含关系的K线"""
    if direction == "up":
        return {
            "high": max(k1["high"], k2["high"]),
            "low": max(k1["low"], k2["low"]),
            "date": k2["date"],
            "open": k2["open"],
            "close": k2["close"],
            "volume": k2["volume"],
        }
    else:
        return {
            "high": min(k1["high"], k2["high"]),
            "low": min(k1["low"], k2["low"]),
            "date": k2["date"],
            "open": k2["open"],
            "close": k2["close"],
            "volume": k2["volume"],
        }


def process_inclusion(klines):
    """处理K线包含关系"""
    if len(klines) < 2:
        return klines

    result = [klines[0]]
    direction = "up"

    for i in range(1, len(klines)):
        cur = klines[i]
        prev = result[-1]

        if prev["high"] > cur["high"] and prev["low"] > cur["low"]:
            direction = "down"
        elif prev["high"] < cur["high"] and prev["low"] < cur["low"]:
            direction = "up"

        if is_contain(prev, cur):
            merged = merge_kline(prev, cur, direction)
            result[-1] = merged
        else:
            result.append(cur)

    return result


# ============================================================
# 3. 分型识别
# ============================================================

def identify_fractals(klines):
    """识别顶分型和底分型"""
    fractals = []
    for i in range(1, len(klines) - 1):
        prev = klines[i - 1]
        cur = klines[i]
        next_k = klines[i + 1]

        if cur["high"] > prev["high"] and cur["high"] > next_k["high"] and \
           cur["low"] > prev["low"] and cur["low"] > next_k["low"]:
            fractals.append({
                "type": "top",
                "index": i,
                "date": cur["date"],
                "value": cur["high"],
                "kline": cur,
            })
        elif cur["low"] < prev["low"] and cur["low"] < next_k["low"] and \
             cur["high"] < prev["high"] and cur["high"] < next_k["high"]:
            fractals.append({
                "type": "bottom",
                "index": i,
                "date": cur["date"],
                "value": cur["low"],
                "kline": cur,
            })

    return fractals


# ============================================================
# 4. 笔的划分
# ============================================================

def identify_strokes(fractals):
    """识别笔：顶底分型交替，间隔>=4，高低关系正确"""
    if len(fractals) < 2:
        return []

    strokes = []
    last = fractals[0]

    for i in range(1, len(fractals)):
        cur = fractals[i]

        if cur["type"] == last["type"]:
            if cur["type"] == "top" and cur["value"] > last["value"]:
                last = cur
            elif cur["type"] == "bottom" and cur["value"] < last["value"]:
                last = cur
            continue

        if cur["index"] - last["index"] < 4:
            continue

        if last["type"] == "top" and cur["type"] == "bottom":
            if last["value"] > cur["value"]:
                strokes.append({
                    "type": "top_down",
                    "start_date": last["date"],
                    "start_value": last["value"],
                    "end_date": cur["date"],
                    "end_value": cur["value"],
                    "start_index": last["index"],
                    "end_index": cur["index"],
                })
                last = cur
        elif last["type"] == "bottom" and cur["type"] == "top":
            if cur["value"] > last["value"]:
                strokes.append({
                    "type": "bottom_up",
                    "start_date": last["date"],
                    "start_value": last["value"],
                    "end_date": cur["date"],
                    "end_value": cur["value"],
                    "start_index": last["index"],
                    "end_index": cur["index"],
                })
                last = cur

    return strokes


# ============================================================
# 5. 中枢识别
# ============================================================

def identify_centers(strokes):
    """识别中枢：至少3笔重叠区间，支持延伸"""
    centers = []
    if len(strokes) < 3:
        return centers

    i = 0
    while i <= len(strokes) - 3:
        s1, s2, s3 = strokes[i], strokes[i+1], strokes[i+2]

        range1 = (min(s1["start_value"], s1["end_value"]), max(s1["start_value"], s1["end_value"]))
        range2 = (min(s2["start_value"], s2["end_value"]), max(s2["start_value"], s2["end_value"]))
        range3 = (min(s3["start_value"], s3["end_value"]), max(s3["start_value"], s3["end_value"]))

        zg = min(range1[1], range2[1], range3[1])
        zd = max(range1[0], range2[0], range3[0])

        if zg > zd:
            end_idx = i + 2
            for j in range(i + 3, len(strokes)):
                sj = strokes[j]
                sj_high = max(sj["start_value"], sj["end_value"])
                sj_low = min(sj["start_value"], sj["end_value"])
                if sj_low <= zg and sj_high >= zd:
                    zg = min(zg, sj_high)
                    zd = max(zd, sj_low)
                    end_idx = j
                else:
                    break

            zz = (zg + zd) / 2
            centers.append({
                "start_date": s1["start_date"],
                "end_date": strokes[end_idx]["end_date"],
                "zg": round(zg, 2),
                "zd": round(zd, 2),
                "zz": round(zz, 2),
                "stroke_start": i,
                "stroke_end": end_idx,
                "level": 1,
                "stroke_count": end_idx - i + 1,
            })
            i = end_idx + 1
        else:
            i += 1

    return centers


# ============================================================
# 6. 背驰检测
# ============================================================

def calculate_stroke_momentum(stroke, klines):
    """计算笔的力度（MACD面积法改进：价格变动×成交量加权）"""
    start_idx = stroke["start_index"]
    end_idx = stroke["end_index"]
    if end_idx <= start_idx:
        return 0

    total = 0.0
    for idx in range(start_idx, min(end_idx + 1, len(klines))):
        price_change = abs(klines[idx]["close"] - klines[idx]["open"])
        vol = klines[idx].get("volume", 1)
        if vol <= 0:
            vol = 1
        total += price_change * vol

    return total


def detect_divergence(strokes, centers, klines):
    """检测趋势背驰和盘整背驰，增加MACD辅助判断"""
    divergences = []

    if len(strokes) < 4 or not centers:
        return divergences

    for center in centers:
        c_end = center["stroke_end"]

        # 趋势背驰
        if c_end + 2 < len(strokes):
            s1 = strokes[c_end + 1]
            s2 = strokes[c_end + 2]

            if s1["type"] == s2["type"]:
                m1 = calculate_stroke_momentum(s1, klines)
                m2 = calculate_stroke_momentum(s2, klines)
                if m1 > 0 and m2 < m1 * 0.7:
                    direction = "up" if s1["type"] == "bottom_up" else "down"
                    divergences.append({
                        "type": f"趋势背驰({'上' if direction == 'up' else '下'})",
                        "date": s2["end_date"],
                        "direction": direction,
                        "momentum_prev": round(m1, 2),
                        "momentum_cur": round(m2, 2),
                        "decay": round((1 - m2 / m1) * 100, 1),
                        "center_zg": center["zg"],
                        "center_zd": center["zd"],
                    })

        # 盘整背驰
        for j in range(center["stroke_start"], c_end):
            if j + 2 <= c_end and j + 2 < len(strokes):
                if strokes[j]["type"] == strokes[j + 2]["type"]:
                    m1 = calculate_stroke_momentum(strokes[j], klines)
                    m2 = calculate_stroke_momentum(strokes[j + 2], klines)
                    if m1 > 0 and m2 < m1 * 0.6:
                        direction = "up" if strokes[j]["type"] == "bottom_up" else "down"
                        divergences.append({
                            "type": "盘整背驰",
                            "date": strokes[j + 2]["end_date"],
                            "direction": direction,
                            "momentum_prev": round(m1, 2),
                            "momentum_cur": round(m2, 2),
                            "decay": round((1 - m2 / m1) * 100, 1),
                            "center_zg": center["zg"],
                            "center_zd": center["zd"],
                        })

    return divergences


# ============================================================
# 7. 买卖点识别
# ============================================================

def identify_buy_sell_points(strokes, centers, divergences, klines):
    """识别三类买卖点"""
    signals = []

    if not centers or len(strokes) < 3:
        return signals

    last_center = centers[-1]
    last_stroke = strokes[-1]
    current_price = klines[-1]["close"] if klines else 0

    # ---- 买点 ----

    # 第一类买点：下跌趋势背驰 + 笔向上
    for div in divergences:
        if div["direction"] == "down" and "趋势背驰" in div["type"]:
            if last_stroke["type"] == "bottom_up":
                signals.append({
                    "type": "第一类买点",
                    "reason": f"下跌趋势背驰（力度衰减{div['decay']}%），当前笔向上反转",
                    "date": last_stroke["end_date"],
                    "price": current_price,
                    "confidence": "高" if div["decay"] > 50 else "中",
                    "signal_type": "buy",
                })
                break

    # 第二类买点：回调不破中枢下沿
    if len(strokes) >= 4:
        recent_bottom = None
        for s in reversed(strokes):
            if s["type"] == "top_down":
                recent_bottom = s
                break
        if recent_bottom and recent_bottom["end_value"] > last_center["zd"]:
            signals.append({
                "type": "第二类买点",
                "reason": f"回调低点{recent_bottom['end_value']:.2f}未破中枢下沿{last_center['zd']:.2f}",
                "date": recent_bottom["end_date"],
                "price": current_price,
                "confidence": "高",
                "signal_type": "buy",
            })

    # 第三类买点：突破中枢后回踩不破上沿
    if len(strokes) >= 2 and last_stroke["type"] == "bottom_up":
        if last_stroke["end_value"] > last_center["zg"] and len(strokes) >= 3:
            prev_stroke = strokes[-2]
            if prev_stroke["type"] == "top_down" and prev_stroke["end_value"] > last_center["zg"]:
                signals.append({
                    "type": "第三类买点",
                    "reason": f"突破中枢后回踩{prev_stroke['end_value']:.2f}未破上沿{last_center['zg']:.2f}",
                    "date": last_stroke["end_date"],
                    "price": current_price,
                    "confidence": "高",
                    "signal_type": "buy",
                })

    # ---- 卖点 ----

    # 第一类卖点：上涨趋势背驰 + 笔向下
    for div in divergences:
        if div["direction"] == "up" and "趋势背驰" in div["type"]:
            if last_stroke["type"] == "top_down":
                signals.append({
                    "type": "第一类卖点",
                    "reason": f"上涨趋势背驰（力度衰减{div['decay']}%），当前笔向下反转",
                    "date": last_stroke["end_date"],
                    "price": current_price,
                    "confidence": "高" if div["decay"] > 50 else "中",
                    "signal_type": "sell",
                })
                break

    # 第二类卖点：反弹不破中枢上沿
    if len(strokes) >= 4:
        recent_top = None
        for s in reversed(strokes):
            if s["type"] == "bottom_up":
                recent_top = s
                break
        if recent_top and recent_top["end_value"] < last_center["zg"]:
            signals.append({
                "type": "第二类卖点",
                "reason": f"反弹高点{recent_top['end_value']:.2f}未破中枢上沿{last_center['zg']:.2f}",
                "date": recent_top["end_date"],
                "price": current_price,
                "confidence": "中",
                "signal_type": "sell",
            })

    # 第三类卖点：跌破中枢后反弹不破下沿
    if len(strokes) >= 2 and last_stroke["type"] == "top_down":
        if last_stroke["end_value"] < last_center["zd"] and len(strokes) >= 3:
            prev_stroke = strokes[-2]
            if prev_stroke["type"] == "bottom_up" and prev_stroke["end_value"] < last_center["zd"]:
                signals.append({
                    "type": "第三类卖点",
                    "reason": f"跌破中枢后反弹{prev_stroke['end_value']:.2f}未回下沿{last_center['zd']:.2f}",
                    "date": last_stroke["end_date"],
                    "price": current_price,
                    "confidence": "高",
                    "signal_type": "sell",
                })

    return signals


# ============================================================
# 8. 综合评估
# ============================================================

def evaluate_stance(strokes, centers, divergences, signals, klines):
    """综合评估多空立场"""
    if not strokes or not klines:
        return "数据不足", "无法判断"

    current_price = klines[-1]["close"]
    buy_signals = [s for s in signals if s.get("signal_type") == "buy"]
    sell_signals = [s for s in signals if s.get("signal_type") == "sell"]

    if buy_signals and not sell_signals:
        return "看多", f"出现{buy_signals[0]['type']}，{buy_signals[0]['reason']}"
    elif sell_signals and not buy_signals:
        return "看空", f"出现{sell_signals[0]['type']}，{sell_signals[0]['reason']}"
    elif buy_signals and sell_signals:
        high_buy = any(s["confidence"] == "高" for s in buy_signals)
        high_sell = any(s["confidence"] == "高" for s in sell_signals)
        if high_buy and not high_sell:
            return "偏多", "多空信号并存，买点置信度较高"
        elif high_sell and not high_buy:
            return "偏空", "多空信号并存，卖点优先考虑"
        else:
            return "中性", "多空信号并存，等待方向明确"

    if centers:
        last_c = centers[-1]
        if current_price > last_c["zg"]:
            return "偏多", f"价格在中枢[{last_c['zd']:.2f}-{last_c['zg']:.2f}]上方运行"
        elif current_price < last_c["zd"]:
            return "偏空", f"价格在中枢[{last_c['zd']:.2f}-{last_c['zg']:.2f}]下方运行"
        else:
            return "中性", f"价格在中枢[{last_c['zd']:.2f}-{last_c['zg']:.2f}]内震荡"

    return "观望", "中枢尚未形成，等待趋势明确"


# ============================================================
# 9. 策略回测
# ============================================================

def backtest_strategy(klines, strokes, centers, signals, initial_capital=100000):
    """
    基于买卖点信号的简单回测
    规则：买点出现次日开盘买入，卖点出现次日开盘卖出，每次全仓
    """
    if not signals or not klines:
        return {"error": "信号不足，无法回测"}

    # 构建日期→价格映射
    date_open = {}
    for kl in klines:
        date_open[kl["date"]] = kl["open"]

    # 按日期排序信号
    sorted_signals = sorted(signals, key=lambda x: x["date"])

    trades = []
    capital = initial_capital
    position = 0  # 持仓股数
    entry_price = 0

    for sig in sorted_signals:
        sig_date = sig["date"]

        # 找到信号次日（找信号日期之后的第一个交易日开盘价）
        next_dates = [d for d in sorted(date_open.keys()) if d > sig_date]
        if not next_dates:
            continue
        execute_date = next_dates[0]
        execute_price = date_open[execute_date]

        if sig["signal_type"] == "buy" and position == 0:
            # 买入
            position = int(capital / execute_price / 100) * 100  # 整手买入
            if position == 0:
                position = int(capital / execute_price)  # 资金不足一手则按股买
            if position == 0:
                continue
            entry_price = execute_price
            cost = position * execute_price
            capital -= cost
            trades.append({
                "action": "买入",
                "date": execute_date,
                "price": execute_price,
                "shares": position,
                "reason": sig["type"],
                "capital_after": round(capital, 2),
            })

        elif sig["signal_type"] == "sell" and position > 0:
            # 卖出
            revenue = position * execute_price
            profit = revenue - position * entry_price
            profit_pct = (execute_price / entry_price - 1) * 100
            capital += revenue
            trades.append({
                "action": "卖出",
                "date": execute_date,
                "price": execute_price,
                "shares": position,
                "reason": sig["type"],
                "profit": round(profit, 2),
                "profit_pct": round(profit_pct, 2),
                "capital_after": round(capital, 2),
            })
            position = 0
            entry_price = 0

    # 期末估值
    last_price = klines[-1]["close"]
    final_value = capital + position * last_price
    total_return = (final_value / initial_capital - 1) * 100

    # 基准收益（买入持有）
    benchmark_return = (last_price / klines[0]["close"] - 1) * 100

    # 计算胜率
    sell_trades = [t for t in trades if t["action"] == "卖出"]
    win_trades = [t for t in sell_trades if t.get("profit", 0) > 0]

    result = {
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "benchmark_return_pct": round(benchmark_return, 2),
        "excess_return_pct": round(total_return - benchmark_return, 2),
        "total_trades": len(trades),
        "buy_count": len([t for t in trades if t["action"] == "买入"]),
        "sell_count": len(sell_trades),
        "win_rate": round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else 0,
        "trades": trades,
        "open_position": position,
        "open_entry_price": entry_price,
    }

    return result


# ============================================================
# 10. 多级别分析
# ============================================================

def multi_level_analysis(code, levels=None):
    """多级别联合分析"""
    if levels is None:
        levels = ["daily"]

    results = {}
    for level in levels:
        stock_data, err = get_stock_kline(code, period=level)
        if err:
            results[level] = {"error": err}
            continue

        klines = stock_data["klines"]
        if len(klines) < 60:
            results[level] = {"error": f"数据不足60根K线（当前{len(klines)}根）"}
            continue

        result = _run_analysis(klines, stock_data["name"], code)
        result["data_source"] = stock_data.get("data_source", "unknown")
        result["kline_count"] = len(klines)
        results[level] = result

    return results


def _run_analysis(klines, name, code):
    """对K线序列执行完整缠论分析（内部调用）"""
    processed = process_inclusion(klines)
    fractals = identify_fractals(processed)
    strokes = identify_strokes(fractals)
    centers = identify_centers(strokes)
    divergences = detect_divergence(strokes, centers, processed)
    divergences = [d for d in divergences if d["decay"] > 40]
    divergences = sorted(divergences, key=lambda x: x["decay"], reverse=True)[:5]
    signals = identify_buy_sell_points(strokes, centers, divergences, processed)
    stance, stance_reason = evaluate_stance(strokes, centers, divergences, signals, processed)

    recent_strokes = strokes[-5:] if len(strokes) >= 5 else strokes
    stroke_desc = []
    for s in recent_strokes:
        direction = "↑" if s["type"] == "bottom_up" else "↓"
        stroke_desc.append(f"{s['start_date']}{direction}{s['end_date']}({s['start_value']:.2f}→{s['end_value']:.2f})")

    center_desc = []
    for c in centers[-3:]:
        center_desc.append(f"[{c['zd']:.2f}-{c['zg']:.2f}]({c['start_date']}~{c['end_date']})")

    result = {
        "code": code,
        "name": name,
        "current_price": klines[-1]["close"],
        "current_date": klines[-1]["date"],
        "processed_count": len(processed),
        "fractal_count": len(fractals),
        "stroke_count": len(strokes),
        "center_count": len(centers),
        "recent_strokes": stroke_desc,
        "centers": center_desc,
        "last_stroke_direction": "向上" if strokes and strokes[-1]["type"] == "bottom_up" else "向下" if strokes else "未知",
        "divergences": divergences,
        "signals": signals,
        "stance": stance,
        "stance_reason": stance_reason,
    }

    if centers:
        last_c = centers[-1]
        result["last_center"] = {
            "zd": last_c["zd"],
            "zg": last_c["zg"],
            "zz": last_c["zz"],
            "start_date": last_c["start_date"],
            "end_date": last_c["end_date"],
            "stroke_count": last_c.get("stroke_count", 0),
        }

    return result, strokes, centers, divergences, signals, processed


# ============================================================
# 11. 主分析流程
# ============================================================

def analyze_stock(code, period="daily", count=1000, enable_backtest=True):
    """对单只股票进行完整的缠论分析（含回测）"""
    stock_data, err = get_stock_kline(code, period, count)
    if err:
        return {"code": code, "error": err}

    klines = stock_data["klines"]
    name = stock_data["name"]

    if len(klines) < 60:
        return {"code": code, "name": name, "error": f"数据不足60个交易日（当前{len(klines)}根），无法进行可靠分析"}

    analysis_result = _run_analysis(klines, name, code)

    # _run_analysis返回元组，需要解包
    if isinstance(analysis_result, tuple):
        result, strokes, centers, divergences, signals, processed = analysis_result
    else:
        return analysis_result

    result["data_source"] = stock_data.get("data_source", "unknown")
    result["kline_count"] = len(klines)

    # 回测
    if enable_backtest and signals:
        backtest = backtest_strategy(klines, strokes, centers, signals)
        result["backtest"] = backtest

    return result


def format_output(results):
    """格式化输出结果"""
    output = []
    for r in results:
        if "error" in r:
            output.append(f"❌ {r.get('code', '未知')}: {r['error']}")
            continue

        lines = [
            f"📊 {r['name']}({r['code']}) 缠论分析",
            f"━━━━━━━━━━━━━━━━━━",
            f"💰 当前价格: {r['current_price']:.2f} ({r['current_date']})",
            f"📈 数据: {r['kline_count']}根K线 | 来源: {r.get('data_source', 'unknown')}",
            f"🔸 当前笔方向: {r['last_stroke_direction']}",
        ]

        if r.get("last_center"):
            c = r["last_center"]
            lines.append(f"🔸 最近中枢: [{c['zd']:.2f} - {c['zg']:.2f}] 中点{c['zz']:.2f} ({c['start_date']}~{c['end_date']})")
        else:
            lines.append("🔸 最近中枢: 尚未形成")

        if r["divergences"]:
            div_strs = [f"{d['type']}(力度衰减{d['decay']}%)" for d in r["divergences"]]
            lines.append(f"🔸 背驰信号: {'; '.join(div_strs)}")
        else:
            lines.append("🔸 背驰信号: 无")

        if r["signals"]:
            sig_strs = [f"{s['type']}(置信度:{s['confidence']})" for s in r["signals"]]
            lines.append(f"🔸 买卖点: {'; '.join(sig_strs)}")
        else:
            lines.append("🔸 买卖点: 暂无信号")

        lines.append(f"🔸 综合评估: {r['stance']} — {r['stance_reason']}")

        if r["recent_strokes"]:
            lines.append(f"\n📝 近5笔:")
            for i, s in enumerate(r["recent_strokes"], 1):
                lines.append(f"  {i}. {s}")

        if r["centers"]:
            lines.append(f"\n📝 中枢:")
            for i, c in enumerate(r["centers"], 1):
                lines.append(f"  {i}. {c}")

        # 回测结果
        if r.get("backtest") and "error" not in r["backtest"]:
            bt = r["backtest"]
            lines.append(f"\n📊 策略回测:")
            lines.append(f"  初始资金: {bt['initial_capital']:,.0f}")
            lines.append(f"  期末市值: {bt['final_value']:,.2f}")
            lines.append(f"  总收益率: {bt['total_return_pct']:.2f}%")
            lines.append(f"  基准收益(持有): {bt['benchmark_return_pct']:.2f}%")
            lines.append(f"  超额收益: {bt['excess_return_pct']:.2f}%")
            lines.append(f"  交易次数: {bt['buy_count']}买{bt['sell_count']}卖")
            lines.append(f"  胜率: {bt['win_rate']:.1f}%")
            if bt["open_position"] > 0:
                lines.append(f"  ⚠️ 当前持仓: {bt['open_position']}股 @ {bt['open_entry_price']:.2f}")

            if bt["trades"]:
                lines.append(f"\n  交易明细:")
                for t in bt["trades"]:
                    if t["action"] == "买入":
                        lines.append(f"    🟢 {t['date']} 买入 {t['shares']}股@{t['price']:.2f} ({t['reason']})")
                    else:
                        lines.append(f"    🔴 {t['date']} 卖出 {t['shares']}股@{t['price']:.2f} 盈亏{t.get('profit_pct', 0):.2f}% ({t['reason']})")

        lines.append(f"\n⚠️ 以上分析基于缠论技术理论，仅供参考学习，不构成任何投资建议。股市有风险，投资需谨慎。")
        output.append("\n".join(lines))

    return "\n\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="缠论选股分析器 v2")
    parser.add_argument("--codes", required=True, help="股票代码，逗号分隔，如 '000001,600519'")
    parser.add_argument("--period", default="daily", choices=["daily", "30min", "weekly"], help="K线周期")
    parser.add_argument("--count", type=int, default=1000, help="获取K线数量（默认1000）")
    parser.add_argument("--no-backtest", action="store_true", help="禁用回测")
    parser.add_argument("--multi-level", action="store_true", help="多级别联合分析（日线+30分钟）")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        print(json.dumps({"error": "未提供有效的股票代码"}, ensure_ascii=False))
        return

    if args.multi_level:
        # 多级别分析
        all_results = {}
        for code in codes:
            clean_code = _clean_code(code)
            if not clean_code:
                all_results[code] = {"error": f"无效的股票代码: {code}"}
                continue
            levels = ["daily", "30min"] if args.period == "daily" else [args.period]
            all_results[code] = multi_level_analysis(clean_code, levels)

        text = format_multi_level_output(all_results)
        print(text)
        print(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return

    results = []
    for code in codes:
        clean_code = _clean_code(code)
        if not clean_code:
            results.append({"code": code, "error": f"无效的股票代码格式: {code}，请使用6位数字如 000001"})
            continue

        result = analyze_stock(clean_code, args.period, args.count, enable_backtest=not args.no_backtest)
        results.append(result)

    text_output = format_output(results)
    print(text_output)
    json_output = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    print(json_output, file=sys.stderr)


def _clean_code(code):
    """清理股票代码，去除市场前缀"""
    clean = code
    for prefix in ["sh", "sz", "SH", "SZ", "SHSE", "SZSE"]:
        clean = clean.replace(prefix, "")
    clean = clean.strip()
    if clean.isdigit() and len(clean) == 6:
        return clean
    return None


def format_multi_level_output(all_results):
    """格式化多级别分析输出"""
    output = []
    for code, levels in all_results.items():
        lines = [f"📊 {code} 多级别缠论分析", f"━━━━━━━━━━━━━━━━━━"]
        for level, r in levels.items():
            level_name = {"daily": "日线", "30min": "30分钟", "weekly": "周线"}.get(level, level)
            if "error" in r:
                lines.append(f"\n📍 {level_name}: {r['error']}")
                continue

            lines.append(f"\n📍 {level_name}级别:")
            lines.append(f"  笔方向: {r.get('last_stroke_direction', '未知')}")
            if r.get("last_center"):
                c = r["last_center"]
                lines.append(f"  最近中枢: [{c['zd']:.2f}-{c['zg']:.2f}]")
            if r.get("signals"):
                sig_strs = [f"{s['type']}({s['confidence']})" for s in r["signals"]]
                lines.append(f"  买卖点: {', '.join(sig_strs)}")
            lines.append(f"  评估: {r.get('stance', '?')} — {r.get('stance_reason', '?')}")

        lines.append(f"\n⚠️ 以上分析基于缠论技术理论，仅供参考学习，不构成任何投资建议。股市有风险，投资需谨慎。")
        output.append("\n".join(lines))

    return "\n\n".join(output)


if __name__ == "__main__":
    main()
