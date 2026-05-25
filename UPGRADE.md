# UPGRADE.md — QQBot 版本升级指南（AI 代理用）

> 最后更新: 2026-05-26 | v0.5.2+

## 概述

此文件供 AI 编程代理（Sisyphus / Claude / Cursor 等）在升级 QQBot 版本时使用。

## 升级流程

```
1. 从 GitHub Release 下载最新 zip
2. 解压到临时目录
3. 运行升级脚本覆盖文件
4. 运行 npm install 更新 Live2D
5. 隐私检查
```

## 文件覆盖清单

以下文件**直接覆盖**（不含用户配置）：

### Hermes 核心
```
hermes/plugins/platforms/onebot/adapter.py
hermes/gateway/platforms/onebot/adapter.py
hermes/plugins/knowledge-base/__init__.py
hermes/plugins/knowledge-base/knowledge_base_tool.py
hermes/agent/memory/gateway.py
hermes/agent/memory/obsidian.py
hermes/tools/memory_gateway_tool.py
hermes/requirements.txt
```

### Dashboard
```
modules/dashboard/server.py
modules/dashboard/static/index.html
```

### 脚本
```
scripts/install.py
scripts/setup_config.py
scripts/fix_napcat.py
scripts/upgrade.py
```

### 模板（覆盖，用户通过 PeiZhiAPI.bat 重新生成配置）
```
templates/config-template.yaml
templates/SOUL-template.md
templates/.env.template
templates/napcat/onebot11.json
templates/napcat/napcat.json
```

### .bat 启动脚本
```
install.bat
PeiZhiAPI.bat
FixNapCat.bat
start.bat
setup.bat
```

### Live2D 依赖
```
node/                              ← 完整 Node.js portable
modules/live2d/node_modules/       ← npm install 后生成
modules/tts/ts_adapter_template.py
```

## 文件**禁止覆盖**（用户配置）
```
config.yaml      ← API keys, 群号
SOUL.md          ← 角色人设
.env             ← 环境变量
```

## 升级后检查

```python
# 1. 语法检查
import py_compile
py_compile.compile("hermes/plugins/platforms/onebot/adapter.py", doraise=True)

# 2. 隐私扫描（确认无硬编码 QQ/Token/APIKey）
import re, os
for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith((".py",".md",".yaml",".json")):
            text = open(os.path.join(root,f), encoding="utf-8").read()
            for pattern in ["3560998016", "cTDW~Sv_EAjgJ0kF", "sk-CfBJRQ8u",
                           "清尘璃落", "jixiong233", "2910137276",
                           "/home/ji/", "E:/ai/"]:
                if pattern in text and f != "AGENTS.md":
                    print(f"LEAK: {f} - {pattern}")

# 3. Live2D 依赖
# cd modules/live2d && ..\..\node\npm.cmd install
```

## 版本号更新
```
VERSION            → 写入新版本号
hermes/VERSION     → 写入新版本号
AGENTS.md          → 更新日期和版本
CHANGELOG.md       → 添加新版本条目
README.md          → 更新安装流程（如有变更）
```
