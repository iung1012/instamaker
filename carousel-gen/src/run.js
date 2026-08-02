#!/usr/bin/env node
// src/run.js — Orquestrador principal: fetch → pick → write → art → crop → render

import { fetchCandidates } from './fetch-news.js';
import { pick } from './pick.js';
import { writeCopy } from './write-copy.js';
import { makeArt } from './make-art.js';
import { crop } from './crop.js';
import { render } from './render.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(root, '.env') });

async function loadSeen() {
  const seenPath = path.join(root, 'state', 'seen.json');
  try {
    const data = await fs.readFile(seenPath, 'utf-8');
    return JSON.parse(data);
  } catch {
    return [];
  }
}

async function saveSeen(seen) {
  await fs.mkdir(path.dirname(path.join(root, 'state', 'seen.json')), { recursive: true });
  await fs.writeFile(path.join(root, 'state', 'seen.json'), JSON.stringify(seen, null, 2), 'utf-8');
}

function log(tag, msg, level = 'info') {
  const prefix = `[${new Date().toISOString()}]`;
  if (level === 'error') {
    console.error(`${prefix} ❌ ${tag}: ${msg}`);
  } else if (level === 'warn') {
    console.warn(`${prefix} ⚠️ ${tag}: ${msg}`);
  } else {
    console.error(`${prefix} ${tag}: ${msg}`);
  }
}

export async function main() {
  log('run', '🚀 Pipeline Carousel Blueprint iniciado');
  
  // Data do dia
  const today = new Date();
  const dateStr = today.toISOString().split('T')[0]; // AAAA-MM-DD
  const outDir = path.join(root, 'out', dateStr);
  
  // Etapa 1: Fetch
  log('run', '📡 ETAPA 1 — Coletando notícias...');
  let candidates;
  try {
    candidates = await fetchCandidates();
  } catch (err) {
    log('run', `FATAL: Falha no fetch: ${err.message}`, 'error');
    process.exit(1);
  }
  
  // Filtrar já vistas
  const seenUrls = await loadSeen();
  const fresh = candidates.filter(c => !seenUrls.includes(c.url));
  log('run', `✅ ${candidates.length} candidatos, ${fresh.length} novos (excluídos ${candidates.length - fresh.length})`);
  
  if (fresh.length === 0) {
    log('run', '⏭️ Nenhuma notícia nova hoje — saindo sem erro (já rodou)', 'warn');
    process.exit(0);
  }
  
  // Etapa 2: Pick
  log('run', '🎯 ETAPA 2 — Escolhendo melhor notícia...');
  let picked;
  try {
    picked = await pick(fresh);
  } catch (err) {
    log('run', `FATAL: Pick falhou: ${err.message}`, 'error');
    process.exit(1);
  }
  log('run', `📰 "${picked.candidate.title.substring(0, 80)}" (${picked.candidate.source}, ${picked.candidate.points} pts)`);
  
  // Etapa 3: Copy
  log('run', '✍️ ETAPA 3 — Gerando deck.json...');
  let deck;
  try {
    deck = await writeCopy(picked);
  } catch (err) {
    log('run', `FATAL: Copy falhou: ${err.message}`, 'error');
    process.exit(1);
  }
  log('run', `✅ Deck gerado com ${deck.slides?.length || 0} slides`);
  
  // Etapa 4: Art
  log('run', '🎨 ETAPA 4 — Gerando arte dos personagens...');
  try {
    await makeArt(deck, dateStr);
  } catch (err) {
    log('run', `⚠️ Arte falhou (não crítico): ${err.message}`, 'warn');
  }
  
  // Etapa 5: Crop
  log('run', '🖼️ ETAPA 5 — Baixando e recortando imagens...');
  try {
    await crop(deck, dateStr);
  } catch (err) {
    log('run', `⚠️ Crop falhou (não crítico): ${err.message}`, 'warn');
  }
  
  // Adicionar artImage ao cover se disponível
  const artImgPath = path.join(outDir, 'art-0.png');
  try {
    await fs.access(artImgPath);
    const relPath = `../out/${dateStr}/art-0.png`;
    deck.slides[0].artImage = relPath;
    log('run', '✅ Image art-linkada para capa');
  } catch {}
  
  // Etapa 6: Render
  log('run', '📸 ETAPA 6 — Renderizando PNGs...');
  let result;
  try {
    result = await render(deck, dateStr);
  } catch (err) {
    log('run', `FATAL: Render falhou: ${err.message}`, 'error');
    process.exit(1);
  }
  
  // Marcar como visto
  seenUrls.push(picked.candidate.url);
  await saveSeen(seenUrls.slice(-100)); // manter apenas últimas 100
  
  log('run', `✅ PIPELINE COMPLETO! Saída: ${outDir}/`);
  log('run', `   PNGs: ${result.slides.join(', ')}`);
  log('run', `   Deck: ${result.deckPath}`);
  log('run', `   Legenda: ${result.legendaPath}`);
  
  return { date: dateStr, path: outDir, result };
}

// Rodar se executado diretamente
if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    await main();
    process.exit(0);
  } catch (err) {
    log('run', `CRASH FATAL: ${err.stack || err.message}`, 'error');
    process.exit(1);
  }
}