import fs from 'fs';
import path from 'path';
import { homedir } from 'os';
import { fileURLToPath } from 'url';
import { pbkdf2Sync, createDecipheriv } from 'crypto';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const EXT_HEADER_SIZE = 1024, PAGE_SIZE = 4096, SALT_SIZE = 16;
const KEY_SIZE = 32, IV_SIZE = 16, RESERVE_SIZE = 48;
const KDF_ITERATIONS = 4000, HMAC_MASK = 58;
const SQLITE_HEADER = Buffer.from("SQLite header 3\0");
const SQLITE_FORMAT = Buffer.from("SQLite format 3\0");

function deriveKeys(passphrase, salt) {
  const encKey = pbkdf2Sync(passphrase, salt, KDF_ITERATIONS, KEY_SIZE, "sha512");
  const hmacSalt = Buffer.alloc(salt.length);
  for (let i = 0; i < salt.length; i++) hmacSalt[i] = salt[i] ^ HMAC_MASK;
  return { encKey };
}
function decryptPage(pageData, encKey, skipSalt = 0) {
  const data = pageData.subarray(skipSalt);
  const contentLen = data.length - RESERVE_SIZE;
  const encrypted = data.subarray(0, contentLen);
  const iv = data.subarray(contentLen, contentLen + IV_SIZE);
  const decipher = createDecipheriv("aes-256-cbc", encKey, iv);
  decipher.setAutoPadding(false);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]);
}
function decryptDatabase(fileData, passphrase) {
  if (fileData.length < EXT_HEADER_SIZE + PAGE_SIZE || !fileData.subarray(0,16).equals(SQLITE_HEADER)) return null;
  const scData = fileData.subarray(EXT_HEADER_SIZE);
  const totalPages = Math.floor(scData.length / PAGE_SIZE);
  if (!totalPages) return null;
  const salt = scData.subarray(0, SALT_SIZE);
  const { encKey } = deriveKeys(passphrase, salt);
  const output = Buffer.alloc(totalPages * PAGE_SIZE);
  let offset = 0;
  for (let pgno = 1; pgno <= totalPages; pgno++) {
    const po = (pgno - 1) * PAGE_SIZE;
    const rawPage = scData.subarray(po, po + PAGE_SIZE);
    const decrypted = decryptPage(rawPage, encKey, pgno === 1 ? SALT_SIZE : 0);
    if (pgno === 1) {
      SQLITE_FORMAT.copy(output, 0);
      decrypted.copy(output, 16);
      output.writeUInt16BE(PAGE_SIZE, 16);
      offset = PAGE_SIZE;
    } else {
      decrypted.copy(output, offset);
      offset += PAGE_SIZE;
    }
  }
  return output;
}

export async function plugin_init(ctx) {
  const core = ctx.core || ctx;
  const passphrase = core.dbPassphrase;
  ctx.logger.info("[DBExport] Starting...");

  try {
    // Auto-detect QQ data directory
    const homeQQ = path.join(homedir(), '.config', 'QQ');
    const qqDirs = fs.readdirSync(homeQQ).filter(d => d.startsWith('nt_qq_'));
    if (!qqDirs.length) {
      ctx.logger.error("[DBExport] No QQ data directory found under ~/.config/QQ/");
      return;
    }
    const dbPath = path.join(homeQQ, qqDirs[0], 'nt_db', 'nt_msg.db');
    ctx.logger.info(`[DBExport] Reading ${dbPath}, passphrase=${passphrase.length} bytes`);
    const fileData = fs.readFileSync(dbPath);
    ctx.logger.info(`[DBExport] File size: ${fileData.length}`);

    const decrypted = decryptDatabase(fileData, Buffer.from(passphrase));
    ctx.logger.info(`[DBExport] Decrypted: ${decrypted ? decrypted.length + ' bytes' : 'FAILED'}`);

    if (decrypted) {
      const outPath = path.join(__dirname, 'nt_msg_decrypted.db');
      fs.writeFileSync(outPath, decrypted);
      ctx.logger.info(`[DBExport] Written to ${outPath}`);

      const { DatabaseSync } = await import('node:sqlite');
      const db = new DatabaseSync(outPath, { readOnly: true });
      const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
      ctx.logger.info(`[DBExport] Tables: ${tables.map(r=>r[0]).join(', ')}`);
      db.close();
    }
  } catch (e) {
    ctx.logger.error(`[DBExport] Error: ${e.message}`);
  }
}
export async function plugin_cleanup(ctx) {}
