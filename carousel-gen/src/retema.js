#!/usr/bin/env node
// src/retema.js — re-renderiza um carrossel ja gerado com outro template.
//
// O texto (deck.json) e as imagens ficam salvos na pasta de saida, entao
// trocar de tema so refaz o render: ~12s e zero token de LLM.
//
//   npm run retema -- dark              # tema no carrossel de hoje
//   npm run retema -- 2026-07-30 dark   # tema numa data especifica
//   npm run retema -- --lista           # temas disponiveis

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

import { render } from './render.js';
import { THEMES } from '../template/themes.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(root, '.env') });

const nomes = Object.keys(THEMES);

function hoje() {
  const tz = process.env.TIMEZONE || 'America/Sao_Paulo';
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date());
}

async function main() {
  const args = process.argv.slice(2);

  if (args.includes('--lista') || args.length === 0) {
    console.log(`Temas disponiveis: ${nomes.join(', ')}`);
    console.log('Uso: npm run retema -- [data] <tema>');
    process.exit(args.length === 0 ? 2 : 0);
  }

  // O tema e o argumento que bate com um nome conhecido; o outro e a data.
  const tema = args.find(a => nomes.includes(a.toLowerCase()));
  if (!tema) {
    console.error(`Tema invalido. Disponiveis: ${nomes.join(', ')}`);
    process.exit(2);
  }
  const data = args.find(a => a !== tema) || hoje();

  const dir = path.join(root, 'out', data);
  const deckPath = path.join(dir, 'deck.json');
  try {
    await fs.access(deckPath);
  } catch {
    console.error(`Nao achei ${deckPath}. Gere o carrossel primeiro com: npm run link -- "<url>"`);
    process.exit(1);
  }

  const deck = JSON.parse(await fs.readFile(deckPath, 'utf-8'));
  console.log(`Re-renderizando ${data} com o tema "${tema}" (sem chamar LLM)...`);

  const t0 = Date.now();
  await render(deck, data, tema);
  console.log(`Pronto em ${((Date.now() - t0) / 1000).toFixed(1)}s — ${dir}`);
  console.log(`Para publicar:  ./publish.sh ${data} --publish`);
}

main().catch(err => {
  console.error(`FATAL: ${err.message}`);
  process.exit(1);
});
