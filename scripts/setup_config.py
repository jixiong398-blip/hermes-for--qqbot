#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQBot Quick Setup — 多供应商 API 配置

交互式配置工具：供应商 → 模型 → 密钥 → 自动生成 config.yaml + .env

用法：
  配置API.bat
"""

import os, sys, secrets, json as _json
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
TPL_DIR = BOT_DIR / "templates"
HERMES_HOME = Path.home() / ".hermes"


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text
def green(t): return c(t, "32")
def red(t):   return c(t, "31")
def bold(t):  return c(t, "1")
def dim(t):   return c(t, "2")


# ════════════════════════════════════════════════════════════════
# LLM 主模型供应商（全部 OpenAI 兼容，除 Anthropic 由 Hermes 内置支持）
# ════════════════════════════════════════════════════════════════
LLM_PROVIDERS = {
    "1": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
          "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"]},
    "2": {"name": "OpenCode Go（推荐，一站式）", "base_url": "https://opencode.ai/zen/go/v1",
          "models": ["deepseek-v4-flash", "deepseek-v4-pro"]},
    "3": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4/",
          "models": ["glm-5.2", "glm-4.6", "glm-4-flash"]},
    "4": {"name": "火山方舟（豆包）", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
          "models": ["doubao-1.5-pro-256k", "doubao-1.5-lite-32k", "doubao-seed-1.6"]},
    "5": {"name": "阿里百炼（通义千问）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
          "models": ["qwen3.7-plus", "qwen3.6-plus", "qwen-max"]},
    "6": {"name": "MiniMax", "base_url": "https://api.minimax.chat/v1",
          "models": ["MiniMax-Text-01", "MiniMax-M1"]},
    "7": {"name": "Moonshot（Kimi）", "base_url": "https://api.moonshot.cn/v1",
          "models": ["moonshot-v1-auto", "kimi-k2", "moonshot-v1-32k"]},
    "8": {"name": "OpenAI", "base_url": "https://api.openai.com/v1",
          "models": ["gpt-4.1", "gpt-4o", "o4-mini"]},
    "9": {"name": "Anthropic（Claude）", "base_url": "https://api.anthropic.com",
          "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"]},
    "A": {"name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1",
          "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-235B-A22B"]},
    "B": {"name": "OpenRouter（多模型聚合）", "base_url": "https://openrouter.ai/api/v1",
          "models": ["openai/gpt-4.1", "anthropic/claude-sonnet-4", "google/gemini-2.5-flash", "deepseek/deepseek-chat"]},
    "0": {"name": "自定义（OpenAI 兼容）", "base_url": "", "models": []},
}

# ════════════════════════════════════════════════════════════════
# 视觉识别供应商
# ════════════════════════════════════════════════════════════════
VISION_PROVIDERS = {
    "1": {"name": "OpenCode Go（推荐，mimo / qwen-vl）", "base_url": "https://opencode.ai/zen/go/v1",
          "models": ["mimo-v2.5", "kimi2.6", "minimax-m3", "qwen3.6-plus", "qwen3.7-plus"]},
    "2": {"name": "智谱 GLM（glm-4v）", "base_url": "https://open.bigmodel.cn/api/paas/v4/",
          "models": ["glm-4.6v", "glm-4v"]},
    "3": {"name": "火山方舟（豆包视觉）", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
          "models": ["doubao-1.5-vision-pro-32k"]},
    "4": {"name": "阿里百炼（qwen-vl）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
          "models": ["qwen-vl-max", "qwen3-vl-plus"]},
    "5": {"name": "OpenAI（gpt-4o）", "base_url": "https://api.openai.com/v1",
          "models": ["gpt-4o"]},
    "6": {"name": "TokenPlan（MiMo）", "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
          "models": ["mimo-v2.5"]},
    "0": {"name": "自定义", "base_url": "", "models": []},
}

DEFAULT_PORTS = {"napcat_http": 3000, "napcat_ws": 3001, "dashboard": 8899, "gateway": 18789,
                 "tts": 5000, "live2d": 19919}


# ════════════════════════════════════════════════════════════════
# 交互工具函数
# ════════════════════════════════════════════════════════════════

def ask(prompt, default="", required=False, secret=False):
    while True:
        suffix = f" [{default}]" if default else ""
        val = input(f"  {prompt}{suffix}: ").strip()
        if not val and default:
            return default
        if not val and required:
            print(red("    此项必填"))
            continue
        return val


def choose(prompt, options, default="1"):
    print(f"\n  {prompt}")
    for k, v in options.items():
        extra = dim(f"  ({v['base_url']})") if v.get("base_url") else ""
        print(f"    {k}. {v['name']}{extra}")
    print()
    while True:
        c = input(f"  选择 [{default}]: ").strip() or default
        if c in options:
            return c, options[c]
        print(red("    无效选项"))


def choose_model(models, default="1"):
    if not models:
        return ask("  模型名称", required=True)
    if len(models) == 1:
        print(f"  模型: {models[0]}")
        return models[0]
    print("  可用模型:")
    for i, m in enumerate(models, 1):
        print(f"    {i}. {m}")
    print()
    while True:
        try:
            c = input(f"  选择 [{default}]: ").strip() or default
            idx = int(c) - 1
            if 0 <= idx < len(models):
                return models[idx]
        except ValueError:
            pass
        print(red("    无效选项"))


def check_port(port):
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def auto_detect_ports():
    ports = {}
    for name, default in DEFAULT_PORTS.items():
        if check_port(default):
            ports[name] = default
        else:
            for p in range(default + 1, default + 100):
                if not check_port(p):
                    ports[name] = p
                    break
            else:
                ports[name] = default
    return ports


def auto_read_napcat_token():
    napcat_cfg = BOT_DIR / "napcat" / "napcat" / "config"
    if not napcat_cfg.exists():
        return None, None
    files = sorted(napcat_cfg.glob("onebot11_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:
        if f.stem == "onebot11":
            continue
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            for srv in data.get("network", {}).get("httpServers", []):
                token = srv.get("token", "").strip()
                if token:
                    return token, f.stem.replace("onebot11_", "")
        except Exception:
            continue
    return None, None


# ════════════════════════════════════════════════════════════════
# 配置生成
# ════════════════════════════════════════════════════════════════

def generate_config(llm_key, vision_key, anysearch_key,
                    gateway_token, owner_qq,
                    llm_url, llm_model, vision_url, vision_model, terminal_cwd):
    tpl = (TPL_DIR / "config-template.yaml").read_text(encoding="utf-8")
    for old, new in [
        ("{{DEEPSEEK_API_KEY}}", llm_key),
        ("{{MIMO_TOKEN}}", vision_key),
        ("{{ANYSEARCH_KEY}}", anysearch_key),
        ("{{GATEWAY_AUTH_TOKEN}}", gateway_token),
        ("{{OWNER_QQ}}", owner_qq),
        ("{{TERMINAL_CWD}}", terminal_cwd),
        ("{{LLM_MODEL}}", llm_model),
        ("{{LLM_BASE_URL}}", llm_url),
        ("{{VISION_BASE_URL}}", vision_url),
        ("{{VISION_MODEL}}", vision_model),
    ]:
        tpl = tpl.replace(old, new)
    return tpl


def generate_env(llm_key, vision_key, anysearch_key,
                 gateway_token, ports, knowledge_path, owner_qq):
    tpl = (TPL_DIR / ".env.template").read_text(encoding="utf-8")
    for old, new in [
        ("{{HERMES_HOME_PATH}}", str(HERMES_HOME)),
        ("{{OPENAI_API_KEY}}", llm_key),
        ("{{OPENROUTER_API_KEY}}", vision_key or ""),
        ("{{ONEBOT_ACCESS_TOKEN}}", ""),
        ("{{GATEWAY_AUTH_TOKEN}}", gateway_token),
        ("{{DEEPSEEK_API_KEY}}", llm_key),
        ("{{OWNER_QQ}}", owner_qq),
        ("{{MIMO_TOKEN}}", vision_key or ""),
        ("{{KNOWLEDGE_PATH}}", knowledge_path),
        ("{{BOT_ROOT}}", str(BOT_DIR)),
    ]:
        tpl = tpl.replace(old, new)

    # 追加语义判断需要的变量（不在模板中）
    judge_vars = (
        "\n# Semantic Judge (DEEPSEEK_BASE_URL + DEEPSEEK_MODEL)\n"
        f"DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1\n"
        "DEEPSEEK_MODEL=deepseek-v4-flash\n"
    )
    if "DEEPSEEK_BASE_URL" not in tpl:
        tpl += judge_vars
    return tpl


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════

def main():
    print()
    print(bold("  ╔═══════════════════════════════════════╗"))
    print(bold("  ║       QQBot Quick Setup               ║"))
    print(bold("  ╚═══════════════════════════════════════╝"))
    print()

    # ── 1. LLM 主模型 ──
    print(bold("  [1/4] LLM 主模型 — 聊天 & 语义判断"))
    llm_choice, llm_info = choose("选择 LLM 供应商:", LLM_PROVIDERS, default="2")
    llm_url = llm_info["base_url"]
    if llm_choice == "0":
        llm_url = ask("API 端点 (base_url)", required=True)
    llm_model = choose_model(llm_info["models"])
    llm_key = ask("API Key", required=True, secret=True)
    print(green(f"    -> {llm_info['name']} / {llm_model}"))
    print()

    # ── 2. 视觉识别 ──
    print(bold("  [2/4] 图片识别（可选）"))
    auto_vision = False
    vis_info = None
    vision_model = vision_url = vision_key = ""

    if llm_choice == "2":
        print(dim("    LLM 已选 OpenCode Go，视觉可直接复用"))
        reuse = ask("    视觉也用 OpenCode Go？(Y/n)", default="y").strip().lower()
        if reuse in ("", "y", "yes"):
            auto_vision = True

    if auto_vision:
        vision_key = llm_key
        vision_url = llm_url
        vis_info = VISION_PROVIDERS["1"]
        print(f"\n  供应商: {vis_info['name']}")
        print(dim("    API Key 自动复用 LLM"))
        vision_model = choose_model(vis_info["models"])
    else:
        vis_choice, vis_info = choose("选择视觉供应商:", VISION_PROVIDERS, default="1")
        vision_url = vis_info["base_url"]
        if vis_choice == "0":
            vision_url = ask("视觉 API 端点", required=True)
        vision_model = choose_model(vis_info["models"])
        if vision_url and llm_url and vision_url == llm_url:
            vision_key = llm_key
            print(dim("    视觉与 LLM 同端点 -> 复用 API Key"))
        else:
            vision_key = ask("视觉 API Key（可跳过）", default="", secret=True)

    if vision_model:
        print(green(f"    -> {vis_info['name']} / {vision_model}"))
    else:
        print(dim("    -> 已跳过"))
    print()

    # ── 3. 语音 ──
    print(bold("  [3/4] 语音合成（可选）"))
    print("    1. 内置（GPT-SoVITS，自行安装 tts-refs-pack）")
    print("    2. 跳过")
    tts_choice = ask("选择", default="2")
    print(green("    -> 内置 GPT-SoVITS") if tts_choice == "1" else dim("    -> 已跳过"))
    print()

    # ── 4. QQ 配置 ──
    print(bold("  [4/4] QQ 配置"))
    owner_qq = ask("管理员 QQ 号（主人，能执行指令）", required=True)
    terminal_cwd = str(BOT_DIR)
    knowledge_dir = str(HERMES_HOME / "knowledge")
    print(f"\n    知识库路径: {knowledge_dir} (自动)")
    print(dim("    QQ 群号 / 群名 -> NapCat 登录后自动获取"))
    print()

    # ── 网络搜索（可选）──
    print(dim("  [可选] 网络搜索"))
    anysearch_key = ask("AnySearch Key（可跳过）", default="", secret=True)
    print()

    # ── 自动生成 ──
    gateway_token = secrets.token_hex(24)
    print(dim("  检测端口..."))
    ports = auto_detect_ports()
    print(f"    NapCat: :{ports['napcat_http']} / :{ports['napcat_ws']}")
    print(f"    Dashboard: :{ports['dashboard']}  Gateway: :{ports['gateway']}")
    print()

    # ── 写配置 ──
    print(bold("  生成配置文件..."))
    HERMES_HOME.mkdir(parents=True, exist_ok=True)

    cfg = generate_config(llm_key, vision_key, anysearch_key,
                          gateway_token, owner_qq,
                          llm_url, llm_model, vision_url, vision_model, terminal_cwd)

    napcat_token, napcat_qq = auto_read_napcat_token()
    if napcat_token:
        cfg = cfg.replace(
            "access_token: ''  # 从 NapCat WebUI -> OneBot11 设置中获取，登录后回填",
            f"access_token: {napcat_token}")
        print(dim(f"    V 从 NapCat onebot11_{napcat_qq}.json 自动读取 access_token"))
    else:
        print(dim("    !! NapCat 未登录 - access_token 留空，启动前回填"))

    (HERMES_HOME / "config.yaml").write_text(cfg, encoding="utf-8")
    print(f"    V config.yaml")

    env = generate_env(llm_key, vision_key, anysearch_key,
                       gateway_token, ports, knowledge_dir, owner_qq)
    if napcat_token:
        env = env.replace("ONEBOT_ACCESS_TOKEN=", f"ONEBOT_ACCESS_TOKEN={napcat_token}")
    (HERMES_HOME / ".env").write_text(env, encoding="utf-8")
    print(f"    V .env")

    if not (HERMES_HOME / "SOUL.md").exists():
        print(f"    !! SOUL.md 未创建 - 参考 templates/SOUL-template.md")

    # ── 完成 ──
    print()
    print(green("  V 配置完成！"))
    print()
    print(f"  LLM:      {llm_info['name']} / {llm_model}")
    if vis_info and vision_model:
        vis_label = f"{vis_info['name']} / {vision_model}"
    else:
        vis_label = "跳过"
    print(f"  视觉:     {vis_label}")
    print(f"  语音:     {'内置 GPT-SoVITS' if tts_choice == '1' else '跳过'}")
    print(f"  搜索:     {'AnySearch' if anysearch_key else '未配置'}")
    print(f"  管理员:   {owner_qq}")
    print()
    print("  下一步:")
    if not napcat_token:
        print("    1. 启动 NapCat 扫码登录 -> WebUI 开端口")
        print("    2. 重新运行 配置API.bat（自动读 token）")
    print(f"    -> start.bat -> 浏览器打开 http://127.0.0.1:{ports['dashboard']}")
    print()


if __name__ == "__main__":
    main()
