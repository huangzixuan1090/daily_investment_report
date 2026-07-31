# 项目记忆：每日市场报告 Agent

## 概览
每日 8:00 自动生成「前一交易日市场总结」HTML 报告并发邮件到 irene06@qq.com、3101928126@qq.com。
内容（9 章，按 render 顺序）：① 期货市场(资金流入/涨跌幅排名) ② 缠论机会筛选(期货·本地大模型·30分钟K线·仅当天信号) ③ 股市板块表现(+ETF) ④ 全球债券市场 ⑤ 全球货币 ⑥ X 博主(@aleabitoreddit/@Ariston_Macro/@michaeljburry)当日观点 ⑦ 公众号观点监控(地平线全球策略/行知交易员·Sogou) ⑧ 美股板块涨跌 ⑨ 我的持仓(IBKR 实盘：实时持仓+昨收行情+持仓股新闻本地Ollama总结)。

**注意**：「③ 缠论日线A股机会筛选」章节已于 2026-07-29 从每日报告中删除（独立全市场A股缠论扫描产物 gen_scan_report_with_charts.py 保留）。

## 关键路径与约定
- 项目目录：`/Users/michaelhuang/WorkBuddy/老婆agent`
- 运行：`./run.sh`（抓取+渲染+发送）；`./run.sh --no-send` 测试；`./run.sh --reuse-cache` 用今日缓存；`./run.sh --from-cache reports/bundle_*.json` 用已有bundle渲染并发送。
- venv 解释器：`/Users/michaelhuang/.workbuddy/binaries/python/envs/default/bin/python`（akshare/requests/pandas/numpy/twitter-cli）。pip 必须用清华镜像源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`（直连 PyPI 极慢/卡死）。
- agently-cli：`/Users/michaelhuang/.workbuddy/binaries/node/workspace/node_modules/.bin/agently-cli`，PATH 需带 node 目录。发信两阶段：先 `message +send` 拿 confirmation_token，再带 token 发送。

## 数据源
- 期货：akshare `futures_zh_minute_sina(symbol="rb0", period="30")` 取 30分钟K线（列 datetime/open/high/low/close/volume/hold），最新到当天。主力连续= code.lower()+"0"。涨跌幅/资金流入仍用日线 `futures_zh_daily_sina`。
- 板块：东方财富 push2 clist `m:90 t:2`(行业)/`m:90 t:3`(概念)，按 f3(涨跌幅)/f62(主力净流入) 各取涨跌/流入流出 top_n。**易被高频请求 IP 限流**，需请求间隔+短超时+降级。
- 博主：twitter-cli，靠 TWITTER_AUTH_TOKEN+TWITTER_CT0 Cookie 认证。
- 公众号：Sogou 微信搜索 `https://weixin.sogou.com/weixin?type=2&query=<账号名>`，解析 `txt-box`（标题/来源/时间戳/摘要）。**受反爬限制**（验证码/限流/排名不稳），故 type=2 + 来源匹配 + **只取报告数据日期(前一天)发布的文章** + indirect 标记；直链被拦截不抓正文，仅用标题+摘要由 Ollama 总结。**注意**：两号均非每日发文(如行知交易员上次 7/11)，严格"仅前一天"多数日子该模块为空——若想保留近期观点需放宽窗口(如最近 3 个交易日)。
- 美股板块(⑧)：原用 Yahoo Finance(限流 429 频繁)。现 **IBKR 兜底**：`ibkr_collect.fetch_sector_changes` 用 `reqHistoricalData` 取 11 个 SPDR 行业 ETF 前一交易日涨跌幅(连接后先抓，不受慢持仓拖累)，`daily_report.py` 在 Yahoo 板块不全(数量少于 IBKR)或 `quotes_ok=False` 时把 `bundle["us_stocks"].sectors` 整体替换为 IBKR 数据并打 `sector_source="ibkr"`。`us_stocks.py` 默认 `sector_source="yahoo"`。
- A股日线(③·缠论)：取新浪日线(前复权 qfq)。**关键坑**：akshare `stock_zh_a_daily` 内部用 `py_mini_racer`(V8) 解密 sina JS，**非线程安全**——多线程并发必崩(`libmini_racer` Crash)。因此 `lib/chanlun_a.py` 的 `fetch_daily` 已**绕过 akshare**，直接调 sina `CN_MarketData.getKLineData`(plain JSON) + `qfq.js`(用 `eval` 解析因子字典，无 V8)，实现线程安全并发。qfq 调整 = `raw / qfq_factor`(因子从 qfq.js 的 `data` 列表 `eval` 取出，按日期 ffill)。`_prefix` 自动补全前缀(6→sh、0/3→sz)。判定复用 **lib/chan 标准缠论引擎**(确定性、不依赖大模型)，每日线做一/二买(底背驰)、一/二卖(顶背驰)识别。
- **全市场扫描** `scan_all_a_buy(cfg, max_workers=16, within_trading_days=20)`：用 sina `hs_a` 节点分页拉全量代码清单(`cache/a_codes_YYYYMMDD.json` 当日缓存)，`ThreadPoolExecutor` 并发跑全部 ~5200 只沪深A股(默认剔除北交所 8/4/9；include_bse=True 含)。**关键点**：必须用新增的 `lib/chan.scan_buy_signals(bars)` 遍历**全部**底分型对(而非 `analyze` 只看最后一根笔)，才能找回窗口内早先成立的买点。`_analyze_buys` 返回每股票 `buy_signals` 列表；扫描按股票去重(`_dedup_buys`，每只取最新买点)，排序「一买优先→fresh优先→信号最新」。本环境 eastmoney/sse/szse 列表接口全超时，sina 唯一可用。**耗时 ~11 分钟**(5201只/16并发)，必须 `run_in_background`(前台120s超时会被SIGKILL)；`socket.setdefaulttimeout(30)` 防挂起。结果缓存 `cache/chanlun_a_scan_YYYYMMDD.json`，报告 `reports/chanlun_a_buy_scan_YYYYMMDD.html`(由 `render.render_chanlun_scan_html` 生成，**只列买点、不含卖点对照**；卡片式布局手机可读；按股票去重后展示前60只，附「窗口内总信号数/去重标的数/fresh数」摘要)。**断点续扫** `resume=True` 仅复用含 `buy_signals` 字段的新版缓存(旧格式缓存会被忽略强制重扫)。监控池(27只蓝筹)仍走 `get_chanlun_a`(顺序、用 `analyze`)。
- **⚠️ lib/chan.py 引擎三大 bug（2026-07-29 修复，务必记住）**：
  1. `find_pivots` 中枢判定要求三笔「两两不同型」，但笔只有顶/底两种类型、连续三笔必首尾同型 → 条件永不成立 → **中枢列表永远为空** → 所有需中枢的买点(二买)/卖点(二卖)永不触发。修正为「相邻交替」(`s1!=s2 and s2!=s3`)。
  2. 买点背离量错了腿：`_classify`/`scan_buy_signals` 原用 `d1→t1`(上涨段)的绿柱面积比背驰，但上涨段MACD为红柱→绿柱面积≈0→`a2<a1*0.85`几乎恒假→**一买(底背驰)永不触发**。修正为量**下跌段** `t0→d1`(前一下跌)与 `t1→d2`(近一下跌)的绿柱面积(`sign=-1`)。
  3. `_classify` 只检查**最后一根笔**，早先成立、之后股价反转的买卖点被整体忽略(全市场扫描初版因此 0 买点)。全市场扫描改用 `scan_buy_signals`(遍历全部底分型对)解决。
  - 修复后实测(2026-07-29, 窗口20日, 400只样本)：买点信号 372 个(全二买、一买0)、fresh 45、去重后 248 只；全市场量级更大。一买(底背驰)在本环境该时段极少见(下跌段动能多不萎缩)，二买为主。

## 缠论实现
- 现由 **本地大模型(Ollama qwen2.5:7b)** 分析：lib/futures.py `fetch_main_minute` 取各品种 30分钟K线(最近 40 根) → lib/llm.py `analyze_chan_with_llm` 用线程池(`chan_concurrency`，见下)并发分析**全部品种**，标准缠论框架(K线包含→分型→笔→中枢→MACD背驰→一二买/卖)，输出当天买卖点 JSON。
- **关键约束(system prompt)**：只能基于【当天】30分钟K线给信号，**严禁引用当天之前的旧日期**（如"6月8日"类历史分析一律禁止）。输出字段含 signal/point/trend/bias/signal_price/reason，fresh=True。
- lib/chan.py 仍保留为标准缠论实现（笔/中枢/MACD 背驰判定），目前 LLM 路径为主，二者可对比。
- **性能坑**：qwen2.5:7b 并发 >2 易超时(urllib timeout=180)。`chan_concurrency` 已从 6 降到 **2**（更稳且单调用时更短）。55 品种全分析约 15–20 分钟。

## 待办（用户侧）
1. config.json 填 `twitter.auth_token` + `ct0`（X 登录后浏览器 Cookie）。
2. 跑 `./auth_mail.sh` 完成 agently OAuth（一次性）。
3. 确认博主 X handle（默认 serenity / aristonwang，可能需修正）。

## 定时任务
- 主任务（系统 launchd）：`~/Library/LaunchAgents/com.dailyreport.market.plist`，每天 **07:30**。脱离 WorkBuddy 运行，需 **TWS 实盘常开(7496)+API 启用 + Ollama 运行**。
- WorkBuddy automation：`automation-1784040166914`，同名已同步 07:30 但 **PAUSED**（不运行，仅备份）。

## ⚠️ 发信授权时效（重试兜底已加，但后台无法自动刷新）
- 发信用 agently-cli OAuth（access token ~15 天过期）。`lib/mail.py` 的 `send_report` 内置**重试(3次)+每轮先 `auth refresh`**，`run.sh` 发信前也先 refresh。该兜底**只救偶发网络失败**。
- **致命限制**：agently 在**后台/无 GUI 会话**（launchd、WorkBuddy Bash）下 `auth refresh` 失败（报 `Authorization required` / 连不上 `auth.agent.qq.com`），`token_status` 卡在 `auto_refresh` 无法完成。即 access token 过期后**任何后台任务都无法自动续期**，必须用户在 Mac GUI 会话 `agently-cli auth login`（或 GUI 终端 `auth refresh`）恢复。约每 15 天必发生一次。
- 失败处理：`send_report` 彻底失败时写 `reports/SEND_FAILED_<stem>.flag` 告警标记（崩溃 bug 已修，确保优雅失败而非静默崩溃/假成功），可 `./run.sh --from-cache reports/bundle_YYYYMMDD.json` 补发。
- **根治方向**：改用 **SMTP 发信**（QQ 邮箱授权码，长期有效，不依赖 OAuth 过期）→ 彻底免登录。需用户开 SMTP + 给授权码，改 `lib/mail.py` 走 smtplib。
- **失败监控**：automation `automation-1785460513286`（ACTIVE，每日 09:00）检查 `reports/SEND_FAILED_*.flag`，存在则提醒用户 `agently-cli auth login` + 补发。`run.sh` 发信后也终端提示补发命令；`daily_report.py` 发信失败日志含补发指引。
