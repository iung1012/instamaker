#!/usr/bin/env node
// src/read-link.js — le o conteudo de um link e devolve no formato que o
// writeCopy() ja espera ({ candidate, reason }). Cobre dois casos:
//   1. Post do X.com / Twitter  -> metadados via yt-dlp + frame do video
//   2. Qualquer outra pagina    -> titulo + texto principal do HTML

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

const YTDLP = process.env.YTDLP_BIN || '/usr/local/bin/yt-dlp';
const FFMPEG = process.env.FFMPEG_BIN || '/usr/bin/ffmpeg';
const FFPROBE = process.env.FFPROBE_BIN || '/usr/bin/ffprobe';

const isX = (u) => {
  try {
    const h = new URL(u).hostname.replace(/^www\./i, "").toLowerCase();
    return h === "x.com" || h === "twitter.com" || h.endsWith(".x.com") || h.endsWith(".twitter.com");
  } catch { return false; }
};

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36';

function stripHtml(html) {
  return html
    .replace(/<(script|style|noscript|svg|nav|header|footer|aside)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h[1-6]|section|article)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n+/g, '\n')
    .trim();
}

function metaTag(html, prop) {
  const re = new RegExp(`<meta[^>]+(?:property|name)=["']${prop}["'][^>]*content=["']([^"']+)["']`, 'i');
  const alt = new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']${prop}["']`, 'i');
  return (html.match(re) || html.match(alt) || [])[1] || null;
}

// --------------------------------------------------------------------
// X.com: metadados + frame representativo do video
// --------------------------------------------------------------------
async function readX(url, outDir) {
  let meta = {};
  try {
    const { stdout } = await execFileAsync(YTDLP, ['-J', '--no-warnings', '--no-playlist', url],
      { maxBuffer: 32 * 1024 * 1024, timeout: 120000 });
    meta = JSON.parse(stdout);
  } catch (e) {
    throw new Error(`yt-dlp nao leu o post do X: ${e.message.split('\n')[0]}`);
  }

  const author = meta.uploader || meta.channel || meta.uploader_id || 'X';
  const text = (meta.description || meta.title || '').trim();
  if (!text) throw new Error('post do X sem texto legivel');

  let framePath = null;
  if (meta.duration || meta.formats?.length) {
    try {
      framePath = await grabFrame(url, outDir, meta.duration);
    } catch (e) {
      console.error(`[link] frame do video falhou (segue sem imagem): ${e.message}`);
    }
  }

  return {
    candidate: {
      title: text.split('\n')[0].slice(0, 200),
      summary: text,
      url,
      source: `@${String(author).replace(/^@/, '')}`,
      points: 0,
    },
    reason: 'link enviado pelo usuario',
    framePath,
  };
}

// Baixa o video e extrai um frame representativo. Evita o primeiro
// segundo (costuma ser preto/fade) e prefere ~25% da duracao.
async function grabFrame(url, outDir, durationHint) {
  await fs.mkdir(outDir, { recursive: true });
  const videoPath = path.join(outDir, 'source-video.mp4');

  await execFileAsync(YTDLP, [
    '-f', 'mp4/best', '--no-warnings', '--no-playlist',
    '-o', videoPath, url,
  ], { timeout: 300000, maxBuffer: 16 * 1024 * 1024 });

  let duration = Number(durationHint) || 0;
  if (!duration) {
    const { stdout } = await execFileAsync(FFPROBE, [
      '-v', 'error', '-show_entries', 'format=duration',
      '-of', 'default=noprint_wrappers=1:nokey=1', videoPath,
    ], { timeout: 30000 });
    duration = Number(stdout.trim()) || 0;
  }

  const at = duration > 4 ? Math.min(duration * 0.25, duration - 1) : Math.max(duration / 2, 0.5);
  const framePath = path.join(outDir, 'frame.png');
  await execFileAsync(FFMPEG, [
    '-y', '-ss', String(at.toFixed(2)), '-i', videoPath,
    '-frames:v', '1', '-vf', 'scale=1080:-2', framePath,
  ], { timeout: 120000 });

  const st = await fs.stat(framePath);
  if (st.size < 2000) throw new Error('frame extraido veio vazio');
  console.error(`[link] frame extraido em ${at.toFixed(1)}s de ${duration.toFixed(1)}s`);
  return framePath;
}

// --------------------------------------------------------------------
// Pagina comum: titulo + corpo
// --------------------------------------------------------------------
async function readPage(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  let html;
  try {
    const res = await fetch(url, { signal: controller.signal, headers: { 'User-Agent': UA } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    html = await res.text();
  } finally {
    clearTimeout(timer);
  }

  const title = metaTag(html, 'og:title')
    || (html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1]
    || url;
  const desc = metaTag(html, 'og:description') || metaTag(html, 'description') || '';
  const site = metaTag(html, 'og:site_name') || new URL(url).hostname.replace(/^www\./, '');

  const article = (html.match(/<article[\s\S]*?<\/article>/i) || [])[0] || html;
  const body = stripHtml(article).slice(0, 12000);
  if (body.length < 200 && !desc) throw new Error('nao consegui extrair texto util da pagina');

  return {
    candidate: {
      title: stripHtml(title).slice(0, 200),
      summary: body.length > 200 ? body : desc,
      url,
      source: site,
      points: 0,
    },
    reason: 'link enviado pelo usuario',
    framePath: null,
  };
}

export async function readLink(url, dateStr) {
  const u = String(url || '').trim();
  if (!/^https?:\/\//i.test(u)) throw new Error(`URL invalida: ${u}`);
  const outDir = path.join(root, 'out', dateStr);
  console.error(`[link] lendo ${isX(u) ? 'post do X' : 'pagina'}: ${u}`);
  const result = isX(u) ? await readX(u, outDir) : await readPage(u);
  console.error(`[link] "${result.candidate.title.slice(0, 70)}" (${result.candidate.source}) — ${result.candidate.summary.length} chars${result.framePath ? ' + frame' : ''}`);
  return result;
}
