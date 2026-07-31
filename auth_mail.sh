#!/bin/bash
# 授权 Agent Mail（agently-cli）。执行后按提示在浏览器完成 OAuth，仅需一次。
BIN=/Users/michaelhuang/.workbuddy/binaries/node/workspace/node_modules/.bin/agently-cli
export PATH=/Users/michaelhuang/.workbuddy/binaries/node/versions/22.22.2/bin:$PATH
"$BIN" auth login
echo "--- 验证 ---"
"$BIN" +me
