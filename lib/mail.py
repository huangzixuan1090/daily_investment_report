"""发送 HTML 报告邮件（支持 SMTP 和 agently-cli）。

优先级：
  1. SMTP（config.mail.type="smtp"）- 云端推荐，无外部依赖
  2. agently-cli（config.mail.type="agently"） - 本地兼容，需 OAuth 授权
"""
from __future__ import annotations
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.utils
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

from . import common

log = common.log

NODE_DIR = "/Users/michaelhuang/.workbuddy/binaries/node/versions/22.22.2/bin"
AGENTLY_BIN = ("/Users/michaelhuang/.workbuddy/binaries/node/workspace"
               "/node_modules/.bin/agently-cli")


def _env() -> dict:
    e = dict(os.environ)
    e["PATH"] = NODE_DIR + ":" + e.get("PATH", "")
    return e


def _run(cmd: list, cwd: str | Path | None = None, timeout: int = 90) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=_env(),
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "agently-cli 未安装"
    except subprocess.TimeoutExpired:
        return 124, "", "超时"


def _send_smtp(smtp_cfg: dict, html_path: Path, subject: str, recipients: List[str]) -> bool:
    """用 SMTP 发送 HTML 邮件（无需外部依赖，云端推荐）。"""
    try:
        host = smtp_cfg.get("smtp_host")
        port = smtp_cfg.get("smtp_port", 587)
        user = smtp_cfg.get("smtp_user")
        password = smtp_cfg.get("smtp_password")
        from_addr = smtp_cfg.get("from_addr") or user

        if not all([host, user, password]):
            log.error("SMTP 配置不完整（需 smtp_host/smtp_user/smtp_password）")
            return False

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg["Date"] = email.utils.formatdate(localtime=True)

        part = email.mime.text.MIMEText(html_content, "html", "utf-8")
        msg.attach(part)

        import ssl
        ctx = ssl.create_default_context()

        def _try_ssl(p):
            log.info("尝试 SMTP_SSL %s:%d ...", host, p)
            with smtplib.SMTP_SSL(host, p, timeout=30, context=ctx) as srv:
                srv.login(user, password)
                srv.sendmail(from_addr, recipients, msg.as_string())

        def _try_starttls(p):
            log.info("尝试 STARTTLS %s:%d ...", host, p)
            with smtplib.SMTP(host, p, timeout=30) as srv:
                srv.starttls(context=ctx)
                srv.login(user, password)
                srv.sendmail(from_addr, recipients, msg.as_string())

        # 始终先试 465/SSL，再试配置端口/STARTTLS
        attempts = [
            (lambda: _try_ssl(465), "SSL:465"),
            (lambda: _try_starttls(587), "STARTTLS:587"),
        ]
        if port not in (465, 587):
            attempts.append((lambda: _try_ssl(port), f"SSL:{port}"))

        last_err = None
        for fn, label in attempts:
            try:
                fn()
                log.info("邮件已发送 via %s → %s", label, ", ".join(recipients))
                return True
            except Exception as e:
                log.warning("SMTP %s 失败: %s", label, e)
                last_err = e

        raise last_err
    except Exception as e:
        log.error("SMTP 发送失败: %s", e)
        return False


def _extract_json(text: str):
    """从输出里抠出第一段 JSON。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def is_ready() -> bool:
    rc, out, err = _run([AGENTLY_BIN, "+me"])
    return rc == 0 and "@\"" in out or rc == 0


def _refresh_token() -> bool:
    """非交互刷新 OAuth access token（救 access token 过期场景）。返回是否成功。"""
    rc, out, err = _run([AGENTLY_BIN, "auth", "refresh"], timeout=60)
    if rc == 0:
        return True
    # 部分版本 refresh 成功但退出码非 0，再查 status 兜底确认
    rc2, out2, _ = _run([AGENTLY_BIN, "auth", "status"], timeout=30)
    try:
        if rc2 == 0 and json.loads(out2).get("data", {}).get("token_status") == "valid":
            return True
    except Exception:
        pass
    log.warning("agently token 刷新失败 rc=%d: %s", rc, (err or out)[:200])
    return False


def send_report(cfg: dict, html_path: Path, subject: str,
                max_attempts: int = 3, backoff: tuple = (60, 300)) -> bool:
    """发送报告（智能路由：优先 SMTP，备选 agently-cli）。

    SMTP（云端推荐）：无外部依赖，无 token 过期问题。
    agently-cli（本地兼容）：OAuth 授权，15~30 天需续期。
    """
    recipients: List[str] = cfg.get("recipients", [])
    if not recipients:
        log.warning("未配置收件人")
        return False

    mail_cfg = cfg.get("mail") or {}
    mail_type = mail_cfg.get("type", "smtp").lower()

    # 优先尝试 SMTP
    if mail_type == "smtp" or (mail_type == "auto" and mail_cfg.get("smtp_host")):
        log.info("使用 SMTP 发送邮件...")
        if _send_smtp(mail_cfg, html_path, subject, recipients):
            return True
        if mail_type == "smtp":  # 强制 SMTP 但失败
            return False
        log.warning("SMTP 失败，尝试备选方案...")

    # 备选：agently-cli
    if not Path(AGENTLY_BIN).exists():
        log.error("agently-cli 未安装，无法发信")
        return False

    log.info("使用 agently-cli 发送邮件...")
    return _send_agently(html_path, subject, recipients, max_attempts, backoff)


def _send_agently(html_path: Path, subject: str, recipients: List[str],
                  max_attempts: int = 3, backoff: tuple = (60, 300)) -> bool:
    """agently-cli 发送实现（保持原有逻辑）。"""
    cwd = Path(html_path).parent
    body_file = Path(html_path).name
    base = [AGENTLY_BIN, "message", "+send", "--subject", subject,
            "--body-file", body_file]
    for to in recipients:
        base += ["--to", to]

    def _wait(i: int):
        secs = backoff[min(i, len(backoff) - 1)]
        log.info("  退避 %ds 后重试...", secs)
        time.sleep(secs)

    last_err = "未知错误"
    for attempt in range(1, max_attempts + 1):
        _refresh_token()
        rc, out, err = _run([AGENTLY_BIN, "+me"])
        if rc != 0:
            last_err = f"未授权: {(err or out)[:120]}"
            if attempt < max_attempts:
                log.warning("[发信 %d/%d] %s，将重试", attempt, max_attempts, last_err)
                _wait(attempt - 1)
                continue
            log.error("[发信 %d/%d] 授权彻底失效，需先 `agently-cli auth login`",
                      attempt, max_attempts)
            break

        cmd1 = list(base)
        log.info("[发信 %d/%d] 阶段1：请求发送", attempt, max_attempts)
        rc, out, err = _run(cmd1, cwd=cwd)
        if rc != 0:
            last_err = f"阶段1失败 rc={rc}: {(err or out)[:200]}"
            if attempt < max_attempts:
                log.warning("[发信 %d/%d] %s，将重试", attempt, max_attempts, last_err)
                _wait(attempt - 1)
                continue
            break

        data = _extract_json(out)
        token = None
        if isinstance(data, dict):
            d = data.get("data", data)
            token = d.get("confirmation_token") if isinstance(d, dict) else None
        if not token:
            if isinstance(data, dict) and data.get("data", {}).get("sent"):
                log.info("邮件已直接发送（无需确认）")
                return True
            last_err = "未取到 confirmation_token"
            if attempt < max_attempts:
                log.warning("[发信 %d/%d] %s，将重试", attempt, max_attempts, last_err)
                _wait(attempt - 1)
                continue
            break

        cmd2 = list(base) + ["--confirmation-token", token]
        log.info("[发信 %d/%d] 阶段2：确认发送 (token=%s...)",
                 attempt, max_attempts, token[:12])
        rc, out, err = _run(cmd2, cwd=cwd)
        if rc != 0:
            last_err = f"阶段2失败 rc={rc}: {(err or out)[:200]}"
            if attempt < max_attempts:
                log.warning("[发信 %d/%d] %s，将重试", attempt, max_attempts, last_err)
                _wait(attempt - 1)
                continue
            break
        log.info("发送完成 → %s", ", ".join(recipients))
        return True

    try:
        stem = Path(html_path).stem
        m = re.search(r"(\d{8})", stem)
        date8 = m.group(1) if m else "YYYYMMDD"
        flag = Path(html_path).parent / f"SEND_FAILED_{stem}.flag"
        flag.write_text(
            f"发送失败时间: {datetime.now().astimezone().isoformat()}\n"
            f"原因: {last_err}\n"
            f"处理: 先 `agently-cli auth login` 续期，再 "
            f"`./run.sh --from-cache reports/bundle_{date8}.json` 补发\n",
            encoding="utf-8")
        log.error("发信最终失败（已重试 %d 次），告警标记: %s", max_attempts, flag)
    except Exception as e:
        log.error("发信最终失败（已重试 %d 次）：%s；标记文件写入失败: %s",
                  max_attempts, last_err, e)
    return False


if __name__ == "__main__":
    cfg = common.load_config()
    ready = is_ready()
    print("agently ready:", ready)
