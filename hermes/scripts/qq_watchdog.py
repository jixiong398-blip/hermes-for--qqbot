#!/usr/bin/env python3
"""QQ连接看门狗 - 检测断线 + 日志关键词报警"""
import json, os, time, socket, re

STATE_FILE = os.path.expanduser("~/.hermes/qq_watchdog_state.json")
LOG_FILE = "/home/ji/Napcat/log/napcat_{{BOT_QQ_ID}}.log"

# 只检测两类：快速登录失败（重启后）和被踢下线（在线久了被踢）
ALERT_PATTERNS = [
    "快速登录失败",
    "快速登录错误",
    "KickedOffLine",
    "被踢下线",
]

def check_connection():
    """检查 QQ 是否在线（端口检测）"""
    for port in [6099, 3001]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(('127.0.0.1', port))
            s.close()
            return True
        except:
            pass
    return False

def check_log_errors():
    """读取日志文件，检测最近的登录/断开错误"""
    state = {"online": True, "notified": False, "last_line": 0}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except:
            pass

    last_line = state.get("last_line", 0)
    new_alerts = []

    try:
        result = os.popen(f"wc -l < {LOG_FILE}").read().strip()
        total_lines = int(result) if result else 0

        if total_lines <= last_line:
            last_line = 0

        if total_lines > last_line:
            read_start = last_line + 1
            lines = os.popen(f"sed -n '{read_start},{total_lines}p' {LOG_FILE}").read().splitlines()

            for line in lines:
                for pattern in ALERT_PATTERNS:
                    if pattern in line:
                        ts_match = re.match(r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                        ts = ts_match.group(1) if ts_match else time.strftime("%m-%d %H:%M:%S")
                        if "快速登录" in pattern:
                            alert_msg = f"⚠️ 重启后快速登录失败 ({ts})"
                        else:
                            alert_msg = f"⚠️ QQ被踢下线 ({ts})"
                        if alert_msg not in new_alerts:
                            new_alerts.append(alert_msg)
                        break

        state["last_line"] = total_lines
    except:
        pass

    return state, new_alerts

def main():
    now_online = check_connection()
    state, new_alerts = check_log_errors()

    was_online = state.get("online", True)
    ts = time.strftime("%H:%M:%S")

    if new_alerts:
        for alert in new_alerts:
            print(alert)

    if was_online and not now_online:
        state["online"] = False
        state["notified"] = True
        state["at"] = ts
        print(f"⚠️ QQ Bot 端口断线了 ({ts})")
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        return

    if not was_online and now_online:
        state["online"] = True
        state["notified"] = False
        print(f"✅ QQ Bot 已恢复连接 ({ts})")
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        return

    state["online"] = now_online
    if now_online:
        state["notified"] = False
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

if __name__ == "__main__":
    main()
