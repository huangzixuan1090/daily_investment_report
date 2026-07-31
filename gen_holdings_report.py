#!/usr/bin/env python3
"""便捷入口：生成「实盘持仓 + 昨日新闻」独立预览 HTML（不发送）。

实际采集逻辑在 ibkr_collect.py（需 trading_bot/.venv 的 ib_insync）。
用法：
  python gen_holdings_report.py            # 默认昨收日
  python gen_holdings_report.py 2026-07-15 # 指定数据日期
"""
from __future__ import annotations
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = "/Users/michaelhuang/code/trading_bot/.venv/bin/python"


def main():
    if len(sys.argv) > 1:
        data_date = sys.argv[1]
    else:
        data_date = (datetime.now(timezone(timedelta(hours=8)) - timedelta(days=1)).strftime("%Y-%m-%d"))
    out = ROOT / "reports" / f"holdings_report_{data_date}.html"
    cmd = [VENV, str(ROOT / "ibkr_collect.py"),
           "--standalone-html", str(out), "--date", data_date]
    print("运行:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"\n已生成: {out}")


if __name__ == "__main__":
    main()
