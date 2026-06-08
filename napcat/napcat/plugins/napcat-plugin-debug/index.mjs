import fs from 'fs';

const DEFAULT_CONFIG = {
  port: 8998,
  host: "127.0.0.1",
  enableAuth: false,
  authToken: ""
};

class PluginState {
  _ctx = null;
  _config = { ...DEFAULT_CONFIG };
  _logger = null;
  get ctx() {
    return this._ctx;
  }
  get config() {
    return this._config;
  }
  get logger() {
    return this._logger;
  }
  init(ctx) {
    this._ctx = ctx;
    this._logger = ctx.logger;
    this.loadConfig(ctx.configPath);
  }
  cleanup() {
    this._ctx = null;
    this._logger = null;
  }
  replaceConfig(config) {
    this._config = { ...DEFAULT_CONFIG, ...config };
    this.saveConfig();
  }
  loadConfig(configPath) {
    try {
      if (fs.existsSync(configPath)) {
        const raw = JSON.parse(fs.readFileSync(configPath, "utf-8"));
        this._config = { ...DEFAULT_CONFIG, ...raw };
      }
    } catch {
      this._logger?.warn("配置加载失败，使用默认值");
    }
  }
  saveConfig() {
    if (!this._ctx) return;
    try {
      const dir = this._ctx.configPath.replace(/[/\\][^/\\]+$/, "");
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(this._ctx.configPath, JSON.stringify(this._config, null, 2));
    } catch {
      this._logger?.warn("配置保存失败");
    }
  }
}
const pluginState = new PluginState();

class DebugServer {
  wss = null;
  clients = /* @__PURE__ */ new Set();
  ctx;
  config;
  constructor(ctx, config) {
    this.ctx = ctx;
    this.config = config;
  }
  async start() {
    process.env.WS_NO_BUFFER_UTIL = "1";
    process.env.WS_NO_UTF_8_VALIDATE = "1";
    const { WebSocketServer } = await import('ws');
    this.wss = new WebSocketServer({
      port: this.config.port,
      host: this.config.host
    });
    this.wss.on("connection", (ws, req) => {
      if (this.config.enableAuth && this.config.authToken) {
        const url = new URL(req.url || "/", `http://${req.headers.host}`);
        const token = url.searchParams.get("token") || req.headers["authorization"]?.replace("Bearer ", "");
        if (token !== this.config.authToken) {
          ws.close(4001, "Unauthorized");
          this.ctx.logger.warn("CLI 连接被拒绝：token 无效");
          return;
        }
      }
      this.ctx.logger.info(`CLI 客户端已连接: ${req.socket.remoteAddress}`);
      this.clients.add(ws);
      ws.on("message", async (raw) => {
        try {
          const msg = JSON.parse(raw.toString());
          if (msg.jsonrpc === "2.0" && msg.method) {
            const response = await this.handleRpc(msg);
            ws.send(JSON.stringify(response));
          }
        } catch {
        }
      });
      ws.on("close", () => {
        this.clients.delete(ws);
        this.ctx.logger.info("CLI 客户端已断开");
      });
      ws.on("error", (err) => {
        this.ctx.logger.error("WebSocket 错误:", err.message);
        this.clients.delete(ws);
      });
      ws.send(JSON.stringify({
        jsonrpc: "2.0",
        method: "welcome",
        params: {
          version: "1.0.0",
          pluginCount: this.ctx.pluginManager.getAllPlugins().length
        }
      }));
    });
    this.wss.on("error", (err) => {
      this.ctx.logger.error("WS 服务器错误:", err.message);
    });
    this.ctx.logger.info(`调试服务已启动: ws://${this.config.host}:${this.config.port}`);
  }
  async stop() {
    if (!this.wss) return;
    for (const c of this.clients) {
      try {
        c.close(1e3, "Server stopping");
      } catch {
      }
    }
    this.clients.clear();
    await new Promise((r) => this.wss.close(() => r()));
    this.wss = null;
    this.ctx.logger.info("调试服务已停止");
  }
  broadcastEvent(event) {
    const msg = JSON.stringify({
      jsonrpc: "2.0",
      method: "event",
      params: event
    });
    for (const c of this.clients) {
      try {
        if (c.readyState === 1) c.send(msg);
      } catch {
      }
    }
  }
  // ==================== 自身插件 ID ====================
  static SELF_PLUGIN_ID = "napcat-plugin-debug";
  // ==================== JSON-RPC 方法路由 ====================
  async handleRpc(req) {
    const pm = this.ctx.pluginManager;
    const params = req.params || [];
    try {
      let result;
      switch (req.method) {
        case "ping":
          result = "pong";
          break;
        case "getDebugInfo":
          result = {
            version: "1.0.0",
            pluginCount: pm.getAllPlugins().length,
            loadedCount: pm.getLoadedPlugins().length,
            pluginPath: pm.getPluginPath(),
            uptime: process.uptime()
          };
          break;
        case "getPluginPath":
          result = pm.getPluginPath();
          break;
        case "getAllPlugins":
          result = pm.getAllPlugins().map((e) => this.serializeEntry(e));
          break;
        case "getLoadedPlugins":
          result = pm.getLoadedPlugins().map((e) => ({
            id: e.id,
            name: e.name,
            version: e.version,
            loaded: e.loaded
          }));
          break;
        case "getPluginInfo":
          const info = pm.getPluginInfo(params[0]);
          result = info ? this.serializeEntry(info) : null;
          break;
        case "setPluginStatus":
          await pm.setPluginStatus(params[0], params[1]);
          result = true;
          break;
        case "loadPluginById":
          result = await pm.loadPluginById(params[0]);
          break;
        case "unregisterPlugin": {
          const targetId = params[0];
          if (targetId === DebugServer.SELF_PLUGIN_ID) {
            return {
              jsonrpc: "2.0",
              id: req.id,
              error: { code: -32001, message: "不能通过调试服务卸载自身 (napcat-plugin-debug)" }
            };
          }
          await pm.unregisterPlugin(targetId);
          result = true;
          break;
        }
        case "reloadPlugin": {
          const targetId = params[0];
          if (targetId === DebugServer.SELF_PLUGIN_ID) {
            return {
              jsonrpc: "2.0",
              id: req.id,
              error: { code: -32001, message: "不能通过调试服务重载自身 (napcat-plugin-debug)" }
            };
          }
          result = await pm.reloadPlugin(targetId);
          break;
        }
        case "scanPlugins": {
          return {
            jsonrpc: "2.0",
            id: req.id,
            error: { code: -32601, message: "scanPlugins 已弃用，请使用 getAllPlugins 获取插件列表" }
          };
        }
        case "loadDirectoryPlugin":
          await pm.loadDirectoryPlugin(params[0]);
          result = true;
          break;
        case "uninstallPlugin": {
          const targetId = params[0];
          if (targetId === DebugServer.SELF_PLUGIN_ID) {
            return {
              jsonrpc: "2.0",
              id: req.id,
              error: { code: -32001, message: "不能通过调试服务卸载自身 (napcat-plugin-debug)" }
            };
          }
          await pm.uninstallPlugin(targetId, params[1]);
          result = true;
          break;
        }
        case "getPluginDataPath":
          result = pm.getPluginDataPath(params[0]);
          break;
        case "getPluginConfigPath":
          result = pm.getPluginConfigPath(params[0]);
          break;
        case "getPluginConfig":
          result = pm.getPluginConfig();
          break;
        // ==================== 远程文件传输 ====================
        case "writeFiles": {
          const files = params[0];
          if (!Array.isArray(files)) {
            return { jsonrpc: "2.0", id: req.id, error: { code: -32602, message: "params[0] must be an array of file entries" } };
          }
          const fsModule = await import('fs');
          const nodePath = await import('path');
          const pluginPath = nodePath.resolve(pm.getPluginPath());
          let written = 0;
          for (const f of files) {
            const targetPath = nodePath.resolve(nodePath.join(pluginPath, f.path));
            if (!targetPath.toLowerCase().startsWith(pluginPath.toLowerCase())) {
              return { jsonrpc: "2.0", id: req.id, error: { code: -32001, message: `路径越界: ${f.path}` } };
            }
            const dir = nodePath.dirname(targetPath);
            if (!fsModule.existsSync(dir)) fsModule.mkdirSync(dir, { recursive: true });
            const buf = f.encoding === "base64" ? Buffer.from(f.content, "base64") : f.content;
            fsModule.writeFileSync(targetPath, buf);
            written++;
          }
          result = { written };
          break;
        }
        case "removeDir": {
          const relPath = params[0];
          if (!relPath || typeof relPath !== "string") {
            return { jsonrpc: "2.0", id: req.id, error: { code: -32602, message: "params[0] must be a relative path string" } };
          }
          const fsModule2 = await import('fs');
          const nodePath2 = await import('path');
          const pluginPath2 = nodePath2.resolve(pm.getPluginPath());
          const targetDir = nodePath2.resolve(nodePath2.join(pluginPath2, relPath));
          if (!targetDir.toLowerCase().startsWith(pluginPath2.toLowerCase())) {
            return { jsonrpc: "2.0", id: req.id, error: { code: -32001, message: `路径越界: ${relPath}` } };
          }
          if (fsModule2.existsSync(targetDir)) {
            fsModule2.rmSync(targetDir, { recursive: true, force: true });
          }
          result = true;
          break;
        }
        default:
          return {
            jsonrpc: "2.0",
            id: req.id,
            error: { code: -32601, message: `Method not found: ${req.method}` }
          };
      }
      return { jsonrpc: "2.0", id: req.id, result };
    } catch (err) {
      return {
        jsonrpc: "2.0",
        id: req.id,
        error: { code: -32e3, message: err.message || String(err) }
      };
    }
  }
  serializeEntry(e) {
    return {
      id: e.id,
      fileId: e.fileId,
      name: e.name,
      version: e.version,
      description: e.description,
      author: e.author,
      pluginPath: e.pluginPath,
      entryPath: e.entryPath,
      enable: e.enable,
      loaded: e.loaded,
      runtimeStatus: e.runtime?.status,
      runtimeError: e.runtime?.error
    };
  }
}

let plugin_config_ui = [];
let debugServer = null;
const plugin_init = async (ctx) => {
  pluginState.init(ctx);
  ctx.logger.info("插件调试服务初始化中...");
  plugin_config_ui = ctx.NapCatConfig.combine(
    ctx.NapCatConfig.html(`
      <div style="padding:16px 20px;background:#fdf2f8;border:1px solid #f5c6d8;border-radius:8px;margin-bottom:16px;color:#4a3040;font-family:system-ui,-apple-system,sans-serif">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d4709a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.855z"/></svg>
          <span style="font-size:16px;font-weight:600;color:#8b3a62">Plugin Debug Service</span>
        </div>
        <p style="margin:0;font-size:13px;line-height:1.6;color:#7a506b">
          WebSocket 调试服务器，通过 JSON-RPC 协议暴露插件管理接口，配合 Vite 插件或 CLI 工具实现插件热重载开发。
        </p>
      </div>
    `),
    ctx.NapCatConfig.number("port", "调试服务端口", 8998, "WebSocket 监听端口"),
    ctx.NapCatConfig.text("host", "监听地址", "127.0.0.1", "仅限本地调试时使用 127.0.0.1；改为 0.0.0.0 会暴露在网络中，存在安全风险"),
    ctx.NapCatConfig.html(`
      <div style="padding:10px 14px;background:#fef2f2;border-left:3px solid #ef4444;border-radius:4px;margin:8px 0;font-family:system-ui,-apple-system,sans-serif">
        <p style="margin:0;font-size:12px;line-height:1.6;color:#7f1d1d">
          <strong style="color:#991b1b">安全提示：</strong>默认不启用认证，任何能访问该端口的客户端均可执行插件管理操作（加载、卸载、重载插件等）。
          如需远程调试，请务必启用认证并设置高强度 Token，同时通过防火墙限制来源 IP。建议优先使用 SSH 隧道转发端口。
        </p>
      </div>
    `),
    ctx.NapCatConfig.boolean("enableAuth", "启用认证", false, "启用后客户端需提供 Token 才能连接，强烈建议远程调试时开启"),
    ctx.NapCatConfig.text("authToken", "认证 Token", "", "客户端连接时的认证凭据，请使用高强度随机字符串")
  );
  debugServer = new DebugServer(ctx, pluginState.config);
  await debugServer.start();
  ctx.logger.info("插件调试服务就绪");
  ctx.logger.info(`CLI 连接: node cli.mjs ws://${pluginState.config.host}:${pluginState.config.port}`);
};
const plugin_onmessage = async (_ctx, event) => {
  debugServer?.broadcastEvent({ eventType: "message", ...safeSerialize(event) });
};
const plugin_onevent = async (_ctx, event) => {
  debugServer?.broadcastEvent({ eventType: "notify", ...safeSerialize(event) });
};
const plugin_cleanup = async (ctx) => {
  ctx.logger.info("停止调试服务...");
  await debugServer?.stop();
  debugServer = null;
  pluginState.cleanup();
};
const plugin_get_config = async () => {
  return pluginState.config;
};
const plugin_set_config = async (_ctx, config) => {
  pluginState.replaceConfig(config);
  await debugServer?.stop();
  debugServer = new DebugServer(pluginState.ctx, pluginState.config);
  await debugServer.start();
};
function safeSerialize(obj) {
  try {
    return JSON.parse(JSON.stringify(obj));
  } catch {
    return { raw: String(obj) };
  }
}

export { plugin_cleanup, plugin_config_ui, plugin_get_config, plugin_init, plugin_onevent, plugin_onmessage, plugin_set_config };
