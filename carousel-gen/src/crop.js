#!/usr/bin/env node
// src/crop.js — baixa e recorta imagens dos artigos com sharp

import sharp from 'sharp';
import https from 'node:https';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

export async function crop(deck, data) {
  const outDir = path.join(root, 'out', data);
  await fs.mkdir(outDir, { recursive: true });

  let imgCounter = 0;

  // Deck é gerado pelo write-copy e não contém URLs de imagem reais.
  // Precisamos buscar a ogImage do candidato original ou do site do artigo.
  // O deck já tem deck.source.url com o URL do artigo → extraímos ogImage dele.
  
  const articleUrl = deck.source?.url;
  if (!articleUrl) {
    console.error('[crop] ⚠️ No source URL in deck — skipping images');
    return { cropsDone: false };
  }

  // Tentar obter ogImage do artigo
  let ogImage = null;
  try {
    const html = await fetchText(articleUrl, 8000);
    ogImage = extractOGImage(html);
  } catch (err) {
    console.error(`[crop] ⚠️ Failed to fetch article for OG: ${err.message}`);
  }

  if (!ogImage) {
    console.error('[crop] ⚠️ No ogImage found — removing all image fields from deck');
    deck.slides.forEach(s => delete s.image);
    return { cropsDone: false };
  }

  // Baixar e recortar ogImage uma vez (usamos em todos os slides que pedem auto)
  try {
    const buffer = await downloadImage(ogImage, 30000);
    const dest = path.join(outDir, 'img-0.png');
    
    // Recortar para 924×520, fit: cover, position: attention
    await sharp(buffer)
      .resize({ width: 924, height: 520, fit: 'cover', position: 'attention' })
      .toFile(dest);
    
    console.error(`[crop] ✅ Saved cropped image to ${dest}`);
    
    // Atualizar deck com caminho relativo
    deck.slides.forEach(s => {
      if (s.image === 'auto') {
        s.image = 'img-0.png';
      }
    });
  } catch (err) {
    console.error(`[crop] ❌ Failed to download/resize image: ${err.message}`);
    deck.slides.forEach(s => delete s.image);
  }

  return { cropsDone: true };
}

function extractOGImage(html) {
  const match = html.match(/(?:property|name)="og:image"(?:\s+content\s*=)"([^"]+)"/i)
    || html.match(/(?:property|name)="twitter:image"(?:\s+content\s*=)"([^"]+)"/i);
  if (match) {
    let img = match[1].trim();
    if (!img.startsWith('http')) img = 'https:' + img;
    return img;
  }
  return null;
}

function downloadImage(url, timeout = 30000) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https') ? https : http;
    const req = client.get(url, { signal: AbortSignal.timeout(timeout) }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return resolve(downloadImage(new URL(res.headers.location, url).href, timeout));
      }
      if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode}`));
      
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    });
    req.on('error', reject);
    req.setTimeout(timeout, () => req.destroy());
  });
}

function fetchText(url, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https') ? https : http;
    const req = client.get(url, { signal: AbortSignal.timeout(timeout) }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.setTimeout(timeout, () => req.destroy());
  });
}