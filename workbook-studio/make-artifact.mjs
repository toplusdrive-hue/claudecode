#!/usr/bin/env node
// Strips index.html down to the body-only form the Artifact publisher expects
// (it supplies its own <!doctype>, <head> and <body>).
//
//   node make-artifact.mjs [outFile]
import { readFileSync, writeFileSync } from 'node:fs';

const src = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
const out = process.argv[2] || new URL('./artifact.html', import.meta.url).pathname;

const body = src
  .replace(/^[\s\S]*?<title>/, '<title>')
  .replace(/<\/head>\s*<body>/, '')
  .replace(/<\/body>\s*<\/html>\s*$/, '');

writeFileSync(out, body);
console.log(`wrote ${out} (${(Buffer.byteLength(body) / 1024).toFixed(0)} KB)`);
