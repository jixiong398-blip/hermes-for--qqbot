"""NapCat capability tools for the OneBot platform (v0.14.11 rebuild).

能力层：每个 qq_* 工具的 description 承载使用边界（对齐清单 A/B）。
所有工具**同步执行**——每次调用新建 `httpx.Client(trust_env=False)`，
独立于 gateway 事件循环，可安全在 delegate_task 子代理线程中运行
（避免跨 loop 使用 adapter 的 cached AsyncClient）。

红线：零硬编码路径/外部指向；bot 名字读运行时配置（ONEBOT_BOT_NAME）。
"""

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ADMIN_ONLY_HINT = "仅当管理员明确要求时使用。非管理群收到管理请求时礼貌拒绝。"
HONESTY_HINT = "调用后如实报告结果；失败就说明原因，不得编造失败。"


# ── 运行时配置解析 ─────────────────────────────────────────────

def _adapter():
    """定位运行中的 OneBotAdapter 实例（双命名空间探测）。

    插件以 ``hermes_plugins.onebot_platform`` 命名空间加载，而本模块
    ``plugins.platforms.onebot`` 是同一物理文件的不同模块对象——实例
    只注册在其中一个类上。按正确顺序探测两者。
    """
    import importlib
    for mod in ("hermes_plugins.onebot_platform.adapter",
                "plugins.platforms.onebot.adapter"):
        try:
            m = importlib.import_module(mod)
            inst = m.OneBotAdapter.get_instance()
            if inst is not None:
                return inst
        except Exception:
            continue
    return None


def _http_base() -> str:
    import os
    a = _adapter()
    return (getattr(a, "_http_url", "")
            or os.getenv("ONEBOT_HTTP_URL", "")
            or "http://127.0.0.1:3000").rstrip("/")


def _http_token() -> str:
    import os
    a = _adapter()
    return getattr(a, "_access_token", "") or os.getenv("ONEBOT_ACCESS_TOKEN", "")


def _admin_id() -> str:
    import os
    a = _adapter()
    return str(getattr(a, "_admin_id", "") or os.getenv("ONEBOT_ADMIN_ID", "") or "")


def _bot_name() -> str:
    import os
    a = _adapter()
    return getattr(a, "_bot_name", "") or os.getenv("ONEBOT_BOT_NAME", "") or "Soyo"


def _self_id() -> str:
    import os
    a = _adapter()
    return str(getattr(a, "_self_id", "") or os.getenv("ONEBOT_SELF_ID", "") or "")


def _call(action: str, params: dict, retries: int = 2) -> Dict[str, Any]:
    """同步调用 NapCat HTTP API。每次新建 client，trust_env=False（避开代理坑）。"""
    import httpx
    headers = {"Authorization": f"Bearer {_http_token()}"} if _http_token() else {}
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with httpx.Client(base_url=_http_base(), timeout=15.0,
                              headers=headers, trust_env=False) as client:
                resp = client.post(action, json=params)
                result = resp.json()
                if result.get("retcode") == 0:
                    return {"success": True, "data": result.get("data", {})}
                last_err = f"retcode={result.get('retcode')} msg={result.get('msg', result.get('message', ''))}"
        except Exception as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(0.5)
    return {"success": False, "error": last_err or "unknown"}


def _notify_admin(text: str) -> None:
    """向管理员私聊发送通知（同步 HTTP，独立于主 loop）。"""
    aid = _admin_id()
    if not aid:
        return
    try:
        _call("send_private_msg", {
            "user_id": int(aid),
            "message": [{"type": "text", "data": {"text": text}}],
        })
    except Exception as e:
        logger.error("[qq_napcat] 通知管理员失败: %s", e)


def _mark_recalled(group_id: str, message_id: str) -> None:
    """在 corpus 中标记消息为已撤回（记忆保留，检索时标注忽略）。"""
    if not message_id:
        return
    try:
        from plugins.platforms.onebot.adapter import get_state_db_path
        import sqlite3 as _sq
        _db = _sq.connect(str(get_state_db_path()), timeout=10)
        cols = {r[1] for r in _db.execute("PRAGMA table_info(corpus_messages)")}
        if "recalled" not in cols:
            _db.execute("ALTER TABLE corpus_messages ADD COLUMN recalled INTEGER DEFAULT 0")
        cur = _db.execute(
            "UPDATE corpus_messages SET recalled=1 WHERE message_id=? AND chat_id=?",
            (message_id, group_id))
        _db.commit()
        _db.close()
        logger.info("[qq_napcat] 标记已撤回 message_id=%s group=%s rows=%d", message_id, group_id, cur.rowcount)
    except Exception as e:
        logger.warning("[qq_napcat] 撤回标记失败: %s", e)


def _gid(group_id: str) -> Optional[int]:
    try:
        return int(str(group_id).strip())
    except (TypeError, ValueError):
        return None


def _uid(user_id: str) -> Optional[int]:
    try:
        return int(str(user_id).strip())
    except (TypeError, ValueError):
        return None


# ── 转发辅助 ───────────────────────────────────────────────────

def _parse_media_urls(raw) -> list:
    """解析持久化的 media_paths JSON 为 URL 列表。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(u) for u in raw if u]
    try:
        val = json.loads(raw)
        return [str(u) for u in val if u] if isinstance(val, list) else []
    except Exception:
        return []


def _clean_readable(text: str) -> str:
    """剥离历史污染占位文本（[引用…]/[回复消息]/[image:pending]/[图片]）。"""
    import re as _re
    t = text.strip()
    t = _re.sub(r'^\[引用[^\]]*\]\s*', '', t)
    t = _re.sub(r'(^|\s)\[回复消息\]', r'\1', t)
    t = t.replace('[image:pending]', '').replace('[图片]', '')
    return t.strip()


def _text_to_segments(text: str) -> list:
    """文本切段，把 @QQ<id> 字面量转成可点的 at 段。"""
    import re as _re
    segs = []
    pos = 0
    for m in _re.finditer(r'@QQ(\d+)', text):
        if m.start() > pos:
            segs.append({"type": "text", "data": {"text": text[pos:m.start()]}})
        segs.append({"type": "at", "data": {"qq": m.group(1)}})
        pos = m.end()
    if pos < len(text):
        segs.append({"type": "text", "data": {"text": text[pos:]}})
    return segs or [{"type": "text", "data": {"text": text}}]


_nick_cache: Dict[str, str] = {}


def _group_card_name(group_id: str, uid: str) -> str:
    """通过 API 取成员的群名片（按 group+uid 缓存）。"""
    key = f"{group_id}:{uid}"
    if key in _nick_cache:
        return _nick_cache[key]
    r = _call("get_group_member_info", {"group_id": int(group_id), "user_id": int(uid)})
    if r.get("success"):
        data = r.get("data") or {}
        name = str(data.get("card") or data.get("nickname") or "")
        _nick_cache[key] = name
        return name
    return ""


def _display_nick(is_bot: bool, group_id: str, sender_name: str) -> str:
    """转发卡片里的昵称：bot 用群名片，其他人用存储的发送者名。"""
    if is_bot:
        sid = _self_id()
        if sid:
            card = _group_card_name(group_id, sid)
            if card:
                return card
        return _bot_name()
    return sender_name or "群友"


def _is_msg_in_group(message_id: str, group_id: str) -> bool:
    """校验引用目标消息存在且属于当前群（否则转发卡片里引用会坏）。"""
    try:
        r = _call("get_msg", {"message_id": str(message_id)})
        if not r.get("success"):
            return False
        data = r.get("data") or {}
        return str(data.get("group_id", "")) == str(group_id)
    except Exception:
        return False


# ── 阶段 1：转发 ───────────────────────────────────────────────

def qq_forward_log(
    group_id: str,
    limit: int = 20,
    keyword: str = "",
    target: str = "",
    target_id: str = "",
    include_bot: bool = True,
) -> Dict[str, Any]:
    """把一段聊天记录打包成合并转发发出去（B：合并转发）。

    群友或管理员让 soyo「转发/打包/合并转发聊天记录」时用。数据从本地 corpus
    取（可关键词筛选）；每层最多 100 条、单条 ≤5000 字、引用尽量保留。
    target: group/private；target_id: 目标群号或 QQ 号。
    """
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    limit = max(1, min(int(limit or 20), 100))
    if not target:
        target = "group"
    if target not in ("group", "private") or not target_id:
        return {"success": False, "error": "需要 target(group/private) 和 target_id"}

    try:
        from plugins.platforms.onebot.adapter import get_state_db_path
        import sqlite3
        conn = sqlite3.connect(str(get_state_db_path()), timeout=10)
        conn.row_factory = sqlite3.Row
        q = ("SELECT sender_name, content_readable, is_bot, reply_to_id, media_paths FROM corpus_messages "
             "WHERE chat_id=? AND content_readable != '' AND content_readable NOT LIKE '%[image:pending]%' ")
        args: list = [str(gid)]
        if not include_bot:
            q += "AND is_bot=0 "
        if keyword:
            q += "AND content_readable LIKE ? "
            args.append(f"%{keyword}%")
        q += "ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(q, args).fetchall()
        conn.close()
    except Exception as e:
        return {"success": False, "error": f"读取聊天记录失败: {e}"}

    if not rows:
        return {"success": False, "error": "没有找到可转发的聊天记录"}

    nodes = []
    for r in reversed(rows):
        text = _clean_readable(r["content_readable"] or "")
        media = _parse_media_urls(r["media_paths"])
        if not text and not media:
            continue
        if len(text) > 5000:
            text = text[:5000]
        content = _text_to_segments(text)
        for mu in media[:3]:
            content.append({"type": "image", "data": {"file": mu}})
        rid = (r["reply_to_id"] or "").strip()
        if rid and _is_msg_in_group(rid, str(gid)):
            content.insert(0, {"type": "reply", "data": {"id": rid}})
        nodes.append({"type": "node", "data": {
            "nickname": _display_nick(r["is_bot"], str(gid), r["sender_name"] or ""),
            "content": content,
        }})
    if not nodes:
        return {"success": False, "error": "记录内容为空"}

    action = "send_group_forward_msg" if target == "group" else "send_private_forward_msg"
    key = "group_id" if target == "group" else "user_id"
    tid = _gid(target_id) if target == "group" else _uid(target_id)
    if tid is None:
        return {"success": False, "error": "target_id 无效"}
    return _call(action, {key: tid, "messages": nodes})


def qq_forward_msg(
    message_id: str = "",
    target: str = "",
    target_id: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """把一条消息转发给目标（A：人设化转述 / 原样转发补充）。

    提供 note 时按人设化转述（复述关键信息 + 自己的话）私聊发给 target_id；
    不提供 note 时原样转发（forward_group_single_msg / forward_friend_single_msg）。
    """
    if note:
        if not target_id:
            return {"success": False, "error": "人设化转述需要 target_id（私聊 QQ 号）"}
        tid = _uid(target_id)
        if tid is None:
            return {"success": False, "error": "target_id 无效"}
        r = _call("send_private_msg", {"user_id": tid, "message": [{"type": "text", "data": {"text": note}}]})
        if r.get("success"):
            r["data"] = {"mode": "persona_note", "to": str(tid)}
        return r

    if not message_id or not target or not target_id:
        return {"success": False, "error": "原样转发需要 message_id + target + target_id"}
    if target not in ("group", "private"):
        return {"success": False, "error": "target 必须为 group/private"}
    action = "forward_group_single_msg" if target == "group" else "forward_friend_single_msg"
    key = "group_id" if target == "group" else "user_id"
    tid = _gid(target_id) if target == "group" else _uid(target_id)
    if tid is None:
        return {"success": False, "error": "target_id 无效"}
    return _call(action, {key: tid, "message_id": str(message_id)})


# ── 阶段 1：群管理（禁言/解禁/踢人/撤回）──────────────────────

def qq_ban(
    group_id: str,
    user_id: str,
    duration_minutes: int = 10,
    reason: str = "",
    notify_admin: bool = True,
) -> Dict[str, Any]:
    """禁言群成员（bot 须为群管理/群主）。

    边界：互喷垃圾话/人身攻击 → 先警告 → 无效再禁言（默认 10 分钟）；
    时长 1~43200 分钟（QQ 限制）；禁言后默认向管理员汇报；可主动解除。
    群友要求禁言别人（无管理员要求）时拒绝。"""
    gid = _gid(group_id)
    uid = _uid(user_id)
    if gid is None or uid is None:
        return {"success": False, "error": "group_id/user_id 无效"}
    duration = max(1, min(int(duration_minutes or 10), 43200))
    result = _call("set_group_ban", {"group_id": gid, "user_id": uid, "duration": duration * 60})
    if result.get("success") and notify_admin:
        _notify_admin(f"[禁言] 群 {gid} 禁言了 {uid} {duration} 分钟" + (f"，原因：{reason}" if reason else ""))
    return result


def qq_unban(group_id: str, user_id: str) -> Dict[str, Any]:
    """解除禁言（duration=0）。"""
    gid = _gid(group_id)
    uid = _uid(user_id)
    if gid is None or uid is None:
        return {"success": False, "error": "group_id/user_id 无效"}
    return _call("set_group_ban", {"group_id": gid, "user_id": uid, "duration": 0})


def qq_kick(group_id: str, user_id: str, reason: str = "") -> Dict[str, Any]:
    """踢出群成员。"""
    gid = _gid(group_id)
    uid = _uid(user_id)
    if gid is None or uid is None:
        return {"success": False, "error": "group_id/user_id 无效"}
    result = _call("set_group_kick", {"group_id": gid, "user_id": uid, "reject_add_request": False})
    if result.get("success"):
        _notify_admin(f"[踢人] 群 {gid} 移除了 {uid}" + (f"，原因：{reason}" if reason else ""))
    return result


def qq_recall(message_id: str, group_id: str = "") -> Dict[str, Any]:
    """撤回消息：自己的随时可撤；撤他人消息需 bot 为管理（自主或管理员要求均可）。

    撤回后在本地记录标记为已撤回（记忆保留，检索时标注忽略）。"""
    if not message_id:
        return {"success": False, "error": "message_id 不能为空"}
    result = _call("delete_msg", {"message_id": str(message_id)})
    if result.get("success") and group_id:
        _mark_recalled(str(group_id), str(message_id))
    return result


# ── 阶段 2：群互动 ─────────────────────────────────────────────

def qq_sign(group_id: str) -> Dict[str, Any]:
    """群打卡（每天刷新后准时打卡，针对 bot 所在群；每天一次）。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("send_group_sign", {"group_id": gid})


def qq_poke(user_id: str, group_id: str = "") -> Dict[str, Any]:
    """戳一戳。私聊戳好友只传 user_id；群里戳某人同时传 group_id + user_id。"""
    uid = _uid(user_id)
    if uid is None:
        return {"success": False, "error": "user_id 无效"}
    if group_id:
        gid = _gid(group_id)
        if gid is None:
            return {"success": False, "error": "group_id 无效"}
        return _call("send_poke", {"group_id": gid, "user_id": uid})
    return _call("send_poke", {"user_id": uid})


def qq_upload_file(group_id: str, file_path: str, name: str = "") -> Dict[str, Any]:
    """上传文件到群（报告/文档场景）。file_path 为本地文件路径。"""
    import os
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}"}
    return _call("upload_group_file", {
        "group_id": gid, "file": file_path, "name": name or os.path.basename(file_path)})


# ── 阶段 2：查询 ───────────────────────────────────────────────

def qq_members(group_id: str, limit: int = 50) -> Dict[str, Any]:
    """查询群成员列表（user_id/群名片/角色）。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    result = _call("get_group_member_list", {"group_id": gid})
    if result.get("success"):
        data = result.get("data", []) or []
        result["data"] = [
            {"user_id": m.get("user_id"), "card": m.get("card") or m.get("nickname"),
             "role": m.get("role")} for m in data[: int(limit)]
        ]
    return result


def qq_friends(limit: int = 50) -> Dict[str, Any]:
    """查询好友列表。"""
    result = _call("get_friend_list", {})
    if result.get("success"):
        data = result.get("data", []) or []
        result["data"] = [{"user_id": f.get("user_id"), "nickname": f.get("nickname")}
                          for f in data[: int(limit)]]
    return result


def qq_status() -> Dict[str, Any]:
    """查询 bot 登录状态与账号信息。"""
    r1 = _call("get_login_info", {})
    r2 = _call("get_status", {})
    return {"success": r1.get("success") or r2.get("success"),
            "data": {"login": r1.get("data", {}), "status": r2.get("data", {})},
            "error": "" if (r1.get("success") or r2.get("success")) else (r1.get("error") or r2.get("error"))}


def qq_at_all_remain(group_id: str) -> Dict[str, Any]:
    """查询 @全体剩余次数（仅管理/群主；每天约 10 次，除管理员要求外一般不用）。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("get_group_at_all_remain", {"group_id": gid})


def qq_get_msg(message_id: str) -> Dict[str, Any]:
    """按消息 ID 查询单条消息内容。"""
    if not message_id:
        return {"success": False, "error": "message_id 不能为空"}
    return _call("get_msg", {"message_id": str(message_id)})


def qq_get_msg_history(group_id: str, limit: int = 20) -> Dict[str, Any]:
    """查询群聊历史消息（NapCat 侧，用于转发数据源补充）。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("get_group_msg_history", {"group_id": gid, "message_seq": 0, "count": min(int(limit), 100)})


# ── 阶段 3：消息管理 ───────────────────────────────────────────

def qq_emoji_like(message_id: str, emoji_id: int = 32) -> Dict[str, Any]:
    """给消息加表情回应（可随意发挥，可用可不用）。"""
    if not message_id:
        return {"success": False, "error": "message_id 不能为空"}
    return _call("set_msg_emoji_like", {"message_id": str(message_id), "emoji_id": int(emoji_id)})


def qq_essence(message_id: str, group_id: str = "", action: str = "set") -> Dict[str, Any]:
    """设置/取消群精华消息（仅管理/群主，一般不用）。"""
    if action not in ("set", "delete"):
        return {"success": False, "error": "action 必须为 set/delete"}
    if not message_id:
        return {"success": False, "error": "message_id 不能为空"}
    if action == "set":
        return _call("set_essence_msg", {"message_id": str(message_id)})
    params = {"message_id": str(message_id)}
    gid = _gid(group_id) if group_id else None
    if gid is not None:
        params["group_id"] = gid
    return _call("delete_essence_msg", params)


# ── 阶段 3：群管理（仅管理员要求）──────────────────────────────

def qq_admin(group_id: str, user_id: str, action: str = "set") -> Dict[str, Any]:
    """设置/取消群管理员。"""
    if action not in ("set", "unset"):
        return {"success": False, "error": "action 必须为 set/unset"}
    gid = _gid(group_id)
    uid = _uid(user_id)
    if gid is None or uid is None:
        return {"success": False, "error": "group_id/user_id 无效"}
    return _call("set_group_admin", {"group_id": gid, "user_id": uid, "enable": action == "set"})


def qq_card(group_id: str, user_id: str, card: str) -> Dict[str, Any]:
    """设置群名片（bot 自己或其他成员，需管理权限）。"""
    gid = _gid(group_id)
    uid = _uid(user_id)
    if gid is None or uid is None:
        return {"success": False, "error": "group_id/user_id 无效"}
    return _call("set_group_card", {"group_id": gid, "user_id": uid, "card": str(card)})


def qq_group_name(group_id: str, name: str) -> Dict[str, Any]:
    """修改群名。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("set_group_name", {"group_id": gid, "group_name": str(name)[:32]})


def qq_leave(group_id: str) -> Dict[str, Any]:
    """退出群聊。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("set_group_leave", {"group_id": gid})


def qq_notice(group_id: str, action: str = "list", content: str = "", notice_id: str = "") -> Dict[str, Any]:
    """群公告：list 查公告（新/旧）；send 发公告（仅管理）；delete 删公告（仅管理）。

    NapCat 无公告推送事件，用 API 拉取。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    if action == "list":
        return _call("_get_group_notice", {"group_id": gid})
    if action == "send":
        if not content:
            return {"success": False, "error": "发送公告需要 content"}
        return _call("_send_group_notice", {"group_id": gid, "content": str(content)[:3000]})
    if action == "delete":
        if not notice_id:
            return {"success": False, "error": "删除公告需要 notice_id"}
        return _call("_del_group_notice", {"group_id": gid, "notice_id": str(notice_id)})
    return {"success": False, "error": "action 必须为 list/send/delete"}


def qq_group_avatar(group_id: str, file: str) -> Dict[str, Any]:
    """设置群头像。"""
    gid = _gid(group_id)
    if gid is None:
        return {"success": False, "error": "group_id 无效"}
    return _call("set_group_portrait", {"group_id": gid, "file": str(file)})


def qq_set_profile(field: str, value: str) -> Dict[str, Any]:
    """修改 bot 个人资料：avatar(头像文件路径)/longnick(个性签名)/online_status(在线状态)。"""
    if field == "avatar":
        return _call("set_qq_avatar", {"file": str(value)})
    if field == "longnick":
        return _call("set_self_longnick", {"longNick": str(value)[:80]})
    if field == "online_status":
        return _call("set_online_status", {"status": str(value), "ext_status": 0, "battery_status": 0})
    return {"success": False, "error": "field 必须为 avatar/longnick/online_status"}


# ── 邀请审批（语义审批，管理员明确指示时调用）──────────────────

def qq_invite_approve(group_id: str, approve: bool) -> Dict[str, Any]:
    """批准/拒绝加群邀请（管理员语义审批）。

    管理员明确指示「同意/拒绝某群加入邀请」时调用。group_id 为被邀请加入的群号。
    待审批邀请由 adapter 收到 request 事件时记录。"""
    a = _adapter()
    gid = str(group_id).strip()
    if a is None:
        return {"success": False, "error": "OneBot adapter 未就绪"}
    now = time.time()
    for flag, pend in list(getattr(a, "_invite_pending", {}).items()):
        if str(pend.get("group_id", "")) != gid:
            continue
        if now - pend.get("ts", 0) > 3600:
            a._invite_pending.pop(flag, None)
            continue
        r = _call("set_group_add_request", {"flag": flag, "approve": bool(approve)})
        a._invite_pending.pop(flag, None)
        if r.get("success"):
            _notify_admin(f"已{'同意' if approve else '拒绝'}群 {gid} 的加群邀请。")
        return r
    return {"success": False, "error": f"没有找到群 {gid} 的待审批邀请（可能已过期）"}


# ── 注册 ───────────────────────────────────────────────────────

def _make_handler(fn):
    """生成符合 registry dispatch 契约的 handler。

    dispatch 调 ``handler(args_dict, **meta)``——args dict 作为第一个位置参数。
    这里把它扁平化成 kwargs，按函数签名过滤，结果序列化为 JSON 字符串。
    """
    import inspect
    sig = inspect.signature(fn)

    def _handler(args, **kw):
        params = dict(args) if isinstance(args, dict) else {}
        params.update(kw or {})
        allowed = {k: v for k, v in params.items() if k in sig.parameters}
        logger.info("[qq_napcat] call %s args=%s", fn.__name__, json.dumps(allowed, ensure_ascii=False))
        try:
            result = fn(**allowed)
        except Exception as e:
            logger.exception("Tool %s execution failed: %s", fn.__name__, e)
            return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        if isinstance(result, str):
            _out = result
        else:
            _out = json.dumps(result, ensure_ascii=False)
        logger.info("[qq_napcat] %s result=%s", fn.__name__, _out[:300])
        return _out

    return _handler


def _reg(name: str, fn, desc: str, params: dict) -> None:
    from tools.registry import registry
    registry.register(
        name=name,
        toolset="onebot",
        schema={"type": "function", "function": {"name": name, "description": desc, "parameters": params}},
        handler=_make_handler(fn),
        check_fn=lambda: True,
    )


_reg("qq_forward_log", qq_forward_log,
     "合并转发聊天记录到群/私聊。群友或管理员让 soyo「转发/打包/合并转发聊天记录、消息、记录」时用。"
     "从群历史取消息打包成合并转发卡片发出（每层最多100条、单条≤5000字、引用保留）。"
     "参数：group_id=源群号，target=group/private，target_id=目标群号或QQ号。",
     {"type": "object", "properties": {
      "group_id": {"type": "string", "description": "源群号"},
      "limit": {"type": "integer", "description": "条数，默认20，最多100"},
      "keyword": {"type": "string", "description": "话题关键词筛选，可选"},
      "target": {"type": "string", "enum": ["group", "private"], "description": "目标类型"},
      "target_id": {"type": "string", "description": "目标群号或QQ号"},
      "include_bot": {"type": "boolean", "description": "是否包含 bot 自己的消息，默认true"}},
      "required": ["group_id", "target", "target_id"]})

_reg("qq_forward_msg", qq_forward_msg,
     "把一条消息转发给目标：提供 note 时按人设化转述（复述+自己的话）私聊发送；不提供时原样转发。",
     {"type": "object", "properties": {
      "message_id": {"type": "string", "description": "消息ID（原样转发需要）"},
      "target": {"type": "string", "enum": ["group", "private"], "description": "目标类型"},
      "target_id": {"type": "string", "description": "目标群号或QQ号"},
      "note": {"type": "string", "description": "人设化转述内容（可选）"}},
      "required": []})

_reg("qq_ban", qq_ban,
     "禁言群成员（需 bot 为管理）。边界：互喷垃圾话/人身攻击→先警告→无效再禁言默认10分钟；"
     "时长1~43200分钟；禁言后汇报管理员；可主动解除。群友要求禁言别人（无管理员要求）时拒绝。",
     {"type": "object", "properties": {
      "group_id": {"type": "string"}, "user_id": {"type": "string"},
      "duration_minutes": {"type": "integer", "description": "分钟，默认10"},
      "reason": {"type": "string", "description": "原因（汇报用）"},
      "notify_admin": {"type": "boolean", "description": "是否汇报管理员，默认true"}},
      "required": ["group_id", "user_id"]})

_reg("qq_unban", qq_unban, "解除禁言。", {
    "type": "object", "properties": {"group_id": {"type": "string"}, "user_id": {"type": "string"}},
    "required": ["group_id", "user_id"]})

_reg("qq_kick", qq_kick, "踢出群成员。" + ADMIN_ONLY_HINT, {
    "type": "object", "properties": {"group_id": {"type": "string"}, "user_id": {"type": "string"},
                                    "reason": {"type": "string"}},
    "required": ["group_id", "user_id"]})

_reg("qq_recall", qq_recall,
     "撤回消息：自己的随时可撤；撤他人消息需 bot 为管理（自主或管理员要求均可）。撤回后本地标记。",
     {"type": "object", "properties": {"message_id": {"type": "string"}, "group_id": {"type": "string"}},
      "required": ["message_id"]})

_reg("qq_sign", qq_sign, "群打卡（每天一次，针对 bot 所在群）。", {
    "type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})

_reg("qq_poke", qq_poke, "戳一戳。私聊戳好友只传 user_id；群里戳某人传 group_id + user_id。想戳就戳。", {
    "type": "object", "properties": {
        "user_id": {"type": "string", "description": "被戳的人 QQ 号"},
        "group_id": {"type": "string", "description": "群号（群里戳人才需要）"}},
    "required": ["user_id"]})

_reg("qq_upload_file", qq_upload_file, "上传文件到群（报告/文档场景）。", {
    "type": "object", "properties": {
        "group_id": {"type": "string"}, "file_path": {"type": "string"}, "name": {"type": "string"}},
    "required": ["group_id", "file_path"]})

_reg("qq_members", qq_members, "查询群成员列表（user_id/群名片/角色）。", {
    "type": "object", "properties": {"group_id": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["group_id"]})

_reg("qq_friends", qq_friends, "查询好友列表。", {
    "type": "object", "properties": {"limit": {"type": "integer"}}})

_reg("qq_status", qq_status, "查询 bot 登录状态与账号信息。", {"type": "object", "properties": {}})

_reg("qq_at_all_remain", qq_at_all_remain,
     "查询 @全体剩余次数（仅管理/群主；每天约10次，除管理员要求外一般不用）。",
     {"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})

_reg("qq_get_msg", qq_get_msg, "按消息ID查询单条消息内容。", {
    "type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]})

_reg("qq_get_msg_history", qq_get_msg_history, "查询群聊历史消息（NapCat 侧，转发数据源补充）。", {
    "type": "object", "properties": {"group_id": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["group_id"]})

_reg("qq_emoji_like", qq_emoji_like, "给消息加表情回应（可随意发挥，可用可不用）。", {
    "type": "object", "properties": {"message_id": {"type": "string"}, "emoji_id": {"type": "integer"}},
    "required": ["message_id"]})

_reg("qq_essence", qq_essence, "设置/取消群精华消息（仅管理/群主，一般不用）。", {
    "type": "object", "properties": {
        "message_id": {"type": "string"}, "group_id": {"type": "string"},
        "action": {"type": "string", "enum": ["set", "delete"]}},
    "required": ["message_id"]})

_reg("qq_admin", qq_admin, "设置/取消群管理员。" + ADMIN_ONLY_HINT, {
    "type": "object", "properties": {
        "group_id": {"type": "string"}, "user_id": {"type": "string"},
        "action": {"type": "string", "enum": ["set", "unset"]}},
    "required": ["group_id", "user_id"]})

_reg("qq_card", qq_card, "设置群名片（bot 自己或其他成员，需管理权限）。", {
    "type": "object", "properties": {
        "group_id": {"type": "string"}, "user_id": {"type": "string"}, "card": {"type": "string"}},
    "required": ["group_id", "user_id", "card"]})

_reg("qq_group_name", qq_group_name, "修改群名。" + ADMIN_ONLY_HINT, {
    "type": "object", "properties": {"group_id": {"type": "string"}, "name": {"type": "string"}},
    "required": ["group_id", "name"]})

_reg("qq_leave", qq_leave, "退出群聊。" + ADMIN_ONLY_HINT, {
    "type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})

_reg("qq_notice", qq_notice,
     "群公告：list 查公告（新/旧）；send 发公告（仅管理）；delete 删公告（仅管理）。NapCat 无公告推送事件，用 API 拉取。",
     {"type": "object", "properties": {
         "group_id": {"type": "string"},
         "action": {"type": "string", "enum": ["list", "send", "delete"]},
         "content": {"type": "string"},
         "notice_id": {"type": "string"}}, "required": ["group_id"]})

_reg("qq_group_avatar", qq_group_avatar, "设置群头像。" + ADMIN_ONLY_HINT, {
    "type": "object", "properties": {"group_id": {"type": "string"}, "file": {"type": "string"}},
    "required": ["group_id", "file"]})

_reg("qq_set_profile", qq_set_profile,
     "修改 bot 个人资料（avatar/longnick/online_status）。" + ADMIN_ONLY_HINT, {
     "type": "object", "properties": {
         "field": {"type": "string", "enum": ["avatar", "longnick", "online_status"]},
         "value": {"type": "string"}}, "required": ["field", "value"]})

_reg("qq_invite_approve", qq_invite_approve,
     "批准/拒绝加群邀请（管理员语义审批）。管理员明确指示同意/拒绝某群的加入邀请时调用。",
     {"type": "object", "properties": {
         "group_id": {"type": "string", "description": "被邀请加入的群号"},
         "approve": {"type": "boolean", "description": "true=同意, false=拒绝"}},
         "required": ["group_id", "approve"]})

logger.info("[qq_napcat_tools] registered %d tools", 25)

from tools.registry import registry  # noqa: E402 — 模块级，供 discover hook 使用

# validate_toolset("hermes-onebot") gates loading in model_tools: it must
# resolve through the registry alias so the qq_* tools (toolset="onebot")
# actually reach the agent.
registry.register_toolset_alias("hermes-onebot", "onebot")

# discover_builtin_tools 只自动导入含顶层 registry.register(...) 语句的模块。
# 真实注册都在上面的顶层 _reg(...) 调用中；这里补一个字面调用触发自动导入。
registry.register(
    name="__qq_napcat_loader__",
    toolset="__hidden__",
    schema={"type": "function", "function": {"name": "q", "description": "internal", "parameters": {"type": "object", "properties": {}}}},
    handler=lambda **kw: None,
    check_fn=lambda: False,
)
