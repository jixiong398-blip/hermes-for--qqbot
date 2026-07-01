# 升级指南

> v0.9.0

## 如何升级

已有旧版本（v0.5.x / v0.6.x / v0.7.x）？按以下步骤升级到最新版：

### 方法一：下载最新版覆盖（推荐）

```
1. 从 GitHub Releases 下载最新 bot-template.zip
2. 解压到新目录
3. 将旧版的配置文件搬过来（config.yaml, SOUL.md, .env）
4. 复制到新目录对应位置
5. 运行 install.bat（会跳过已安装的环境）
6. 运行 start.bat 启动
```

### 方法二：直接覆盖（不推荐，可能残留旧文件）

```
1. 下载最新 bot-template.zip
2. 解压后，将所有文件覆盖到你的 bot 目录
3. ⚠️ 不要覆盖 config.yaml、SOUL.md、.env ！
4. 运行 .venv\Scripts\python scripts\upgrade.py
5. 重启 Gateway
```

---

## 配置文件搬迁清单

**必须保留（你的个人配置）：**

| 文件 | 内容 |
|------|------|
| `config.yaml` | LLM 模型配置、群号、端口 |
| `SOUL.md` | 角色人设 |
| `.env` | API Key、Bot QQ、Admin QQ |

这些文件在新版本中不会被覆盖。

**模板文件会自动更新：**

| 文件 | 说明 |
|------|------|
| `templates/config-template.yaml` | 配置模板（新增选项） |
| `templates/SOUL-template.md` | 人设模板 |
| `templates/.env.template` | 环境变量模板 |

如需使用新版模板，重新运行 `配置API.bat` 即可。

---

## v0.9.0 主要变更

1. **配置外提**：QQ号、API Key、路径等不再硬编码，改为环境变量
2. **记忆系统升级**：LLM 蒸馏提取 + 1 天半衰期
3. **离线安装优先**：install.bat 检测已有环境自动跳过
4. **NapCat 登录独立**：扫码登录后运行 FixNapCat.bat 开端口
5. **新增工具**：表情包管理、记忆监控、晚间简报

---

## 常见问题

**Q: 升级后 bot 不回复？**
A: 检查 `.env` 中的 `ONEBOT_SELF_ID` 是否已设置为你 bot 的 QQ 号。

**Q: 升级后 Live2D 不显示？**
A: `electron-offline.zip` 未包含时需联网安装，运行 `cd modules\live2d && ..\..\node\npm.cmd install`。

**Q: 想保留旧版配置？**
A: 方法一创建新目录，把旧版的 config.yaml / SOUL.md / .env 复制过去即可。
