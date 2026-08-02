#!/usr/bin/env node
// src/run-link.js — pipeline a partir de UM link enviado pelo usuario.
// Substitui as etapas de fetch/pick por leitura direta da URL.
// Uso: npm run link -- "https://x.com/..."

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

import { readLink } from './read-link.js';
import { writeCopy } from './write-copy.js';
import { crop } from './crop.js';
import { render } from './render.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(root, '.env') });

const log = (step, msg) => console.log(`[${new Date().toISOString()}] ${step}: ${msg}`);

function today() {
  const tz = process.env.TIMEZONE || 'America/Sao_Paulo';
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date());
}

async function main() {
  const args = process.argv.slice(2);
  const url = args.find(a => !a.startsWith('--'));
  const tplArg = args.find(a => a.startsWith('--template='));
  const template = tplArg ? tplArg.split('=')[1] : (process.env.TEMPLATE || 'blueprint');
  if (!url) {
    console.error('Uso: npm run link -- "<url>" [--template=blueprint|dark]');
    process.exit(2);
  }

  const dateStr = today();
  const outDir = path.join(root, 'out', dateStr);
  await fs.mkdir(outDir, { recursive: true });

  log('link', `pipeline iniciado para ${url} (template: ${template})`);

  // 1. Ler o link
  let read;
  try {
    read = await readLink(url, dateStr);
  } catch (err) {
    log('link', `FATAL: leitura do link falhou: ${err.message}`);
    process.exit(1);
  }

  // 2. Gerar o deck
  let deck;
  try {
    deck = await writeCopy({ candidate: read.candidate, reason: read.reason });
  } catch (err) {
    log('copy', `FATAL: geracao do deck falhou: ${err.message}`);
    process.exit(1);
  }
  log('copy', `deck com ${deck.slides?.length || 0} slides`);

  // 3. Frame do video vira a imagem de destaque da capa e do slide 2
  if (read.framePath) {
    const rel = path.basename(read.framePath);
    const cover = deck.slides?.find(s => s.type === 'cover');
    if (cover) cover.artImage = rel;
    const second = deck.slides?.[1];
    if (second && !second.image) second.image = rel;
    log('frame', `frame aplicado na capa${second ? ' e no slide 02' : ''}`);
  } else {
    log('frame', 'sem frame de video — slides seguem so com texto');
  }

  // 4. Baixar/recortar as demais imagens (nao critico)
  try {
    await crop(deck, dateStr);
  } catch (err) {
    log('crop', `aviso: ${err.message}`);
  }

  // 5. Render
  try {
    await render(deck, dateStr, template);
  } catch (err) {
    log('render', `FATAL: ${err.message}`);
    process.exit(1);
  }

  log('link', `pronto: ${outDir}`);
  log('link', 'PNGs: 01.png ... 09.png + legenda.txt');
}

main().catch(err => {
  console.error(`[link] FATAL inesperado: ${err.stack || err.message}`);
  process.exit(1);
});
