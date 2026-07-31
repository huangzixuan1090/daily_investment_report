#!/usr/bin/env python3
"""探针：用 IBKR API (ib_insync) 取美股 11 个 SPDR 行业 ETF 的「前一交易日」涨跌幅。

方法：reqHistoricalData 取每个 ETF 最近 ~6 个交易日的日线(TRADES, RTH)，
用最后一根已完成日线的 close 与前一根 close 计算涨跌幅。
需本机 TWS 实盘(7496)常开且启用 API。纯只读，不下单。

运行： /Users/michaelhuang/code/trading_bot/.venv/bin/python test_ibkr_sectors.py
"""
from __future__ import annotations
import sys, ssl
ssl._create_default_https_context = ssl._create_unverified_context

from ib_insync import IB, Stock

SECTOR_ETFS = [
    ("XLK", "科技"), ("XLV", "医疗健康"), ("XLI", "工业"), ("XLB", "原材料"),
    ("XLY", "可选消费"), ("XLP", "必需消费"), ("XLE", "能源"), ("XLF", "金融"),
    ("XLU", "公用事业"), ("XLRE", "房地产"), ("XLC", "通信服务"),
]

PORT = 7496
CLIENT_ID = 137  # 避开 ibkr_collect 的 123


def main():
    ib = IB()
    print(f"连接 TWS 实盘 127.0.0.1:{PORT} (clientId={CLIENT_ID}) ...")
    try:
        ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID, timeout=15)
    except Exception as e:
        print(f"[FAIL] 无法连接 TWS: {e}")
        print(">>> 请确认 TWS 实盘已登录且 API 已启用(设置->API->Enable ActiveX and Socket Clients, 端口7496)")
        sys.exit(2)
    print(f"[OK] 已连接，账户 {ib.managedAccounts()}")

    # 历史日线用延时/冻结数据也能取；不需实时订阅
    ib.reqMarketDataType(3)  # 3=delayed（历史数据通常不受实时订阅限制）

    rows = []
    for sym, cn in SECTOR_ETFS:
        c = Stock(sym, "SMART", "USD", primaryExchange="ARCA")
        try:
            ib.qualifyContracts(c)
        except Exception as e:
            print(f"  [skip] {sym} qualify 失败: {e}")
            continue
        try:
            bars = ib.reqHistoricalData(
                c, endDateTime="", durationStr="8 D",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1)
        except Exception as e:
            print(f"  [skip] {sym} reqHistoricalData 失败: {e}")
            continue
        if not bars or len(bars) < 2:
            print(f"  [skip] {sym} 日线不足: n={len(bars) if bars else 0}")
            continue
        last, prev = bars[-1], bars[-2]
        chg = last.close - prev.close
        pct = (chg / prev.close * 100) if prev.close else None
        rows.append({
            "symbol": sym, "name_cn": cn,
            "date": str(last.date), "prev_date": str(prev.date),
            "close": round(last.close, 2), "prev_close": round(prev.close, 2),
            "change": round(chg, 2),
            "change_pct": round(pct, 2) if pct is not None else None,
        })
        print(f"  {sym:5} {cn:6} {last.date}  close={last.close:.2f}  "
              f"prev({prev.date})={prev.close:.2f}  chg={pct:+.2f}%")

    ib.disconnect()
    print("\n===== 排序结果（按涨跌幅降序）=====")
    rows.sort(key=lambda x: (x["change_pct"] or -999), reverse=True)
    for r in rows:
        print(f"  {r['name_cn']:6}({r['symbol']:4})  {r['change_pct']:+.2f}%   "
              f"收 {r['close']}  日期 {r['date']}")
    print(f"\n[RESULT] 成功取到 {len(rows)}/{len(SECTOR_ETFS)} 个板块的前一交易日涨跌幅")


if __name__ == "__main__":
    main()
