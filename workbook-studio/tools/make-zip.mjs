#!/usr/bin/env node
// Packs the studio — frontend, backend, subtitles, tools — into one zip.
//
//   node tools/make-zip.mjs [outFile]
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, cpSync, existsSync, writeFileSync } from 'node:fs';
import { join, resolve, basename, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const NAME = 'two-cups-workbook-studio';
const out = resolve(process.argv[2] || join(ROOT, 'dist', `${NAME}.zip`));
const stage = join(ROOT, 'dist', '.zip-stage');

rmSync(stage, { recursive: true, force: true });
const pkgDir = join(stage, NAME);
mkdirSync(pkgDir, { recursive: true });

for (const item of ['public', 'tools', 'data/srt', 'server.mjs', 'package.json', 'README.md', 'Dockerfile', '.gitignore']) {
  const from = join(ROOT, item);
  if (existsSync(from)) cpSync(from, join(pkgDir, item), { recursive: true });
}
mkdirSync(join(pkgDir, 'data', 'sheets'), { recursive: true });
writeFileSync(join(pkgDir, 'data', 'sheets', '.gitkeep'), '');

mkdirSync(dirname(out), { recursive: true });
rmSync(out, { force: true });
execFileSync('zip', ['-r', '-q', out, NAME], { cwd: stage });
rmSync(stage, { recursive: true, force: true });
console.log(`wrote ${out}`);
