#!/usr/bin/env node
// Strips index.html down to the body-only form the Artifact publisher expects
// (it supplies its own <!doctype>, <head> and <body>).
//
//   node make-artifact.mjs [outFile]
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const src = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const out = process.argv[2] || new URL('../dist/artifact.html', import.meta.url).pathname;

const body = src
  .replace(/^[\s\S]*?<title>/, '<title>')
  .replace(/<\/head>\s*<body>/, '')
  .replace(/<\/body>\s*<\/html>\s*$/, '');

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, body);
console.log(`wrote ${out} (${(Buffer.byteLength(body) / 1024).toFixed(0)} KB)`);
