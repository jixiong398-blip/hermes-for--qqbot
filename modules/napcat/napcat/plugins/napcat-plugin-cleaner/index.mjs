import fs from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';

const defaultCleanOptions = {
  enableVideo: true,
  enableVideoThumb: true,
  enablePtt: true,
  enablePic: true,
  enableFile: true,
  enableLog: true,
  enableLogCache: true,
  enableNtTemp: true,
  enableNapCatData: false,
  enableNapCatTemp: true,
  retainDays: 7
};

class PluginState {
  constructor() {
    this._logger = null;
    this._dataPath = "";
    this._configPath = "";
    this._config = {
      defaultOptions: { ...defaultCleanOptions },
      scheduleTasks: []
    };
    // uin -> uid 的映射
    this._uinToUidMap = /* @__PURE__ */ new Map();
    // uin -> hash目录 的映射
    this._uinToHashDirMap = /* @__PURE__ */ new Map();
    // 定时器存储
    this._scheduleTimers = /* @__PURE__ */ new Map();
    // 当前账号 uid
    this._currentUid = "";
    // 平台检测
    this.isWindows = os.platform() === "win32";
  }
  // getter/setter
  get logger() {
    return this._logger;
  }
  set logger(val) {
    this._logger = val;
  }
  get dataPath() {
    return this._dataPath;
  }
  set dataPath(val) {
    this._dataPath = val;
  }
  get configPath() {
    return this._configPath;
  }
  set configPath(val) {
    this._configPath = val;
  }
  get config() {
    return this._config;
  }
  set config(val) {
    this._config = val;
  }
  get currentUid() {
    return this._currentUid;
  }
  set currentUid(val) {
    this._currentUid = val;
  }
  get uinToUidMap() {
    return this._uinToUidMap;
  }
  get uinToHashDirMap() {
    return this._uinToHashDirMap;
  }
  get scheduleTimers() {
    return this._scheduleTimers;
  }
  // 日志方法
  log(level, ...args) {
    if (!this._logger) return;
    switch (level) {
      case "info":
        this._logger.info(...args);
        break;
      case "warn":
        this._logger.warn(...args);
        break;
      case "error":
        this._logger.error(...args);
        break;
    }
  }
  // 更新配置
  updateConfig(partial) {
    this._config = { ...this._config, ...partial };
  }
  // 更新默认选项
  updateDefaultOptions(options) {
    this._config.defaultOptions = { ...this._config.defaultOptions, ...options };
  }
}
const pluginState = new PluginState();

function formatSize(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
function computeNtHash(uid) {
  const md5Uid = crypto.createHash("md5").update(uid).digest("hex");
  const hash = crypto.createHash("md5").update(md5Uid + "nt_kernel").digest("hex");
  return hash;
}
function generateId() {
  return Date.now().toString(36) + Math.random().toString(36).substring(2, 8);
}
function getDateSubdirs(basePath) {
  if (!fs.existsSync(basePath)) {
    return [];
  }
  try {
    const entries = fs.readdirSync(basePath, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory() && /^\d{4}-\d{2}$/.test(e.name)).map((e) => path.join(basePath, e.name));
  } catch {
    return [];
  }
}
function saveConfig() {
  try {
    const configDir = path.dirname(pluginState.configPath);
    if (!fs.existsSync(configDir)) {
      fs.mkdirSync(configDir, { recursive: true });
    }
    fs.writeFileSync(pluginState.configPath, JSON.stringify(pluginState.config, null, 2), "utf-8");
  } catch (e) {
    pluginState.log("error", "保存配置失败", e);
  }
}
function loadConfig() {
  try {
    if (fs.existsSync(pluginState.configPath)) {
      const savedConfig = JSON.parse(fs.readFileSync(pluginState.configPath, "utf-8"));
      pluginState.updateConfig(savedConfig);
    }
  } catch (e) {
    pluginState.log("warn", "加载配置失败", e);
  }
}

function getNtDataPath(dataPath, uin) {
  if (pluginState.isWindows) {
    const ntDataPath = path.join(dataPath, uin, "nt_qq", "nt_data");
    if (fs.existsSync(ntDataPath)) {
      return ntDataPath;
    }
    return null;
  } else {
    const cachedHashDir = pluginState.uinToHashDirMap.get(uin);
    if (cachedHashDir) {
      const ntDataPath = path.join(cachedHashDir, "nt_data");
      if (fs.existsSync(ntDataPath)) {
        return ntDataPath;
      }
    }
    const uid = pluginState.uinToUidMap.get(uin);
    if (uid) {
      const hash = computeNtHash(uid);
      const hashDir = path.join(dataPath, `nt_qq_${hash}`);
      const ntDataPath = path.join(hashDir, "nt_data");
      if (fs.existsSync(ntDataPath)) {
        pluginState.uinToHashDirMap.set(uin, hashDir);
        return ntDataPath;
      }
    }
    if (fs.existsSync(dataPath)) {
      try {
        const entries = fs.readdirSync(dataPath, { withFileTypes: true });
        for (const entry of entries) {
          if (entry.isDirectory() && /^nt_qq_[a-f0-9]{32}$/.test(entry.name)) {
            const hashDir = path.join(dataPath, entry.name);
            const ntDataPath = path.join(hashDir, "nt_data");
            if (fs.existsSync(ntDataPath)) {
              if (!pluginState.uinToHashDirMap.has(uin)) {
                pluginState.uinToHashDirMap.set(uin, hashDir);
              }
              return ntDataPath;
            }
          }
        }
      } catch {
      }
    }
    return null;
  }
}
function getNtTempPath(dataPath, uin) {
  if (pluginState.isWindows) {
    return null;
  }
  const cachedHashDir = pluginState.uinToHashDirMap.get(uin);
  if (cachedHashDir) {
    const ntTempPath = path.join(cachedHashDir, "nt_temp");
    if (fs.existsSync(ntTempPath)) {
      return ntTempPath;
    }
  }
  return null;
}
function getNapCatPath(dataPath) {
  return path.join(dataPath, "NapCat");
}
function getCleanablePaths(dataPath, uin) {
  const ntDataPath = getNtDataPath(dataPath, uin);
  const result = {
    video: [],
    videoThumb: [],
    ptt: [],
    pic: [],
    file: [],
    log: [],
    logCache: [],
    ntTemp: [],
    napCatData: [],
    napCatTemp: []
  };
  if (!ntDataPath) {
    return result;
  }
  const videoBase = path.join(ntDataPath, "Video");
  const videoDirs = getDateSubdirs(videoBase);
  result.video = videoDirs.flatMap((dir) => {
    const oriPath = path.join(dir, "Ori");
    return fs.existsSync(oriPath) ? [oriPath] : [dir];
  });
  result.videoThumb = videoDirs.flatMap((dir) => {
    const thumbPath = path.join(dir, "Thumb");
    const thumbTempPath = path.join(dir, "ThumbTemp");
    const paths = [];
    if (fs.existsSync(thumbPath)) paths.push(thumbPath);
    if (fs.existsSync(thumbTempPath)) paths.push(thumbTempPath);
    return paths;
  });
  const pttBase = path.join(ntDataPath, "Ptt");
  const pttDirs = getDateSubdirs(pttBase);
  result.ptt = pttDirs.flatMap((dir) => {
    const oriPath = path.join(dir, "Ori");
    const oriTempPath = path.join(dir, "OriTemp");
    const paths = [];
    if (fs.existsSync(oriPath)) paths.push(oriPath);
    if (fs.existsSync(oriTempPath)) paths.push(oriTempPath);
    return paths.length > 0 ? paths : [dir];
  });
  const picBase = path.join(ntDataPath, "Pic");
  const picDirs = getDateSubdirs(picBase);
  result.pic = picDirs.flatMap((dir) => {
    const oriPath = path.join(dir, "Ori");
    return fs.existsSync(oriPath) ? [oriPath] : [dir];
  });
  const fileOri = path.join(ntDataPath, "File", "Ori");
  const fileThumb = path.join(ntDataPath, "File", "Thumb");
  const fileThumbTemp = path.join(ntDataPath, "File", "ThumbTemp");
  if (fs.existsSync(fileOri)) result.file.push(fileOri);
  if (fs.existsSync(fileThumb)) result.file.push(fileThumb);
  if (fs.existsSync(fileThumbTemp)) result.file.push(fileThumbTemp);
  const logPath = path.join(ntDataPath, "log");
  if (fs.existsSync(logPath)) result.log.push(logPath);
  const logCachePath = path.join(ntDataPath, "log-cache");
  if (fs.existsSync(logCachePath)) result.logCache.push(logCachePath);
  const ntTempPath = getNtTempPath(dataPath, uin);
  if (ntTempPath) result.ntTemp.push(ntTempPath);
  const napCatPath = getNapCatPath(dataPath);
  const napCatDataPath = path.join(napCatPath, "data");
  const napCatTempPath = path.join(napCatPath, "temp");
  if (fs.existsSync(napCatDataPath)) result.napCatData.push(napCatDataPath);
  if (fs.existsSync(napCatTempPath)) result.napCatTemp.push(napCatTempPath);
  return result;
}
function cleanDirectory(dirPath, retainDays) {
  let files = 0;
  let size = 0;
  if (!fs.existsSync(dirPath)) {
    return { files, size };
  }
  const now = Date.now();
  const retainMs = retainDays * 24 * 60 * 60 * 1e3;
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name);
      if (entry.isFile()) {
        try {
          const stat = fs.statSync(fullPath);
          if (now - stat.mtimeMs > retainMs) {
            size += stat.size;
            fs.unlinkSync(fullPath);
            files++;
          }
        } catch (e) {
          pluginState.log("warn", `无法删除文件: ${fullPath}`, e);
        }
      } else if (entry.isDirectory()) {
        const subStats = cleanDirectory(fullPath, retainDays);
        files += subStats.files;
        size += subStats.size;
        try {
          const remaining = fs.readdirSync(fullPath);
          if (remaining.length === 0) {
            fs.rmdirSync(fullPath);
          }
        } catch {
        }
      }
    }
  } catch (e) {
    pluginState.log("warn", `无法读取目录: ${dirPath}`, e);
  }
  return { files, size };
}
function getDirStatsWithFilter(dirPath, now, retainMs) {
  let files = 0;
  let size = 0;
  if (!fs.existsSync(dirPath)) {
    return { files, size };
  }
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name);
      if (entry.isFile()) {
        try {
          const stat = fs.statSync(fullPath);
          if (retainMs <= 0 || now - stat.mtimeMs > retainMs) {
            files++;
            size += stat.size;
          }
        } catch {
        }
      } else if (entry.isDirectory()) {
        const subStats = getDirStatsWithFilter(fullPath, now, retainMs);
        files += subStats.files;
        size += subStats.size;
      }
    }
  } catch {
  }
  return { files, size };
}
function scanCache(dataPath, uin, retainDays = 0) {
  const paths = getCleanablePaths(dataPath, uin);
  const stats = {
    totalFiles: 0,
    totalSize: 0,
    categories: {
      video: { files: 0, size: 0 },
      videoThumb: { files: 0, size: 0 },
      ptt: { files: 0, size: 0 },
      pic: { files: 0, size: 0 },
      file: { files: 0, size: 0 },
      log: { files: 0, size: 0 },
      logCache: { files: 0, size: 0 },
      ntTemp: { files: 0, size: 0 },
      napCatData: { files: 0, size: 0 },
      napCatTemp: { files: 0, size: 0 }
    }
  };
  const now = Date.now();
  const retainMs = retainDays * 24 * 60 * 60 * 1e3;
  for (const [category, dirs] of Object.entries(paths)) {
    for (const dir of dirs) {
      const dirStats = getDirStatsWithFilter(dir, now, retainMs);
      const cat = stats.categories[category];
      if (cat) {
        cat.files += dirStats.files;
        cat.size += dirStats.size;
      }
      stats.totalFiles += dirStats.files;
      stats.totalSize += dirStats.size;
    }
  }
  return stats;
}
function executeClean(dataPath, uin, options) {
  const paths = getCleanablePaths(dataPath, uin);
  const stats = {
    totalFiles: 0,
    totalSize: 0,
    categories: {
      video: { files: 0, size: 0 },
      videoThumb: { files: 0, size: 0 },
      ptt: { files: 0, size: 0 },
      pic: { files: 0, size: 0 },
      file: { files: 0, size: 0 },
      log: { files: 0, size: 0 },
      logCache: { files: 0, size: 0 },
      ntTemp: { files: 0, size: 0 },
      napCatData: { files: 0, size: 0 },
      napCatTemp: { files: 0, size: 0 }
    }
  };
  const categoryEnabled = {
    video: options.enableVideo,
    videoThumb: options.enableVideoThumb,
    ptt: options.enablePtt,
    pic: options.enablePic,
    file: options.enableFile,
    log: options.enableLog,
    logCache: options.enableLogCache,
    ntTemp: options.enableNtTemp,
    napCatData: options.enableNapCatData,
    napCatTemp: options.enableNapCatTemp
  };
  for (const [category, dirs] of Object.entries(paths)) {
    if (!categoryEnabled[category]) continue;
    for (const dir of dirs) {
      const cleanResult = cleanDirectory(dir, options.retainDays);
      const cat = stats.categories[category];
      if (cat) {
        cat.files += cleanResult.files;
        cat.size += cleanResult.size;
      }
      stats.totalFiles += cleanResult.files;
      stats.totalSize += cleanResult.size;
    }
  }
  return stats;
}
function getAllAccounts(dataPath) {
  if (pluginState.isWindows) {
    if (!fs.existsSync(dataPath)) {
      return [];
    }
    try {
      const entries = fs.readdirSync(dataPath, { withFileTypes: true });
      return entries.filter((e) => e.isDirectory() && /^\d{5,11}$/.test(e.name)).map((e) => e.name);
    } catch {
      return [];
    }
  } else {
    const accounts = [];
    pluginState.uinToUidMap.forEach((uid, uin) => {
      if (!pluginState.uinToHashDirMap.has(uin)) {
        const hash = computeNtHash(uid);
        const hashDir = path.join(dataPath, `nt_qq_${hash}`);
        if (fs.existsSync(hashDir)) {
          pluginState.uinToHashDirMap.set(uin, hashDir);
        }
      }
      if (pluginState.uinToHashDirMap.has(uin)) {
        accounts.push(uin);
      }
    });
    return accounts;
  }
}

function clearScheduleTimer(taskId) {
  const timer = pluginState.scheduleTimers.get(taskId);
  if (timer) {
    clearInterval(timer);
    pluginState.scheduleTimers.delete(taskId);
  }
}
function setupScheduleTask(task) {
  clearScheduleTimer(task.id);
  if (!task.enabled) {
    return;
  }
  const now = /* @__PURE__ */ new Date();
  let nextRun = /* @__PURE__ */ new Date();
  nextRun.setHours(task.cronHour, task.cronMinute, 0, 0);
  if (nextRun.getTime() <= now.getTime()) {
    nextRun.setDate(nextRun.getDate() + 1);
  }
  const freq = task.frequency || "daily";
  if (freq === "weekly") {
    const targetDay = task.frequencyValue ?? 0;
    while (nextRun.getDay() !== targetDay) {
      nextRun.setDate(nextRun.getDate() + 1);
    }
  } else if (freq === "interval") {
    const intervalDays = task.frequencyValue || 3;
    if (task.lastRun) {
      const lastRunDate = new Date(task.lastRun);
      const potentialNext = new Date(lastRunDate);
      potentialNext.setDate(potentialNext.getDate() + intervalDays);
      potentialNext.setHours(task.cronHour, task.cronMinute, 0, 0);
      if (potentialNext.getTime() > now.getTime()) {
        nextRun = potentialNext;
      }
    }
  }
  const msUntilNextRun = nextRun.getTime() - now.getTime();
  pluginState.log("info", `定时任务 [${task.name}] (${freq}) 将在 ${nextRun.toLocaleString()} 执行`);
  const timer = setTimeout(() => {
    runScheduleTask(task);
    const currentTask = pluginState.config.scheduleTasks.find((t) => t.id === task.id);
    if (currentTask && currentTask.enabled) {
      setupScheduleTask(currentTask);
    }
  }, msUntilNextRun);
  pluginState.scheduleTimers.set(task.id, timer);
}
function runScheduleTask(task) {
  pluginState.log("info", `开始执行定时任务: ${task.name}`);
  let accounts = task.accounts;
  if (accounts.length === 0) {
    accounts = getAllAccounts(pluginState.dataPath);
  }
  let totalFiles = 0;
  let totalSize = 0;
  const results = [];
  for (const account of accounts) {
    try {
      const result = executeClean(pluginState.dataPath, account, task.options);
      totalFiles += result.totalFiles;
      totalSize += result.totalSize;
      results.push(`${account}: ${result.totalFiles}文件, ${formatSize(result.totalSize)}`);
    } catch (e) {
      pluginState.log("error", `清理账号 ${account} 失败:`, e);
      results.push(`${account}: 失败`);
    }
  }
  const foundTask = pluginState.config.scheduleTasks.find((t) => t.id === task.id);
  if (foundTask) {
    foundTask.lastRun = (/* @__PURE__ */ new Date()).toISOString();
    foundTask.lastResult = `删除 ${totalFiles} 文件, 释放 ${formatSize(totalSize)}`;
    saveConfig();
  }
  pluginState.log("info", `定时任务 [${task.name}] 完成: 删除 ${totalFiles} 文件, 释放 ${formatSize(totalSize)}`);
}
function initAllScheduleTasks() {
  for (const task of pluginState.config.scheduleTasks) {
    setupScheduleTask(task);
  }
}
function clearAllScheduleTimers() {
  Array.from(pluginState.scheduleTimers.keys()).forEach((id) => {
    clearScheduleTimer(id);
  });
}
function createScheduleTask(body) {
  const options = { ...pluginState.config.defaultOptions, ...body.options || {} };
  if (typeof body.retainDays === "number") {
    options.retainDays = body.retainDays;
  }
  const task = {
    id: generateId(),
    name: body.name || "新任务",
    accounts: body.accounts || [],
    options,
    cronHour: body.cronHour ?? 3,
    cronMinute: body.cronMinute ?? 0,
    frequency: body.frequency || "daily",
    frequencyValue: body.frequencyValue ?? 0,
    enabled: body.enabled ?? true
  };
  pluginState.config.scheduleTasks.push(task);
  saveConfig();
  setupScheduleTask(task);
  return task;
}
function updateScheduleTask(id, body) {
  const index = pluginState.config.scheduleTasks.findIndex((t) => t.id === id);
  if (index < 0) return null;
  const task = pluginState.config.scheduleTasks[index];
  if (!task) return null;
  Object.assign(task, body);
  saveConfig();
  setupScheduleTask(task);
  return task;
}
function deleteScheduleTask(id) {
  const index = pluginState.config.scheduleTasks.findIndex((t) => t.id === id);
  if (index < 0) return false;
  clearScheduleTimer(id);
  pluginState.config.scheduleTasks.splice(index, 1);
  saveConfig();
  return true;
}
function runTaskNow(id) {
  const task = pluginState.config.scheduleTasks.find((t) => t.id === id);
  if (!task) {
    return { success: false };
  }
  let accounts = task.accounts;
  if (accounts.length === 0) {
    accounts = getAllAccounts(pluginState.dataPath);
  }
  let totalFiles = 0;
  let totalSize = 0;
  for (const account of accounts) {
    const result = executeClean(pluginState.dataPath, account, task.options);
    totalFiles += result.totalFiles;
    totalSize += result.totalSize;
  }
  task.lastRun = (/* @__PURE__ */ new Date()).toISOString();
  task.lastResult = `删除 ${totalFiles} 文件, 释放 ${formatSize(totalSize)}`;
  saveConfig();
  return { success: true, task, totalFiles, totalSize: formatSize(totalSize) };
}

function registerApiRoutes(router, selfUin) {
  router.get("/accounts", (_req, res) => {
    try {
      const dataPath = pluginState.dataPath;
      const accounts = getAllAccounts(dataPath);
      pluginState.log("info", `[/accounts] dataPath=${dataPath}, currentUin=${selfUin}`);
      pluginState.log("info", `[/accounts] getAllAccounts 返回: ${accounts.length} 个账号`);
      const accountStats = accounts.map((uin) => {
        const stats = scanCache(dataPath, uin, 0);
        return {
          uin,
          isCurrent: uin === selfUin,
          stats: {
            totalFiles: stats.totalFiles,
            totalSize: formatSize(stats.totalSize),
            estimatedCleanSize: "0 B",
            categories: Object.fromEntries(
              Object.entries(stats.categories).map(([k, v]) => [k, {
                files: v.files,
                size: formatSize(v.size),
                estimatedCleanSize: "0 B"
              }])
            )
          }
        };
      });
      res.json({
        code: 0,
        data: {
          dataPath,
          currentUin: selfUin,
          accounts: accountStats
        }
      });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.get("/stats/:uin", (req, res) => {
    try {
      const uin = req.params["uin"] ?? "";
      const retainDays = parseInt(req.query["retainDays"]) || 0;
      if (!uin) {
        res.status(400).json({ code: -1, message: "uin参数缺失" });
        return;
      }
      const dataPath = pluginState.dataPath;
      const stats = scanCache(dataPath, uin, 0);
      const estimatedClean = retainDays > 0 ? scanCache(dataPath, uin, retainDays) : null;
      res.json({
        code: 0,
        data: {
          uin,
          stats,
          estimatedClean,
          formattedStats: {
            totalFiles: stats.totalFiles,
            totalSize: formatSize(stats.totalSize),
            estimatedCleanSize: estimatedClean ? formatSize(estimatedClean.totalSize) : "0 B",
            categories: Object.fromEntries(
              Object.entries(stats.categories).map(([k, v]) => [k, {
                files: v.files,
                size: formatSize(v.size),
                sizeBytes: v.size,
                estimatedCleanSize: estimatedClean && estimatedClean.categories[k] ? formatSize(estimatedClean.categories[k].size) : "0 B"
              }])
            )
          }
        }
      });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.post("/clean", (req, res) => {
    try {
      const dataPath = pluginState.dataPath;
      const body = req.body;
      let accounts = body.accounts || [selfUin];
      if (accounts.length === 0) {
        accounts = getAllAccounts(dataPath);
      }
      const options = { ...pluginState.config.defaultOptions, ...body.options || {} };
      pluginState.log("info", `开始清理缓存，账号: ${accounts.join(", ")}，保留 ${options.retainDays} 天`);
      const results = [];
      let totalFiles = 0;
      let totalSize = 0;
      for (const uin of accounts) {
        const result = executeClean(dataPath, uin, options);
        totalFiles += result.totalFiles;
        totalSize += result.totalSize;
        results.push({
          uin,
          stats: result,
          formatted: {
            totalFiles: result.totalFiles,
            totalSize: formatSize(result.totalSize),
            categories: Object.fromEntries(
              Object.entries(result.categories).map(([k, v]) => [k, {
                files: v.files,
                size: formatSize(v.size)
              }])
            )
          }
        });
      }
      pluginState.log("info", `清理完成: 删除 ${totalFiles} 个文件，释放 ${formatSize(totalSize)}`);
      res.json({
        code: 0,
        message: `清理完成: 删除 ${totalFiles} 个文件，释放 ${formatSize(totalSize)}`,
        data: {
          totalFiles,
          totalSize: formatSize(totalSize),
          results
        }
      });
    } catch (e) {
      pluginState.log("error", "清理失败:", e);
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.get("/config", (_req, res) => {
    res.json({
      code: 0,
      data: pluginState.config
    });
  });
  router.post("/config/options", (req, res) => {
    try {
      const options = req.body;
      pluginState.updateDefaultOptions(options);
      saveConfig();
      res.json({ code: 0, message: "默认选项已保存" });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.get("/schedules", (_req, res) => {
    res.json({
      code: 0,
      data: pluginState.config.scheduleTasks
    });
  });
  router.post("/schedules", (req, res) => {
    try {
      const body = req.body;
      const task = createScheduleTask(body);
      res.json({ code: 0, message: "定时任务已添加", data: task });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.post("/schedules/:id", (req, res) => {
    try {
      const { id } = req.params;
      const body = req.body;
      const task = updateScheduleTask(id, body);
      if (!task) {
        res.status(404).json({ code: -1, message: "任务不存在" });
        return;
      }
      res.json({ code: 0, message: "定时任务已更新", data: task });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.delete("/schedules/:id", (req, res) => {
    try {
      const { id } = req.params;
      const success = deleteScheduleTask(id);
      if (!success) {
        res.status(404).json({ code: -1, message: "任务不存在" });
        return;
      }
      res.json({ code: 0, message: "定时任务已删除" });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  router.post("/schedules/:id/run", (req, res) => {
    try {
      const { id } = req.params;
      const result = runTaskNow(id);
      if (!result.success) {
        res.status(404).json({ code: -1, message: "任务不存在" });
        return;
      }
      res.json({
        code: 0,
        message: `任务执行完成: 删除 ${result.totalFiles} 文件, 释放 ${result.totalSize}`,
        data: result.task
      });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
}

const plugin_init = async (ctx) => {
  pluginState.logger = ctx.logger;
  pluginState.dataPath = ctx.core.dataPath;
  pluginState.configPath = ctx.configPath;
  pluginState.log("info", "NapCat 缓存清理插件已初始化");
  pluginState.log("info", `运行平台: ${pluginState.isWindows ? "Windows" : "Linux"}`);
  pluginState.log("info", `dataPath: ${pluginState.dataPath}`);
  try {
    const loginService = ctx.core.context.wrapper.NodeIKernelLoginService.get();
    const loginResult = await loginService.getLoginList();
    pluginState.log("info", `getLoginList 返回: result=${loginResult.result}, count=${loginResult.LocalLoginInfoList?.length || 0}`);
    if (loginResult.result === 0 && loginResult.LocalLoginInfoList) {
      pluginState.log("info", `获取到 ${loginResult.LocalLoginInfoList.length} 个登录账号`);
      for (const item of loginResult.LocalLoginInfoList) {
        if (item.uin && item.uid) {
          pluginState.uinToUidMap.set(item.uin, item.uid);
          if (item.uin === ctx.core.selfInfo.uin) {
            pluginState.currentUid = item.uid;
          }
          if (!pluginState.isWindows) {
            const hash = computeNtHash(item.uid);
            const hashDir = path.join(pluginState.dataPath, `nt_qq_${hash}`);
            if (fs.existsSync(hashDir)) {
              pluginState.uinToHashDirMap.set(item.uin, hashDir);
              pluginState.log("info", `账号 ${item.uin} 的 hash 目录已缓存: ${hashDir}`);
            } else {
              pluginState.log("warn", `账号 ${item.uin} 的 hash 目录不存在: ${hashDir}`);
            }
          }
          pluginState.log("info", `已加载账号映射: uin=${item.uin}, uid=${item.uid}, nickName=${item.nickName || "N/A"}`);
        }
      }
    } else {
      pluginState.log("warn", `getLoginList 返回结果异常: result=${loginResult.result}`);
    }
  } catch (e) {
    pluginState.log("warn", `通过 getLoginList 获取账号列表失败: ${e}`);
    const selfInfo = ctx.core.selfInfo;
    if (selfInfo.uid && selfInfo.uin) {
      pluginState.currentUid = selfInfo.uid;
      pluginState.uinToUidMap.set(selfInfo.uin, selfInfo.uid);
      pluginState.log("info", `回退使用 selfInfo: uin=${selfInfo.uin}, uid=${selfInfo.uid}`);
      if (!pluginState.isWindows) {
        const hash = computeNtHash(selfInfo.uid);
        const hashDir = path.join(pluginState.dataPath, `nt_qq_${hash}`);
        if (fs.existsSync(hashDir)) {
          pluginState.uinToHashDirMap.set(selfInfo.uin, hashDir);
          pluginState.log("info", `Linux hash 目录已缓存`);
        }
      }
    } else {
      pluginState.log("warn", `selfInfo 信息不完整: uid=${selfInfo.uid}, uin=${selfInfo.uin}`);
    }
  }
  loadConfig();
  initAllScheduleTasks();
  ctx.router.static("/static", "webui");
  ctx.router.get("/static/plugin-info.js", (_req, res) => {
    try {
      res.type("application/javascript");
      res.send(`window.__PLUGIN_NAME__ = ${JSON.stringify(ctx.pluginName)};`);
    } catch (e) {
      res.status(500).send("// failed to generate plugin-info");
    }
  });
  registerApiRoutes(ctx.router, ctx.core.selfInfo.uin);
  ctx.router.page({
    path: "dashboard",
    title: "缓存清理",
    icon: "🧹",
    htmlFile: "webui/dashboard.html",
    description: "查看和清理 QQ 缓存文件"
  });
  pluginState.log("info", "WebUI 路由已注册:");
  pluginState.log("info", "  - API 路由: /api/Plugin/ext/" + ctx.pluginName + "/");
  pluginState.log("info", "  - 扩展页面: /plugin/" + ctx.pluginName + "/page/dashboard");
};
const plugin_cleanup = async () => {
  clearAllScheduleTimers();
  pluginState.log("info", "缓存清理插件已卸载");
};

export { plugin_cleanup, plugin_init };
