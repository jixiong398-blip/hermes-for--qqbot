// download-backend.js — Live2D Model Package Download & Decrypt Backend
// Used by main.js via IPC to fetch, download, verify, and install .cvpkg models.

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');
const { execFile } = require('child_process');
const { spawn } = require('child_process');

// ─── Constants ────────────────────────────────────────────────────────────────
const MANIFEST_URL = 'https://cucumbervpet.sevenvoxel.com/manifest.json';
const DOWNLOAD_DIR = path.join(__dirname, 'downloads');
const MODELS_DIR = path.join(__dirname, 'assets', 'models');
const DECRYPT_SCRIPT = path.join(
  process.env.USERPROFILE || '',
  '.config',
  'opencode',
  'skills',
  'cvpkg-decrypt',
  'decrypt_cvpkg.py'
);

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Ensure a directory exists, creating it recursively if needed.
 */
function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

/**
 * Fetch a URL and return the response body as string / parse as JSON.
 * @param {string} url - HTTPS URL
 * @param {boolean} asJSON - parse as JSON (default true)
 * @returns {Promise<any>}
 */
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const proto = url.startsWith('https') ? https : http;
    const req = proto.get(url, { timeout: 15000 }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        fetchJSON(res.headers.location).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} fetching ${url}`));
        return;
      }
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try {
          // Strip BOM if present
          const clean = body.startsWith('\uFEFF') ? body.slice(1) : body;
          resolve(JSON.parse(clean));
        } catch (e) {
          reject(new Error(`JSON parse error for ${url}: ${e.message}`));
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });
  });
}

/**
 * Verify SHA256 checksum of a file.
 * @param {string} filePath
 * @param {string} expectedHash - hex string
 * @returns {boolean}
 */
function verifyChecksum(filePath, expectedHash) {
  if (!expectedHash) return true; // skip if no hash provided
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => {
      const actual = hash.digest('hex').toLowerCase();
      const expected = expectedHash.toLowerCase();
      resolve(actual === expected);
    });
    stream.on('error', reject);
  });
}

/**
 * Parse roleId into { character, costume }.
 * Format: "{char}_{costume}" — first segment before first underscore is character.
 * Example: "mutsumi_adv_live2d_mutsumi_007_casual_spring_01"
 *   → character: "mutsumi", costume: "adv_live2d_mutsumi_007_casual_spring_01"
 */
function parseRoleId(roleId) {
  const idx = roleId.indexOf('_');
  if (idx === -1) {
    return { character: roleId, costume: 'default' };
  }
  return {
    character: roleId.slice(0, idx),
    costume: roleId.slice(idx + 1),
  };
}

/**
 * Format file size in human-readable form.
 */
function formatSize(bytes) {
  if (bytes == null || bytes <= 0) return '? MB';
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return mb.toFixed(1) + ' MB';
  const kb = bytes / 1024;
  return kb.toFixed(0) + ' KB';
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Fetch the CucumberVPet manifest.json and return a flat role list.
 * Each role: { id, displayName, url, sha256, size, character, costume, category, installed }
 * @param {object} config - parsed conf.json with mainCharacters, subCharacters arrays
 * @param {string} modelsDir - path to models directory (default MODELS_DIR)
 * @returns {Promise<Array>}
 */
async function fetchManifest(config = {}, modelsDir = MODELS_DIR) {
  const manifest = await fetchJSON(MANIFEST_URL);
  const roles = manifest.roles || [];

  const mainChars = new Set(config.mainCharacters || []);
  const subChars = new Set(config.subCharacters || []);

  return roles.map((r) => {
    const { character, costume } = parseRoleId(r.id);
    const installed = isRoleInstalled(r.id, modelsDir);
    let category = 'other';
    if (mainChars.has(character)) category = 'main';
    else if (subChars.has(character)) category = 'sub';

    return {
      id: r.id,
      displayName: r.displayName || `${character} - ${costume}`,
      url: r.url,
      sha256: r.sha256 || '',
      size: r.size || 0,
      sizeFormatted: formatSize(r.size),
      character,
      costume,
      category,
      installed,
    };
  });
}

/**
 * Download a .cvpkg file with progress tracking.
 * @param {string} roleId - unique role identifier
 * @param {string} url - CDN download URL
 * @param {Function} onProgress - callback(percent, downloadedBytes, totalBytes, status)
 *   status: 'connecting' | 'downloading' | 'verifying' | 'done' | 'error'
 * @param {string} downloadDir - directory to save the file (default DOWNLOAD_DIR)
 * @returns {Promise<{filePath: string}>}
 */
async function downloadRole(roleId, url, onProgress, downloadDir = DOWNLOAD_DIR) {
  ensureDir(downloadDir);
  const filePath = path.join(downloadDir, `${roleId}.cvpkg`);

  // Remove partial download if exists
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
  }

  return new Promise((resolve, reject) => {
    onProgress && onProgress(0, 0, 0, 'connecting');

    const proto = url.startsWith('https') ? https : http;
    const req = proto.get(url, { timeout: 300000 }, (res) => {
      // Handle redirects
      if (res.statusCode === 301 || res.statusCode === 302) {
        downloadRole(roleId, res.headers.location, onProgress, downloadDir)
          .then(resolve).catch(reject);
        return;
      }

      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} downloading ${roleId}`));
        return;
      }

      const total = parseInt(res.headers['content-length'] || '0', 10);
      let downloaded = 0;
      let lastEmit = 0;
      const file = fs.createWriteStream(filePath);

      res.on('data', (chunk) => {
        downloaded += chunk.length;
        // Throttle progress updates to every 250ms
        const now = Date.now();
        if (onProgress && (now - lastEmit >= 250 || downloaded >= total)) {
          lastEmit = now;
          const percent = total > 0 ? Math.min(99, Math.round((downloaded / total) * 100)) : 0;
          onProgress(percent, downloaded, total, 'downloading');
        }
      });

      res.on('error', (err) => {
        file.close();
        try { fs.unlinkSync(filePath); } catch {}
        reject(err);
      });

      res.pipe(file);

      file.on('finish', () => {
        file.close(() => {
          // Final progress update
          onProgress && onProgress(100, downloaded, total, 'verifying');
          resolve({ filePath });
        });
      });

      file.on('error', (err) => {
        try { fs.unlinkSync(filePath); } catch {}
        reject(err);
      });
    });

    req.on('error', (err) => {
      try { fs.unlinkSync(filePath); } catch {}
      reject(err);
    });

    req.on('timeout', () => {
      req.destroy();
      try { fs.unlinkSync(filePath); } catch {}
      reject(new Error(`Download timeout for ${roleId}`));
    });
  });
}

/**
 * Decrypt and extract a .cvpkg file to the models directory.
 * Uses the cvpkg-decrypt Python script via child_process.execFile.
 * @param {string} cvpkgPath - full path to .cvpkg file
 * @param {string} roleId - role identifier (used to derive output path)
 * @param {string} modelsDir - models root directory (default MODELS_DIR)
 * @returns {Promise<{character: string, costume: string, outputDir: string}>}
 */
async function decryptAndInstall(cvpkgPath, roleId, modelsDir = MODELS_DIR) {
  if (!fs.existsSync(cvpkgPath)) {
    throw new Error(`Download file not found: ${cvpkgPath}`);
  }

  const { character, costume } = parseRoleId(roleId);
  const outputDir = path.join(modelsDir, character, costume);

  // Check if already installed
  if (isRoleInstalled(roleId, modelsDir)) {
    throw new Error(`Model already installed: ${character}/${costume}`);
  }

  // Ensure output directory exists
  ensureDir(outputDir);

  return new Promise((resolve, reject) => {
    execFile('python', [DECRYPT_SCRIPT, '-o', outputDir, cvpkgPath], {
      timeout: 120000, // 2 minute timeout for decrypt+extract
      maxBuffer: 1024 * 1024, // 1MB stdout buffer
    }, (err, stdout, stderr) => {
      if (err) {
        const msg = stderr || err.message || 'Unknown decrypt error';
        // Clean up partial output on error
        try {
          if (fs.existsSync(outputDir) && fs.readdirSync(outputDir).length === 0) {
            fs.rmdirSync(outputDir);
          }
        } catch {}
        reject(new Error(`Decrypt failed: ${msg}`));
        return;
      }
      resolve({ character, costume, outputDir, stdout });
    });
  });
}

/**
 * Check if a role is already installed (has a .model3.json file).
 * @param {string} roleId - role identifier
 * @param {string} modelsDir - models root directory
 * @returns {boolean}
 */
function isRoleInstalled(roleId, modelsDir = MODELS_DIR) {
  const { character, costume } = parseRoleId(roleId);
  const dir = path.join(modelsDir, character, costume);
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    return false;
  }
  try {
    return fs.readdirSync(dir).some((f) => f.endsWith('.model3.json'));
  } catch {
    return false;
  }
}

/**
 * Download, verify SHA256, and prepare for install.
 * Combines download + optional checksum verification.
 * @returns {Promise<{filePath: string, verified: boolean}>}
 */
async function downloadAndVerify(roleId, url, sha256, onProgress, downloadDir = DOWNLOAD_DIR) {
  const { filePath } = await downloadRole(roleId, url, onProgress, downloadDir);

  let verified = true;
  if (sha256) {
    try {
      verified = await verifyChecksum(filePath, sha256);
      if (!verified) {
        // Remove bad download
        fs.unlinkSync(filePath);
        throw new Error(`SHA256 checksum mismatch for ${roleId}`);
      }
    } catch (err) {
      if (err.message.includes('SHA256')) throw err;
      // If verification throws for other reasons, still continue
      console.warn(`[download-backend] Checksum verification warning: ${err.message}`);
    }
  }

  return { filePath, verified };
}

// ─── Exports ──────────────────────────────────────────────────────────────────
module.exports = {
  // Constants
  MANIFEST_URL,
  DOWNLOAD_DIR,
  MODELS_DIR,
  DECRYPT_SCRIPT,

  // Core functions
  fetchManifest,
  downloadRole,
  decryptAndInstall,
  isRoleInstalled,

  // Extended functions
  downloadAndVerify,
  verifyChecksum,
  parseRoleId,
  formatSize,
  fetchJSON,
  ensureDir,
};
