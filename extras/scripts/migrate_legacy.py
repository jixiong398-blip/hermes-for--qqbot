#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""migrate_legacy.py — 旧版平铺结构一键迁移工具（v0.14.2 及更早 → v0.14.3+ core 结构）

用法:
    python extras/scripts/migrate_legacy.py [src_dir] [dst_dir] [--dry-run]

    src_dir  新版模板源码目录（update.bat 下载解压的 zip 目录）
             默认: 本脚本所在仓库根目录（智能检测，含 install.bat 的目录）
    dst_dir  用户现有安装目录（install.bat/update.bat 所在目录，即旧版代码所在地）
             默认: 当前工作目录
    --dry-run  只检测 + 打印迁移计划，不执行任何写操作

重要设计原则:
    * 本项目是开源分发的，用户"解压到哪就安装在哪"，没有固定安装位置。
      因此 src/dst 一律显式解析，绝不假设 C 盘或任何硬编码路径。
    * 运行数据（config.yaml/SOUL.md/.env/*.db/sessions）在 ~/.hermes，
      本脚本完全不动它，只迁移 hermes\ 代码目录。
    * 备份永远自动做；删除旧文件必须用户手动确认（Y/N）。

迁移流程:
    1. 检测旧结构: dst\hermes\gateway 存在 且 dst\hermes\core 不存在
    2. 自动备份:   dst\hermes  ->  dst\hermes.bak.<时间戳>   （回滚点，含用户本地修复）
    3. 询问确认:   是否删除旧平铺文件（Y/N）；选 N 则中止，备份保留
    4. 清理旧文件:  删除 dst\hermes 下旧平铺代码（已备份）
    5. 复制新结构:  src\hermes\core + docs + templates  ->  dst\hermes
    6. 重建环境:   pip uninstall hermes-agent; pip install -e hermes\core;
                   pip install -r hermes\core\requirements.txt（含 CVE 修复）
    7. 同步运行:   调用 src\extras\scripts\upgrade.py 双写 ~/.hermes（BOT_DIR bug 已修）
    8. 移植清单:   diff 备份 vs 新代码 -> 列出用户本地修改过的文件；
                   官方 v0.14.6 已内置的修复自动标注为"无需移植"
    9. 配置检查:   config.yaml 是否含 context_length: 1000000（缺失给提示）
"""
import os
import re
import shutil
import sys
import time
from pathlib import Path


def find_bot_root(start: Path) -> Path:
    """从 start 向上找包含 install.bat 的目录（bot-template 根）。"""
    p = start.resolve()
    for _ in range(5):
        if (p / "install.bat").exists():
            return p
        p = p.parent
    return start.resolve()


def detect_legacy(dst: Path) -> bool:
    """旧结构 = hermes/gateway 存在 且 hermes/core 不存在。"""
    hermes = dst / "hermes"
    return (hermes / "gateway").exists() and not (hermes / "core").exists()


def make_backup(dst: Path, dry_run: bool) -> Path:
    """自动备份 dst/hermes -> dst/hermes.bak.<YYYYMMDD-HHMMSS>，返回备份路径。"""
    hermes = dst / "hermes"
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = dst / f"hermes.bak.{ts}"
    if dry_run:
        print(f"  [计划] 备份: {hermes}  ->  {bak}")
        return bak
    shutil.copytree(hermes, bak)
    print(f"  [OK] 已备份: {hermes}  ->  {bak}")
    return bak


def confirm_remove(dry_run: bool) -> bool:
    """询问用户是否删除旧平铺文件。选 N 中止迁移（备份保留）。"""
    if dry_run:
        return True
    try:
        ans = input("  删除旧的平铺代码文件 (hermes\\gateway 等)? [y/N]: ").strip().lower()
    except EOFError:
        ans = "n"
    if ans in ("y", "yes"):
        return True
    print("  [中止] 未删除旧文件，迁移停止。")
    print("  备份已保留，可随时重跑本脚本；或手动处理旧目录后重试。")
    return False


# 旧平铺结构下的根级 Python 文件（v0.14.2- 时代 hermes\ 下的散落文件）
LEGACY_ROOT_FILES = [
    "run_agent.py", "toolsets.py", "model_tools.py", "utils.py",
    "gateway_runner.py", "batch_runner.py", "cli.py", "mcp_serve.py",
    "hermes_bootstrap.py", "hermes_constants.py", "hermes_logging.py",
    "hermes_state.py", "hermes_time.py", "corpus_history.py",
    "trajectory_compressor.py", "toolset_distributions.py",
    "rl_cli.py", "mini_swe_runner.py", "requirements.txt",
    "pyproject.toml", "setup.py",
]

# 旧平铺结构下的顶层目录（删除时整个移除，备份已覆盖）
LEGACY_ROOT_DIRS = [
    "gateway", "tools", "plugins", "agent", "environments", "skills",
    "hermes_cli", "platforms", "scripts", "providers", "sandboxes",
    "ui-tui", "web", "assets", "bin", "cron", "docs", "tests",
    "acp_adapter", "acp_registry", "datagen-config-examples", "docker",
    "nix", "optional-skills", "packaging", "plans", "tinker-atropos",
    "locales", "tui_gateway", "website", "__pycache__",
]

# 官方 v0.14.6 已内置、用户报告声称"需移植"的修复（自动标注为无需移植）
OFFICIAL_COVERED = {
    "semantic_judge.py": "官方已有 _parse_judge_json 容错解析 (semantic_judge.py:461)",
    "adapter.py": "官方已有 _is_mentioned CQ 回退 + _self_id 预载 (adapter.py:842/207)",
    "send_message_tool.py": "官方已有失败路径诊断日志 (send_message_tool.py)",
    "run.py": "官方已有 _gateway_runner_ref (weakref 实现)",
}


def remove_legacy(dst: Path, dry_run: bool) -> None:
    """删除 dst/hermes 下旧平铺内容（仅在用户确认后调用）。"""
    hermes = dst / "hermes"
    removed = []
    for d in LEGACY_ROOT_DIRS:
        p = hermes / d
        if p.exists():
            removed.append(str(p))
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)
    for f in LEGACY_ROOT_FILES:
        p = hermes / f
        if p.exists():
            removed.append(str(p))
            if not dry_run:
                try:
                    p.unlink()
                except OSError:
                    pass
    if dry_run:
        print(f"  [计划] 将删除 {len(removed)} 个旧文件/目录:")
        for r in removed[:15]:
            print(f"         {r}")
        if len(removed) > 15:
            print(f"         ... 等共 {len(removed)} 项")
    else:
        print(f"  [OK] 已删除 {len(removed)} 个旧文件/目录")


def copy_new(src: Path, dst: Path, dry_run: bool) -> None:
    """复制 src/hermes/core + docs + templates -> dst/hermes。"""
    hermes_src = src / "hermes"
    hermes_dst = dst / "hermes"
    hermes_dst.mkdir(parents=True, exist_ok=True)
    for sub in ("core", "docs", "templates"):
        s = hermes_src / sub
        if not s.exists():
            print(f"  [跳过] 源目录不存在: {s}")
            continue
        t = hermes_dst / sub
        if dry_run:
            print(f"  [计划] 复制: {s}  ->  {t}")
            continue
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(s, t)
        print(f"  [OK] 复制: {s}  ->  {t}")


def rebuild_venv(dst: Path, dry_run: bool) -> None:
    """重建 venv 的 editable mapping：uninstall 旧映射 -> install -e hermes/core。
    保留 .venv 依赖（不整删），只修正指向 core 的映射并升级依赖。"""
    venv = dst / ".venv"
    py = venv / "Scripts" / "python.exe"
    pip = venv / "Scripts" / "pip.exe"
    if not py.exists():
        print("  [跳过] 未找到 .venv，跳过环境重建（可稍后手动 install.bat）")
        return
    core = dst / "hermes" / "core"
    cmds = [
        (pip, ["uninstall", "-y", "hermes-agent"], "卸载旧 editable 映射（失败可忽略）"),
        (pip, ["install", "-e", str(core), "--no-deps"], "重建 editable 映射 -> hermes/core"),
        (pip, ["install", "-r", str(core / "requirements.txt")], "安装/升级依赖（含 requests>=2.33.0 CVE 修复）"),
    ]
    for exe, args, label in cmds:
        if dry_run:
            print(f"  [计划] {label}: {exe} {' '.join(args)}")
            continue
        print(f"  [执行] {label} ...")
        import subprocess
        r = subprocess.run([str(exe)] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 and "uninstall" not in args:
            print(f"  [警告] 命令失败（exit {r.returncode}）: {exe} {' '.join(args)}")
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-5:]
            for ln in tail:
                print(f"         {ln}")
        elif "uninstall" in args:
            # uninstall 未安装也正常，忽略
            pass


def run_upgrade(src: Path, dst: Path, dry_run: bool) -> None:
    """调用 src 的 upgrade.py 同步 ~/.hermes（upgrade.py BOT_DIR 已修复）。"""
    script = src / "extras" / "scripts" / "upgrade.py"
    if not script.exists():
        print("  [跳过] 未找到 upgrade.py（预期 src\\extras\\scripts\\upgrade.py）")
        return
    if dry_run:
        print(f"  [计划] 运行: python {script} {src}")
        return
    print(f"  [执行] 同步 ~/.hermes ...")
    import subprocess
    r = subprocess.run(
        [sys.executable, str(script), str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print(f"  [警告] upgrade.py exit {r.returncode}")
    for ln in (r.stdout or "").strip().splitlines()[-8:]:
        print(f"         {ln}")


def diff_port_list(bak: Path, src: Path, dst: Path, dry_run: bool) -> None:
    """对比备份 vs 新 core，列出用户本地修改过的文件。
    备份是旧平铺结构（hermes/gateway/X），新代码在 hermes/core/gateway/X：
    相对路径一致，只是多了 core/ 前缀。"""
    core_src = src / "hermes" / "core"
    print("\n  === 本地修改文件清单（对比 hermes.bak.* 与官方新代码） ===")
    if not bak.exists():
        print("  （无备份可对比）")
        return
    modified = []
    for old in (bak / "hermes").rglob("*"):
        if not old.is_file():
            continue
        rel = old.relative_to(bak / "hermes")
        new = core_src / rel
        if new.exists():
            try:
                if old.read_bytes() != new.read_bytes():
                    modified.append(rel)
            except OSError:
                pass
    if not modified:
        print("  未发现本地修改（你的本地修复官方已全部覆盖）")
        return
    for rel in sorted(modified):
        name = rel.name
        if name in OFFICIAL_COVERED:
            print(f"  [官方已覆盖] {rel}  -- {OFFICIAL_COVERED[name]}")
        else:
            print(f"  [需人工核对] {rel}")
    print("\n  提示: 官方 v0.14.4-14.6 已内置 @强信号修复、DB 锁根治、@全体信号；")
    print("         '@ 绕过 judge' 旧补丁请放弃（官方设计是走 judge）。")


def check_config(dst: Path, dry_run: bool) -> None:
    """检查 config.yaml 是否含 context_length: 1000000。"""
    candidates = [
        Path.home() / ".hermes" / "config.yaml",
        dst / "config.yaml",
    ]
    for cfg in candidates:
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not re.search(r"context_length\s*[:=]\s*1000000", text):
                print(f"\n  [提示] {cfg} 缺少 context_length: 1000000")
                print("         建议在 model: 段添加（context 探测链已关闭，需显式配置）")
            else:
                print(f"\n  [OK] {cfg} 已含 context_length: 1000000")
            return
    print("\n  [提示] 未找到 config.yaml（安装时自动生成，或已迁移到 ~/.hermes）")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv[1:]

    # src: 新版源码目录；dst: 用户安装目录 —— 均显式解析，绝不硬编码
    script_dir = Path(__file__).resolve().parent
    default_src = find_bot_root(script_dir)
    src = Path(args[0]).resolve() if len(args) > 0 else default_src
    dst = Path(args[1]).resolve() if len(args) > 1 else Path.cwd().resolve()

    print("=" * 60)
    print("  旧版 -> core 结构 迁移工具")
    print("=" * 60)
    mode = " [DRY-RUN 预演]" if dry_run else ""
    print(f"  源目录 (新版) : {src}{mode}")
    print(f"  目标目录(旧版): {dst}")
    print()

    if src == dst:
        print("  [错误] src 与 dst 相同。请用 update.bat 流程（src=解压目录, dst=安装目录）。")
        return 1
    if not (src / "hermes" / "core").exists():
        print(f"  [错误] 源目录没有新版结构 hermes\\core: {src}")
        return 1
    if not detect_legacy(dst):
        print("  [跳过] 目标目录不是旧版平铺结构（hermes\\gateway 不存在或已含 core）。")
        print("         直接运行 update.bat 即可正常更新。")
        return 0

    print("  检测到旧版平铺结构，开始迁移计划...\n")

    # 1. 备份（自动）
    print("  [1/6] 自动备份旧代码（回滚点）")
    bak = make_backup(dst, dry_run)

    # 2. 确认删除
    print("\n  [2/6] 清理旧平铺代码（需要确认）")
    if not confirm_remove(dry_run):
        return 2

    # 3. 删除
    print("\n  [3/6] 删除旧平铺文件")
    remove_legacy(dst, dry_run)

    # 4. 复制新结构
    print("\n  [4/6] 复制新版 core 结构")
    copy_new(src, dst, dry_run)

    # 5. 重建 venv + 同步 ~/.hermes
    print("\n  [5/6] 重建 Python 环境")
    rebuild_venv(dst, dry_run)
    print("\n  [6/6] 同步 ~/.hermes")
    run_upgrade(src, dst, dry_run)

    # 7. 移植清单 + 配置检查
    diff_port_list(bak, src, dst, dry_run)
    check_config(dst, dry_run)

    if dry_run:
        print("\n  [DRY-RUN] 以上为执行计划，未做任何修改。")
        print("  确认无误后去掉 --dry-run 重新运行。")
    else:
        print("\n  ============================================")
        print("  迁移完成！")
        print("  回滚点: " + str(bak))
        print("  旧目录确认无误后，可手动删除: " + str(bak))
        print("  ============================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
