#!/usr/bin/env python3
"""Hermes 健康检查脚本

检查项:
  1. corpus_messages 最近写入频率（连续 2 小时 0 写入 → 告警）
  2. persist worker 存活（通过最近写入时间推断）
  3. WebSocket 连接状态（从日志统计断连次数）
  4. 数据库完整性（PRAGMA integrity_check）

用法:
  python scripts/health_check.py           # 人类可读输出
  python scripts/health_check.py --json    # JSON 输出（供 Dashboard 调用）
  python scripts/health_check.py --cron    # 只输出告警，静默时无输出（供 cron）

退出码:
  0 = 全部健康
  1 = 有告警
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


def get_hermes_home() -> Path:
    """自动探测 ~/.hermes/ 路径，不硬编码。"""
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def get_state_db() -> Path:
    return get_hermes_home() / "state.db"


def get_logs_dir() -> Path:
    return get_hermes_home() / "logs"


def check_corpus_writes(db_path: Path) -> dict:
    """检查 corpus_messages 最近写入频率。"""
    result = {"name": "corpus_messages 写入", "status": "ok", "details": {}}
    if not db_path.exists():
        result["status"] = "critical"
        result["details"]["error"] = f"数据库不存在: {db_path}"
        return result

    try:
        with sqlite3.connect(str(db_path), timeout=5) as db:
            now = time.time()

            h1 = db.execute(
                "SELECT COUNT(*) FROM corpus_messages WHERE created_at > ?", (now - 3600,)
            ).fetchone()[0]

            h2 = db.execute(
                "SELECT COUNT(*) FROM corpus_messages WHERE created_at > ?", (now - 7200,)
            ).fetchone()[0]

            last = db.execute(
                "SELECT MAX(created_at) FROM corpus_messages"
            ).fetchone()[0]
            last_str = datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S") if last else "无记录"
            gap_min = int((now - last) / 60) if last else -1

            per_group = {}
            for r in db.execute(
                "SELECT group_id, COUNT(*) FROM corpus_messages "
                "WHERE created_at > ? AND chat_type='group' AND group_id!='' "
                "GROUP BY group_id ORDER BY COUNT(*) DESC",
                (now - 3600,),
            ):
                per_group[r[0]] = r[1]

            result["details"] = {
                "last_1h_count": h1,
                "last_2h_count": h2,
                "last_write_time": last_str,
                "minutes_since_last": gap_min,
                "per_group_1h": per_group,
            }

            if h2 == 0:
                result["status"] = "critical"
                result["details"]["alert"] = "连续 2 小时无写入，persist worker 可能已停止"
            elif h1 == 0:
                result["status"] = "warning"
                result["details"]["alert"] = "最近 1 小时无写入"
            elif gap_min > 30:
                result["status"] = "warning"
                result["details"]["alert"] = f"最近一次写入在 {gap_min} 分钟前"

    except sqlite3.Error as e:
        result["status"] = "critical"
        result["details"]["error"] = str(e)

    return result


def check_db_integrity(db_path: Path) -> dict:
    """数据库完整性检查。"""
    result = {"name": "数据库完整性", "status": "ok", "details": {}}
    if not db_path.exists():
        result["status"] = "critical"
        result["details"]["error"] = f"数据库不存在: {db_path}"
        return result

    try:
        with sqlite3.connect(str(db_path), timeout=5) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]

            if integrity != "ok":
                result["status"] = "critical"
                result["details"]["alert"] = integrity
            else:
                result["details"]["result"] = "ok"

    except sqlite3.Error as e:
        result["status"] = "critical"
        result["details"]["error"] = str(e)

    return result


def check_websocket_disconnects(logs_dir: Path) -> dict:
    """从日志统计 WebSocket 断连次数。"""
    result = {"name": "WebSocket 连接", "status": "ok", "details": {}}
    agent_log = logs_dir / "agent.log"

    if not agent_log.exists():
        result["details"]["note"] = "日志文件不存在，跳过"
        return result

    try:
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        one_hour_ago_str = one_hour_ago.strftime("%Y-%m-%d %H:")

        disconnects_1h = 0
        last_disconnect = ""

        with open(agent_log, "r", errors="replace") as f:
            for line in f:
                if "disconnect" in line.lower() or "connection lost" in line.lower() or "websocket" in line.lower() and "close" in line.lower():
                    if one_hour_ago_str in line:
                        disconnects_1h += 1
                    # Track last disconnect time
                    if len(line) >= 19:
                        last_disconnect = line[:19]

        result["details"] = {
            "disconnects_last_1h": disconnects_1h,
            "last_disconnect": last_disconnect or "无",
        }

        if disconnects_1h > 50:
            result["status"] = "critical"
            result["details"]["alert"] = f"1 小时内断连 {disconnects_1h} 次，超过阈值 50"
        elif disconnects_1h > 20:
            result["status"] = "warning"
            result["details"]["alert"] = f"1 小时内断连 {disconnects_1h} 次"

    except Exception as e:
        result["status"] = "warning"
        result["details"]["error"] = str(e)

    return result


def check_error_rate(logs_dir: Path) -> dict:
    """检查最近错误日志频率。"""
    result = {"name": "错误日志", "status": "ok", "details": {}}
    error_log = logs_dir / "errors.log"

    if not error_log.exists():
        result["details"]["note"] = "错误日志不存在"
        return result

    try:
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        one_hour_ago_str = one_hour_ago.strftime("%Y-%m-%d %H:")

        errors_1h = 0
        persist_errors_1h = 0

        with open(error_log, "r", errors="replace") as f:
            for line in f:
                if one_hour_ago_str in line:
                    errors_1h += 1
                    if "Persist worker" in line or "persist" in line.lower():
                        persist_errors_1h += 1

        result["details"] = {
            "errors_last_1h": errors_1h,
            "persist_errors_last_1h": persist_errors_1h,
        }

        if persist_errors_1h > 5:
            result["status"] = "critical"
            result["details"]["alert"] = f"persist worker 错误 {persist_errors_1h} 次/小时"
        elif errors_1h > 20:
            result["status"] = "warning"
            result["details"]["alert"] = f"错误日志 {errors_1h} 条/小时"

    except Exception as e:
        result["status"] = "warning"
        result["details"]["error"] = str(e)

    return result


def run_all_checks() -> list:
    """运行所有检查，返回结果列表。"""
    db_path = get_state_db()
    logs_dir = get_logs_dir()

    return [
        check_corpus_writes(db_path),
        check_db_integrity(db_path),
        check_websocket_disconnects(logs_dir),
        check_error_rate(logs_dir),
    ]


def format_human(results: list) -> str:
    """人类可读输出。"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"Hermes 健康检查 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)

    has_alert = False
    for r in results:
        icon = {"ok": "✅", "warning": "⚠️ ", "critical": "❌"}.get(r["status"], "?")
        lines.append(f"\n{icon} {r['name']} [{r['status']}]")

        for k, v in r["details"].items():
            if k == "alert":
                lines.append(f"   ⚠️  {k}: {v}")
                has_alert = True
            elif k == "error":
                lines.append(f"   ❌ {k}: {v}")
                has_alert = True
            else:
                lines.append(f"   {k}: {v}")

    lines.append("\n" + "=" * 60)
    if has_alert:
        lines.append("结论: 有告警，请检查")
    else:
        lines.append("结论: 全部健康")

    return "\n".join(lines)


def format_json(results: list) -> str:
    """JSON 输出。"""
    overall = "ok"
    for r in results:
        if r["status"] == "critical":
            overall = "critical"
            break
        elif r["status"] == "warning" and overall != "critical":
            overall = "warning"

    return json.dumps({
        "timestamp": datetime.now().isoformat(),
        "overall": overall,
        "checks": results,
    }, ensure_ascii=False, indent=2)


def format_cron(results: list) -> str:
    """cron 模式：只输出告警，静默时无输出。"""
    alerts = []
    for r in results:
        if r["status"] != "ok":
            alert = f"[{r['status'].upper()}] {r['name']}"
            if "alert" in r["details"]:
                alert += f": {r['details']['alert']}"
            elif "error" in r["details"]:
                alert += f": {r['details']['error']}"
            alerts.append(alert)

    if alerts:
        return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Hermes 健康告警:\n" + "\n".join(alerts)
    return ""


def main():
    parser = argparse.ArgumentParser(description="Hermes 健康检查")
    parser.add_argument("--json", action="store_true", help="JSON 输出（供 Dashboard）")
    parser.add_argument("--cron", action="store_true", help="只输出告警（供 cron）")
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        print(format_json(results))
    elif args.cron:
        output = format_cron(results)
        if output:
            print(output)
    else:
        print(format_human(results))

    # 退出码
    has_critical = any(r["status"] == "critical" for r in results)
    has_warning = any(r["status"] == "warning" for r in results)
    sys.exit(1 if (has_critical or has_warning) else 0)


if __name__ == "__main__":
    main()
