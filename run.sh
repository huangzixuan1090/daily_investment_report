#!/bin/bash
# 运行每日报告。用法: ./run.sh [--no-send]
VENV=/Users/michaelhuang/.workbuddy/binaries/python/envs/default
cd "/Users/michaelhuang/WorkBuddy/老婆agent" || exit 1
# 发信前尽量续期授权（救 access token 过期；失败不阻塞，由 mail.send_report 内部重试+refresh 兜底）
AGENTLY=/Users/michaelhuang/.workbuddy/binaries/node/workspace/node_modules/.bin/agently-cli
if [ -x "$AGENTLY" ]; then
  "$AGENTLY" auth refresh >/dev/null 2>&1 || true
fi
"$VENV/bin/python" daily_report.py "$@"
# 发信后检查是否留下失败标记（mail.send_report 彻底失败时会写 SEND_FAILED_*.flag）
FLAG=$(ls reports/SEND_FAILED_*.flag 2>/dev/null | head -1)
if [ -n "$FLAG" ]; then
  echo "============================================================"
  echo "[发信失败] 发现标记: $FLAG"
  echo "处理: 先 agently-cli auth login 续期，再"
  echo "      ./run.sh --from-cache reports/bundle_$(date +%Y%m%d).json 补发"
  echo "============================================================"
fi
