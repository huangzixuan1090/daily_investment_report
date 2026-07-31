# AWS + GitHub Actions 云端部署指南

## 概述

保留本地开发模式的同时，使用 GitHub Actions + SMTP 在云端定时运行每日报告。

```
本地开发: ./run.sh（agently-cli 或 SMTP）
云端定时: GitHub Actions 每天 9:00 UTC 自动执行（SMTP）
```

## 准备步骤

### 1. 配置邮件（SMTP）

在 `config.json` 中增加邮件配置：

```json
{
  "mail": {
    "type": "smtp",
    "smtp_host": "smtp.exmail.qq.com",
    "smtp_port": 587,
    "smtp_user": "your-email@company.com",
    "smtp_password": "your-app-password",
    "from_addr": "your-email@company.com"
  }
}
```

**获取邮件凭证示例（腾讯企业邮箱）：**
- 登录邮箱后台
- 账户设置 → SMTP 开启
- 生成应用专用密码（不是登录密码）

### 2. 创建 requirements.txt

```bash
pip freeze > requirements.txt
```

或手动创建关键依赖：

```
akshare>=1.15.0
pandas>=2.0.0
anthropic>=0.30.0
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
```

### 3. GitHub 配置

#### 步骤 A：创建 Personal Access Token

1. GitHub 右上角 → Settings → Developer settings → Personal access tokens
2. 点击"Generate new token (classic)"
3. 权限选中 `repo`（操作 private repo）
4. 复制 token 保存（仅显示一次）

#### 步骤 B：配置 GitHub Secrets

打开 GitHub repo → Settings → Secrets and variables → Actions

**必需的 Secrets：**

| Secret 名称 | 值 | 说明 |
|----------|-----|------|
| `CLAUDE_API_KEY` | `sk-ant-...` | Claude Haiku API 密钥 |
| `SMTP_HOST` | `smtp.exmail.qq.com` | 邮件服务器地址 |
| `SMTP_PORT` | `587` | SMTP 端口 |
| `SMTP_USER` | `your-email@company.com` | 发信邮箱 |
| `SMTP_PASSWORD` | `your-app-password` | 应用专用密码 |
| `RECIPIENTS` | `irene06@qq.com,3101928126@qq.com` | 收件人列表（逗号分隔） |

**可选的 Secrets（用于 S3 存储）：**

| Secret 名称 | 值 | 说明 |
|----------|-----|------|
| `AWS_ACCESS_KEY_ID` | `AKIAIOSFODNN7EXAMPLE` | AWS 访问密钥 |
| `AWS_SECRET_ACCESS_KEY` | `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` | AWS 秘密密钥 |
| `S3_BUCKET` | `my-reports-bucket` | S3 桶名称 |

### 4. AWS S3 配置（可选，用于存档报告）

#### 创建 S3 桶

```bash
# 在 AWS CLI 中运行
aws s3 mb s3://my-reports-bucket --region us-east-1
```

#### 创建 IAM 用户与访问密钥

1. AWS Console → IAM → Users → Create user
2. 用户名：`github-daily-report`
3. 创建后进入用户详情页 → Security credentials → Create access key
4. 选择 "Third-party service"，复制 Access Key 和 Secret Key

#### 创建与附加策略

创建文件 `s3-policy.json`：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::my-reports-bucket/*"
    }
  ]
}
```

然后附加到 IAM 用户（IAM Console → Users → github-daily-report → Permissions → Add inline policy）

## 工作流详解

### 工作流文件

`.github/workflows/daily-report.yml` 定义了自动化步骤：

1. **触发条件**
   - 每天 09:00 UTC（北京时间 17:00）自动执行
   - 或手动通过 GitHub UI 触发

2. **执行步骤**
   - 拉取代码
   - 安装 Python 依赖
   - 从 Secrets 覆盖配置
   - 执行 `python daily_report.py`
   - （可选）上传报告到 S3

3. **失败处理**
   - 保存错误日志为 artifact（7天内可下载）
   - 失败时不会中断（发送失败后继续）

### 本地与云端的区别

| 方面 | 本地 | 云端 |
|-----|------|------|
| 执行环境 | macOS / Linux | Ubuntu |
| 邮件方式 | agently-cli 或 SMTP | SMTP |
| 定时方式 | `crontab` 或手动运行 | GitHub Actions 原生调度 |
| 报告存储 | 本地 `reports/` | 本地 + 可选 S3 |
| 凭证管理 | config.json | GitHub Secrets |

## 测试与监控

### 手动触发工作流

GitHub repo → Actions → Daily Market Report → Run workflow → Run workflow

### 查看执行日志

GitHub repo → Actions → Daily Market Report → 点击最新运行 → logs

### 常见问题排查

**问题：SMTP 认证失败**
- 检查 `SMTP_PASSWORD` 是否是应用专用密码（不是登录密码）
- 检查邮箱是否启用了 SMTP
- 测试本地 SMTP 连接：
  ```bash
  python3 -c "import smtplib; smtplib.SMTP('smtp.exmail.qq.com', 587).starttls(); print('OK')"
  ```

**问题：GitHub Actions 超时**
- 工作流设置了 20 分钟超时
- 如果数据抓取太慢，可以增加超时时间或优化数据源

**问题：Claude API 调用失败**
- 检查 `CLAUDE_API_KEY` 是否正确
- 确保额度未用尽
- 查看 Claude API 控制面板

## 成本估算

**GitHub Actions：** 免费（2000 分钟/月）

**AWS S3（可选）：** ~$0.01/月
- 存储：300MB × $0.023/GB = $0.007/月
- 请求：30 PUT/月 = $0.0003/月

**总计：基本免费**

## 进阶配置

### 修改执行时间

编辑 `.github/workflows/daily-report.yml`：

```yaml
schedule:
  - cron: '0 9 * * *'  # 改为其他时间（UTC）
```

cron 格式：`分 时 日 月 周`

例子：
- `0 1 * * *` = 每天 1:00 UTC（北京时间 9:00）
- `0 */4 * * *` = 每 4 小时执行一次

### 添加 Slack 通知

在工作流中添加步骤（需要 Slack Webhook）：

```yaml
- name: 发送 Slack 通知
  if: always()
  run: |
    curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
      -H 'Content-Type: application/json' \
      -d '{"text":"报告已生成"}'
```

## 回退方案

如果云端出现问题，保留本地执行方式：

```bash
# 本地运行（需要 agently-cli 或 SMTP 配置）
./run.sh

# 从缓存重新渲染（快速重试）
./run.sh --from-cache reports/bundle_20260731.json
```
