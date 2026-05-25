#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQBot Quick Setup — 多供应商 API 配置

参考 OpenCode / Claude Code 的供应商配置模式：
  - 预置多家 LLM 供应商（DeepSeek / OpenAI / SiliconFlow / 自定义）
  - 预置视觉识别供应商（MiMo / SiliconFlow / OpenAI）
  - 自动检测可用端口
  - 交互式选择，支持自定义端点

用法：
  python scripts/setup_config.py
"""

import os
import sys
import secrets
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
TPL_DIR = BOT_DIR / "templates"
HERMES_HOME = Path.home() / ".hermes"


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text

def green(t): return c(t, "32")
def yellow(t): return c(t, "33")
def red(t): return c(t, "31")
def bold(t): return c(t, "1")
def dim(t): return c(t, "2")


# ── LLM 供应商预置 ──
LLM_PROVIDERS = {
    "1": {
        "name": "DeepSeek（推荐）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "2": {
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "3": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3"],
        "npm": "@ai-sdk/openai",
    },
    "4": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
        "models": ["claude-opus-4-20250514", "claude-sonnet-4-20250514", "claude-haiku-4-5-20250514"],
        "npm": "@ai-sdk/anthropic",
    },
    "5": {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-235B-A22B"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "6": {
        "name": "Moonshot AI (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-auto",
        "models": ["moonshot-v1-auto", "moonshot-v1-8k", "moonshot-v1-32k"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "7": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-Text-01",
        "models": ["MiniMax-Text-01"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "8": {
        "name": "Ollama（本地）",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:14b",
        "models": ["qwen3:14b", "llama3:8b", "deepseek-r1:14b"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "9": {
        "name": "LM Studio（本地）",
        "base_url": "http://localhost:1234/v1",
        "model": "local-model",
        "models": ["local-model"],
        "npm": "@ai-sdk/openai-compatible",
    },
    "0": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "models": [],
        "npm": "@ai-sdk/openai-compatible",
    },
}

# ── 视觉识别供应商预置 ──
VISION_PROVIDERS = {
    "1": {
        "name": "MiMo（推荐）",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "model": "mimo-v2.5",
    },
    "2": {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-VL-32B-Instruct",
    },
    "3": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
    "4": {
        "name": "自定义",
        "base_url": "",
        "model": "",
    },
}

# ── 端口配置 ──
DEFAULT_PORTS = {
    "napcat_http": 3000,
    "napcat_ws": 3001,
    "dashboard": 8899,
    "gateway": 18789,
    "tts": 5000,
    "live2d": 19919,
}


def ask(prompt, default="", required=False, secret=False):
    while True:
        suffix = f" [{default}]" if default else ""
        if secret:
            val = input(f"  {prompt}{suffix}: ").strip()
        else:
            val = input(f"  {prompt}{suffix}: ").strip()
        if not val and default:
            return default
        if not val and required:
            print(red("    此项必填"))
            continue
        return val


def choose(prompt, options, default="1"):
    """显示选项列表，返回选中的 key。"""
    print(f"\n  {prompt}")
    for k, v in options.items():
        extra = ""
        if v.get("base_url"):
            extra = dim(f"  ({v['base_url']})")
        print(f"    {k}. {v['name']}{extra}")
    print()
    while True:
        choice = input(f"  选择 [{default}]: ").strip() or default
        if choice in options:
            return choice, options[choice]
        print(red("    无效选项"))


def choose_model(provider_info):
    """选择模型。"""
    models = provider_info.get("models", [])
    if not models:
        return ask("  模型名称", required=True)
    if len(models) == 1:
        print(f"  模型: {models[0]}")
        return models[0]
    print("\n  可用模型:")
    for i, m in enumerate(models, 1):
        print(f"    {i}. {m}")
    print()
    while True:
        choice = input(f"  选择 [1]: ").strip() or "1"
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            return models[idx]
        print(red("    无效选项"))


def check_port(port):
    """检查端口是否被占用。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def auto_detect_ports():
    """自动检测可用端口。"""
    ports = {}
    for name, default in DEFAULT_PORTS.items():
        if check_port(default):
            ports[name] = default
        else:
            # 找一个可用的
            for p in range(default + 1, default + 100):
                if not check_port(p):
                    ports[name] = p
                    break
            else:
                ports[name] = default
    return ports


def generate_config(deepseek_key, mimo_key, vision_key, anysearch_key,
                    onebot_token, gateway_token, qq_group, channel_name,
                    character_name, llm_url, llm_model, vision_url, vision_model,
                    feishu_id, feishu_secret, terminal_cwd, ports):
    tpl = (TPL_DIR / "config-template.yaml").read_text(encoding="utf-8")
    replacements = {
        "{{DEEPSEEK_API_KEY}}": deepseek_key,
        "{{MIMO_TOKEN}}": mimo_key or vision_key,
        "{{ANYSEARCH_KEY}}": anysearch_key,
        "{{OPENROUTER_KEY}}": vision_key,
        "{{ONEBOT_ACCESS_TOKEN}}": onebot_token,
        "{{GATEWAY_AUTH_TOKEN}}": gateway_token,
        "{{HOME_CHANNEL}}": qq_group,
        "{{CHANNEL_NAME}}": channel_name,
        "{{CHARACTER_NAME}}": character_name,
        "{{FEISHU_APP_ID}}": feishu_id,
        "{{FEISHU_SECRET}}": feishu_secret,
        "{{TERMINAL_CWD}}": terminal_cwd,
        "{{STICKER_PATH}}": str(HERMES_HOME / "stickers"),
    }
    for old, new in replacements.items():
        tpl = tpl.replace(old, new)

    # 替换 LLM base_url 和 model
    tpl = tpl.replace("https://opencode.ai/zen/go/v1", llm_url)
    tpl = tpl.replace("deepseek-v4-flash", llm_model)

    # 替换 vision base_url 和 model
    tpl = tpl.replace("https://token-plan-cn.xiaomimimo.com/v1", vision_url)
    tpl = tpl.replace("mimo-v2.5", vision_model)

    return tpl


def generate_env(deepseek_key, mimo_key, vision_key, anysearch_key,
                 onebot_token, gateway_token, qq_app_id, qq_secret, ports,
                 knowledge_path):
    tpl = (TPL_DIR / ".env.template").read_text(encoding="utf-8")
    replacements = {
        "{{HERMES_HOME_PATH}}": str(HERMES_HOME),
        "{{OPENAI_API_KEY}}": deepseek_key,
        "{{OPENROUTER_API_KEY}}": vision_key or "",
        "{{ONEBOT_ACCESS_TOKEN}}": onebot_token,
        "{{GATEWAY_AUTH_TOKEN}}": gateway_token,
        "{{DEEPSEEK_API_KEY}}": deepseek_key,
        "{{QQ_APP_ID}}": qq_app_id or "",
        "{{QQ_CLIENT_SECRET}}": qq_secret or "",
        "{{MIMO_TOKEN}}": mimo_key or "",
        "{{KNOWLEDGE_PATH}}": knowledge_path,
        "{{BOT_ROOT}}": str(BOT_DIR),
    }
    for old, new in replacements.items():
        tpl = tpl.replace(old, new)
    return tpl


def main():
    print()
    print(bold("  ╔═══════════════════════════════════════╗"))
    print(bold("  ║       QQBot Quick Setup               ║"))
    print(bold("  ╚═══════════════════════════════════════╝"))
    print()

    # ── 1. LLM 供应商 ──
    print(bold("  [1/4] LLM 模型配置"))
    llm_choice, llm_info = choose("选择 LLM 供应商:", LLM_PROVIDERS, default="1")
    if llm_choice == "0":
        llm_url = ask("API 端点 (base_url)", required=True)
        llm_model = ask("模型名称", required=True)
    else:
        llm_url = llm_info["base_url"]
        llm_model = choose_model(llm_info)
    llm_key = ask("API Key", required=True, secret=True)
    print()

    # ── 2. 视觉识别 ──
    print(bold("  [2/4] 图片识别（可选）"))
    vis_choice, vis_info = choose("选择视觉供应商:", VISION_PROVIDERS, default="1")
    vision_url = vis_info["base_url"]
    vision_model = vis_info["model"]
    if vis_choice == "4":
        vision_url = ask("视觉 API 端点", required=True)
        vision_model = ask("视觉模型名称", required=True)
    vision_key = ask("视觉 API Key（可跳过）", default="", secret=True)
    print()

    # ── 3. 搜索 ──
    print(bold("  [3/4] 网络搜索（可选）"))
    anysearch_key = ask("AnySearch Key（可跳过）", default="", secret=True)
    print()

    # ── 4. QQ 配置 ──
    print(bold("  [4/4] QQ 群配置"))
    qq_group = ask("QQ 群号", required=True)
    channel_name = ask("群名称", default="默认群")
    character_name = ask("角色名称", default="QQBot")
    terminal_cwd = ask("知识库路径", default=str(BOT_DIR))
    print()

    print(dim("  [选填] 飞书"))
    feishu_id = ask("飞书 App ID（可跳过）", default="")
    feishu_secret = ask("飞书 App Secret（可跳过）", default="", secret=True)
    print()

    print(dim("  [选填] QQ 官方 Bot"))
    qq_app_id = ask("QQ App ID（可跳过）", default="")
    qq_secret = ask("QQ Client Secret（可跳过）", default="", secret=True)
    print()

    # ── 自动生成 token ──
    onebot_token = secrets.token_urlsafe(16)
    gateway_token = secrets.token_hex(24)

    # ── 自动检测端口 ──
    print(dim("  检测端口..."))
    ports = auto_detect_ports()
    print(f"    NapCat: :{ports['napcat_http']} / :{ports['napcat_ws']}")
    print(f"    Dashboard: :{ports['dashboard']}")
    print(f"    Gateway: :{ports['gateway']}")
    print()

    # ── 生成配置 ──
    print(bold("  生成配置文件..."))
    HERMES_HOME.mkdir(parents=True, exist_ok=True)

    cfg = generate_config(
        llm_key, "", vision_key, anysearch_key,
        onebot_token, gateway_token, qq_group, channel_name,
        character_name, llm_url, llm_model, vision_url, vision_model,
        feishu_id, feishu_secret, terminal_cwd, ports
    )
    cfg_path = HERMES_HOME / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")
    print(f"    ✓ {cfg_path}")

    knowledge_dir = terminal_cwd + "/knowledge"
    env = generate_env(
        llm_key, "", vision_key, anysearch_key,
        onebot_token, gateway_token, qq_app_id, qq_secret, ports,
        knowledge_dir
    )
    env_path = HERMES_HOME / ".env"
    env_path.write_text(env, encoding="utf-8")
    print(f"    ✓ {env_path}")

    # SOUL.md
    soul_src = TPL_DIR / "SOUL-template.md"
    soul_dst = HERMES_HOME / "SOUL.md"
    if soul_src.exists() and not soul_dst.exists():
        soul = soul_src.read_text(encoding="utf-8")
        soul = soul.replace("{{CHARACTER_NAME}}", character_name)
        soul = soul.replace("{{STICKER_PATH}}", str(HERMES_HOME / "stickers"))
        soul = soul.replace("{{KNOWLEDGE_PATH}}", terminal_cwd + "/knowledge")
        soul = soul.replace("{{HOME_CHANNEL}}", qq_group)
        soul = soul.replace("{{CHANNEL_NAME}}", channel_name)
        soul_dst.write_text(soul, encoding="utf-8")
        print(f"    ✓ {soul_dst}")
    else:
        print(f"    - {soul_dst} (已存在，跳过)")

    # NapCat 配置
    napcat_tpl = TPL_DIR / "napcat"
    napcat_cfg = BOT_DIR / "napcat" / "napcat" / "config"
    napcat_cfg.mkdir(parents=True, exist_ok=True)

    # onebot11_<QQ>.json
    onebot_src = napcat_tpl / "onebot11.json"
    if onebot_src.exists():
        onebot = onebot_src.read_text(encoding="utf-8")
        onebot = onebot.replace("{{ONEBOT_TOKEN}}", onebot_token)
        onebot_dst = napcat_cfg / f"onebot11_{qq_group}.json"
        if not onebot_dst.exists():
            onebot_dst.write_text(onebot, encoding="utf-8")
            print(f"    ✓ {onebot_dst}")

    # napcat_<QQ>.json（防检测开关）
    napcat_src = napcat_tpl / "napcat.json"
    if napcat_src.exists():
        napcat_dst = napcat_cfg / f"napcat_{qq_group}.json"
        if not napcat_dst.exists():
            napcat_cfg_data = napcat_src.read_text(encoding="utf-8")
            napcat_dst.write_text(napcat_cfg_data, encoding="utf-8")
            print(f"    ✓ {napcat_dst}")

    # ── 完成 ──
    print()
    print(green("  ✓ 配置完成！"))
    print()
    print(f"  LLM:      {llm_info['name']} / {llm_model}")
    print(f"  视觉:     {vis_info['name']} / {vision_model}")
    print(f"  搜索:     {'AnySearch' if anysearch_key else '未配置'}")
    print(f"  QQ 群:    {qq_group} ({channel_name})")
    print(f"  角色:     {character_name}")
    print()
    print("  下一步:")
    print("    1. 启动 NapCat 扫码登录")
    print("    2. 运行 start.bat")
    print(f"    3. 打开 http://127.0.0.1:{ports['dashboard']}")
    print()


if __name__ == "__main__":
    main()
