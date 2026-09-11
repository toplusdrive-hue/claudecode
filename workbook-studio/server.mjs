#!/usr/bin/env node
/**
 * Two Cups Workbook Studio — backend.
 *
 * Serves the studio in public/ and keeps two folders for it:
 *   data/srt/     subtitle files, shared by everyone who opens the studio
 *   data/sheets/  worksheets, saved as you edit them
 *
 * No dependencies: Node 18+ and `node server.mjs` is the whole story.
 *
 *   GET    /api/health                 { ok, episodes, sheets }
 *   GET    /api/episodes               [ { code, file, srt } ]
 *   GET    /api/episodes/:code         the raw subtitle text
 *   PUT    /api/episodes/:code         body = subtitle text, saves data/srt/:code.srt
 *   DELETE /api/episodes/:code
 *   GET    /api/sheets                 [ { code, title, savedAt } ]
 *   GET    /api/sheets/:code           the saved worksheet
 *   PUT    /api/sheets/:code           body = worksheet JSON
 *   DELETE /api/sheets/:code
 */
import { createServer } from 'node:http';
import { readFile, writeFile, readdir, mkdir, unlink, stat } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { extname, join, resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('.', import.meta.url)));
const PUBLIC = join(ROOT, 'public');
const SRT_DIR = process.env.SRT_DIR ? resolve(process.env.SRT_DIR) : join(ROOT, 'data', 'srt');
const SHEET_DIR = process.env.SHEET_DIR ? resolve(process.env.SHEET_DIR) : join(ROOT, 'data', 'sheets');
const PORT = Number(process.env.PORT || 8080);
const HOST = process.env.HOST || '0.0.0.0';
const MAX_BODY = 8 * 1024 * 1024;

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.srt': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon',
  '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.woff2': 'font/woff2'
};
/** A code names one file in our own folders — letters, digits, dot, dash, underscore. */
const isCode = (s) => typeof s === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(s) && !s.includes('..');

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', ...headers });
  res.end(body);
};
const sendJson = (res, status, value) =>
  send(res, status, JSON.stringify(value), { 'Content-Type': MIME['.json'] });

function readBody(req) {
  return new Promise((ok, fail) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { fail(Object.assign(new Error('too large'), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => ok(Buffer.concat(chunks).toString('utf8')));
    req.on('error', fail);
  });
}

async function listEpisodes(withText) {
  const files = (await readdir(SRT_DIR).catch(() => [])).filter((f) => /\.(srt|vtt)$/i.test(f)).sort();
  return Promise.all(files.map(async (file) => {
    const code = basename(file, extname(file));
    const row = { code, file };
    if (withText) row.srt = (await readFile(join(SRT_DIR, file), 'utf8')).replace(/^﻿/, '').replace(/\r\n/g, '\n').trim();
    return row;
  }));
}
async function listSheets() {
  const files = (await readdir(SHEET_DIR).catch(() => [])).filter((f) => f.endsWith('.json')).sort();
  return Promise.all(files.map(async (file) => {
    const path = join(SHEET_DIR, file);
    const [raw, info] = await Promise.all([readFile(path, 'utf8'), stat(path)]);
    let title = '';
    try { title = (JSON.parse(raw).meta || {}).title || ''; } catch (e) { /* keep the name only */ }
    return { code: basename(file, '.json'), title, savedAt: info.mtime.toISOString() };
  }));
}

async function api(req, res, path) {
  const [, , section, code] = path.split('/');           // /api/<section>/<code>
  const method = req.method;

  if (section === 'health' && method === 'GET') {
    const [eps, sheets] = await Promise.all([listEpisodes(false), listSheets()]);
    return sendJson(res, 200, { ok: true, episodes: eps.length, sheets: sheets.length });
  }

  if (section === 'episodes') {
    if (method === 'GET' && !code) return sendJson(res, 200, await listEpisodes(true));
    if (!code) return sendJson(res, 405, { error: 'method not allowed' });
    if (!isCode(code)) return sendJson(res, 400, { error: 'bad episode code' });
    const file = join(SRT_DIR, code + '.srt');
    if (method === 'GET') {
      const text = await readFile(file, 'utf8').catch(() => null);
      return text == null ? sendJson(res, 404, { error: 'no such episode' }) : send(res, 200, text);
    }
    if (method === 'PUT') {
      const body = await readBody(req);
      if (!body.trim()) return sendJson(res, 400, { error: 'empty subtitle' });
      await mkdir(SRT_DIR, { recursive: true });
      await writeFile(file, body.replace(/\r\n/g, '\n').trim() + '\n');
      return sendJson(res, 200, { code, saved: true });
    }
    if (method === 'DELETE') {
      await unlink(file).catch(() => null);
      return sendJson(res, 200, { code, deleted: true });
    }
    return sendJson(res, 405, { error: 'method not allowed' });
  }

  if (section === 'sheets') {
    if (method === 'GET' && !code) return sendJson(res, 200, await listSheets());
    if (!code) return sendJson(res, 405, { error: 'method not allowed' });
    if (!isCode(code)) return sendJson(res, 400, { error: 'bad worksheet code' });
    const file = join(SHEET_DIR, code + '.json');
    if (method === 'GET') {
      const text = await readFile(file, 'utf8').catch(() => null);
      return text == null ? sendJson(res, 404, { error: 'no saved worksheet' }) : send(res, 200, text, { 'Content-Type': MIME['.json'] });
    }
    if (method === 'PUT') {
      const body = await readBody(req);
      let parsed;
      try { parsed = JSON.parse(body); } catch (e) { return sendJson(res, 400, { error: 'worksheet is not valid JSON' }); }
      if (!parsed || typeof parsed !== 'object' || !parsed.blocks) return sendJson(res, 400, { error: 'worksheet has no blocks' });
      await mkdir(SHEET_DIR, { recursive: true });
      await writeFile(file, JSON.stringify(parsed, null, 1));
      return sendJson(res, 200, { code, saved: true });
    }
    if (method === 'DELETE') {
      await unlink(file).catch(() => null);
      return sendJson(res, 200, { code, deleted: true });
    }
    return sendJson(res, 405, { error: 'method not allowed' });
  }

  return sendJson(res, 404, { error: 'no such endpoint' });
}

async function serveStatic(res, path) {
  const rel = path === '/' ? 'index.html' : decodeURIComponent(path).replace(/^\/+/, '');
  const file = resolve(PUBLIC, rel);
  if (!file.startsWith(PUBLIC)) return send(res, 403, 'Forbidden');
  const info = await stat(file).catch(() => null);
  if (!info || !info.isFile()) return send(res, 404, 'Not found');
  res.writeHead(200, {
    'Content-Type': MIME[extname(file).toLowerCase()] || 'application/octet-stream',
    'Content-Length': info.size,
    'Cache-Control': 'no-cache'
  });
  createReadStream(file).pipe(res);
}

const server = createServer(async (req, res) => {
  const path = (req.url || '/').split('?')[0];
  try {
    if (path.startsWith('/api/')) return await api(req, res, path);
    if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, 'Method not allowed');
    return await serveStatic(res, path);
  } catch (err) {
    const status = err && err.status ? err.status : 500;
    console.error(req.method, path, '→', err && err.message);
    if (!res.headersSent) sendJson(res, status, { error: status === 413 ? 'body too large' : 'server error' });
  }
});

await mkdir(SRT_DIR, { recursive: true });
await mkdir(SHEET_DIR, { recursive: true });
server.listen(PORT, HOST, async () => {
  const eps = await listEpisodes(false);
  console.log(`Two Cups Workbook Studio → http://localhost:${PORT}`);
  console.log(`  subtitles  ${SRT_DIR} (${eps.length} file${eps.length === 1 ? '' : 's'})`);
  console.log(`  worksheets ${SHEET_DIR}`);
});
