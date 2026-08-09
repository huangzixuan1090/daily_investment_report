# 每日市场报告 Agent

自动抓取 A 股期货/板块/ETF、全球债券货币、X 博主观点，渲染 HTML 邮件报告，每日定时发送。

## 功能模块

| 模块 | 数据源 |
|------|--------|
| 期货市场（资金流向 + 缠论信号） | akshare |
| A 股板块 / 概念板块 | 东方财富，新浪兜底 |
| ETF 涨跌 + 资金流向 | 东方财富 |
| 全球债券 / 货币 | akshare |
| X（Twitter）博主观点 | 公开推文（需自己的 Cookie） |
| IBKR 实盘持仓快照 | ib_insync 只读连接 TWS |
| LLM 总结 | 本地 Ollama + Claude API（可选） |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`，填入以下内容：

| 字段 | 说明 |
|------|------|
| `recipients` | 收件人邮箱列表 |
| `llm.api.api_key` | Claude API Key（`sk-ant-...`），不用可设 `enabled: false` |
| `twitter.auth_token` / `ct0` | 登录 X 后从浏览器 Cookie 复制 |
| `mail.smtp_user` / `smtp_password` | Gmail 地址 + App Password |
| `ibkr.venv_python` | ib_insync 所在 venv 的 python 路径，不用可设 `enabled: false` |

### 3. 运行

```bash
# 完整抓取 + 渲染 + 发邮件
python daily_report.py

# 只生成不发送（测试）
python daily_report.py --no-send

# 复用今日缓存（省流量）
python daily_report.py --reuse-cache
```

### 4. 定时运行（macOS launchd）

参考 `CLOUD_DEPLOYMENT.md`。

## 目录结构

```
daily_report.py       # 主入口
lib/
  futures.py          # 期货数据 + 缠论信号
  sectors.py          # A 股板块/概念
  etf.py              # ETF 数据
  global_markets.py   # 全球债券/货币
  bloggers.py         # X 博主抓取
  llm.py              # LLM 总结调度
  render.py           # HTML 渲染
  mail.py             # 邮件发送
  chan/               # 标准缠论引擎
config.example.json   # 配置模板（不含密钥）
requirements.txt      # Python 依赖
```

> `config.json`、`cache/`、`logs/`、`reports/` 均在 `.gitignore` 中，不会提交到仓库。
