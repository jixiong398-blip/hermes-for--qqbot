# Live2D Cubism 5 升级计划

> 目标：将 bot-template 的 Live2D 模块从 Cubism 2 升级到 Cubism 4/5，使用 Our Notes 新模型，支持在线下载
> **进度: Phase 1-5 ✅ 完成 | Phase 6 ⏳ 待测试 (2026-07-11)**

## 完成进度

| Phase | 内容 | 文件数 | 状态 |
|-------|------|--------|------|
| 1 | SDK 升级 (Cubism 2→4/5) | 3 新增 + 3 删除 | ✅ |
| 2 | 渲染器重写 (app.js ×2) | 2 文件 | ✅ |
| 3 | 模型下载 (main.js ×2 + conf.json ×2) | 4 文件 | ✅ |
| 4 | 构建脚本 (build-release.ps1, .iss, install.bat) | 3 文件 | ✅ |
| 5 | Dashboard 适配 (server.py ×2 + index.html ×2) | 4 文件 | ✅ |
| 6 | 集成测试 + 文档 | - | ⏳ |

## 现状

| 维度 | 当前 (OLD) | 目标 (NEW) |
|------|-----------|-----------|
| SDK | Cubism 2 (pixi-live2d-display cubism2) | Cubism 4/5 (pixi-live2d-display cubism4) |
| 模型格式 | `.moc` + `model.json` + `.mtn` | `.moc3` + `.model3.json` + `.motion3.json` |
| 模型来源 | ガルパ旧游戏 (11角色) | Our Notes 新游戏 (5+角色) |
| 模型打包 | 裸文件 + figures.zip | `.cvpkg` 加密包 → 需解包 |
| 模型分发 | 本地打包到安装包 | CDN 在线下载 |
| 桌面框架 | Electron 33 | Electron 33 (不变) |
| IPC 协议 | WS :9190 + HTTP :19919 | 保持兼容 (不变) |

## 约束

- ✅ Electron 框架不变
- ✅ IPC 协议保持兼容 (Hermes Gateway 无需改动)
- ✅ 表情/动作映射名保持兼容 (live2d_auto_controller.py 的 EXPRESSIONS/MOTIONS 字典)
- ✅ 现有 main.js 架构不变 (HTTP server + IPC 转发)
- ⚡ 新模型的表情/动作名可能与旧模型不同 → 需要映射表

---

## Phase 1: SDK 升级

**目标：替换渲染引擎，新 SDK 能加载 .moc3 模型**

### 1.1 更新依赖文件

```
modules/live2d/assets/lib/
├── live2dcubismcore.min.js    ← 新增: Cubism 5 Core 运行时
├── pixi-live2d-display-cubism4.min.js ← 新增: Cubism 4 显示插件
├── pixi.min.js                ← 保留: PixiJS v7
├── live2d.min.js              ← 删除: 旧 cubism2 库
└── pixi-live2d-display.min.js ← 删除: 旧通用版
```

### 1.2 更新 package.json

```json
// 不需要改，pixi-live2d-display@0.4.0 同时支持 cubism2/cubism4
// 只需要在 JS 中 import 不同的路径
```

### 1.3 更新 index.html

```html
<!-- 旧 -->
<script src="http://127.0.0.1:19919/assets/lib/live2dcubismcore.min.js"></script>
<script src="http://127.0.0.1:19919/assets/lib/live2d.min.js"></script>

<!-- 新 -->
<script src="http://127.0.0.1:19919/assets/lib/pixi.min.js"></script>
<script src="http://127.0.0.1:19919/assets/lib/live2dcubismcore.min.js"></script>
<script src="http://127.0.0.1:19919/assets/lib/pixi-live2d-display-cubism4.min.js"></script>
```

### 1.4 删除旧文件

```
删除:
  renderer/entry.js          ← cubism2 入口
  renderer/live2d-bundle.js  ← 旧 bundle
  assets/lib/live2d.min.js   ← 旧库
```

---

## Phase 2: 渲染器重写

**目标：用 Cubism 4 API 重写 app.js，参考 CucumberVPet 的 renderer.js**

### 2.1 新 renderer.js 结构

参考 `C:\Program Files\Cucumber VPet\Renderer\renderer.js` (309行)，精简版：

```javascript
// 核心功能:
// 1. PixiJS Application 初始化 (透明背景)
// 2. 模型加载: PIXI.live2d.Live2DModel.from(model3jsonUrl)
// 3. 表情控制: model.expression(name)
// 4. 动作控制: model.motion(group, index)
// 5. 视线追踪: setParameterValueById("ParamAngleX", ...)
// 6. 自适应缩放: resizeModel()
// 7. 鼠标交互: 拖拽 + 右键菜单
// 8. IPC 消息处理: 接收 WS 命令
```

### 2.2 关键 API 变化

| 功能 | Cubism 2 (旧) | Cubism 4 (新) |
|------|--------------|--------------|
| 加载模型 | `Live2DModel.from("model.json")` | `PIXI.live2d.Live2DModel.from(".model3.json")` |
| 播放表情 | `model.expression("smile01")` | `model.expression("smile01")` ← 相同 |
| 播放动作 | `model.motion("group", index)` | `model.motion("group", index)` ← 相同 |
| 设置参数 | `model.setParamFloat("PARAM", val)` | `model.internalModel.coreModel.setParameterValueById(id, val)` |

### 2.3 表情/动作映射表

Our Notes 模型的表情名可能与ガルパ不同，需要建立映射：

```
// 旧模型表情名 → 新模型表情名 (需要实际测试后填写)
const EXPRESSION_MAP = {
  "smile01": "exp_smile_01",    // ← 示例，需实测
  "angry01": "exp_angry_01",
  // ...
};
```

---

## Phase 3: 模型格式支持

**目标：支持 .cvpkg 解包 + .moc3 加载**

### 3.1 .cvpkg 格式分析

```
Header: "CVPKG1" (6 bytes)
Body: 加密/压缩数据

需要逆向:
  - 加密算法 (可能是 XOR/AES)
  - 压缩算法 (可能是 ZIP/DEFLATE)
  - 内部文件结构 (.moc3 + .model3.json + textures + motions)
```

### 3.2 方案选择

**方案 A (推荐): 直接使用原始模型文件**
- 用户说网上有原始模型文件
- 跳过 .cvpkg 解包，直接下载 .moc3 + .model3.json + textures
- 优势: 简单、可靠、不依赖逆向
- 风险: 需要找到模型下载源

**方案 B: 逆向 .cvpkg**
- 从 CucumberVPet WPF DLL 反编译解密逻辑
- 优势: 可以复用 CucumberVPet 的 CDN 和模型包
- 风险: 可能违反使用条款，且加密可能变化

### 3.3 模型目录结构 (新)

```
assets/figure/
├── mutsumi/
│   ├── .model3.json
│   ├── .moc3
│   ├── textures/
│   │   └── texture_00.png
│   ├── motions/
│   │   ├── idle.motion3.json
│   │   ├── tap.motion3.json
│   │   └── ...
│   └── expressions/
│       └── exp_00.exp3.json
├── taki/
├── tomori/
├── umiri/
└── ...
```

---

## Phase 4: 在线模型下载

**目标：从 CDN 下载模型，用户无需手动放置模型文件**

### 4.1 下载流程

```
main.js (Electron 主进程)
  ├── 启动时检查 models/ 目录
  ├── 如果没有模型 → 从 CDN 清单获取可用模型列表
  ├── 用户选择角色 → 下载 .zip/.cvpkg
  ├── 解包到本地 models/ 目录
  └── 通知 renderer 加载新模型
```

### 4.2 新增文件

```
modules/live2d/
├── src/
│   ├── downloader.js    ← 模型下载 (HTTP download + progress)
│   └── unpacker.js      ← .cvpkg 解包 (如果走方案B)
```

### 4.3 CDN 配置

```json
// conf.json 新增
{
  "modelRegistry": "https://models.example.com/manifest.json",
  "modelsDir": "models/"
}
```

---

## Phase 5: IPC 协议兼容

**目标：确保 Hermes Gateway 发送的命令在新渲染器中正常工作**

### 5.1 现有协议 (不变)

```
Hermes Gateway → WS :9190 → Live2D Electron:
  { type: "state", state: "idle"|"thinking"|"tool_call"|"speaking"|"replying" }
  { type: "emotion", emotion: "happy"|"sad"|"angry"|... }

外部工具 → HTTP :19919 /cmd → Electron IPC → renderer:
  { type: "expression", name: "exp_00" }
  { type: "motion", group: "tap", index: 0 }
  { type: "swap_character", character: "mutsumi", costume: "casual" }
```

### 5.2 兼容性保障

- WS 连接逻辑不变 (app.js 中已有 WS 客户端)
- HTTP /cmd 转发逻辑不变 (main.js 中已有)
- 只需要在新 renderer 中实现相同的消息处理函数

---

## Phase 6: 集成测试

### 6.1 测试清单

- [ ] PixiJS + Cubism 4 SDK 能正常加载 .moc3 模型
- [ ] 表情切换正常 (expression)
- [ ] 动作播放正常 (motion)
- [ ] 视线跟随鼠标 (look tracking)
- [ ] 模型缩放/位置适配 (resize)
- [ ] WS :9190 连接正常，接收 Hermes 命令
- [ ] HTTP :19919 /cmd 接收外部命令
- [ ] 角色切换 (swap_character)
- [ ] 模型下载功能
- [ ] 与 Hermes Gateway 联调 (状态同步 + 情绪触发)

### 6.2 回归测试

- [ ] 旧模型 (ガルパ) 仍可加载 (向后兼容)
- [ ] Dashboard 启停 Live2D 正常
- [ ] start.bat 一键启动正常

---

## 文件变更清单

```
修改:
  modules/live2d/package.json          ← 依赖更新 (如需要)
  modules/live2d/renderer/index.html   ← SDK 引用更新
  modules/live2d/renderer/app.js       ← 重写为 Cubism 4 API
  modules/live2d/main.js               ← 新增模型下载 + .cvpkg 处理
  modules/live2d/conf.json             ← 新增 CDN 配置

新增:
  modules/live2d/assets/lib/live2dcubismcore.min.js    ← Cubism 5 Core
  modules/live2d/assets/lib/pixi-live2d-display-cubism4.min.js ← Cubism 4 display
  modules/live2d/src/downloader.js     ← 模型下载器
  modules/live2d/src/unpacker.js       ← .cvpkg 解包器 (可选)

删除:
  modules/live2d/renderer/entry.js     ← cubism2 入口
  modules/live2d/renderer/live2d-bundle.js ← 旧 bundle
  modules/live2d/assets/lib/live2d.min.js  ← 旧 cubism2 库
```

---

## 风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| Our Notes 模型表情名与旧映射不兼容 | 高 | 建立映射表，运行时自动适配 |
| .cvpkg 加密无法破解 | 中 | 优先使用原始模型文件，.cvpkg 作为备选 |
| pixi-live2d-display cubism4 与 Cubism 5 .moc3 不兼容 | 低 | Cubism 4 SDK 向后兼容 5.x 模型 |
| 新模型体积大，下载慢 | 中 | 支持断点续传 + 进度显示 |
| 模型版权问题 | 中 | 仅用于个人学习，不随分发版发布 |