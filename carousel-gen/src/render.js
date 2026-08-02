#!/usr/bin/env node
// src/render.js — Playwright Chromium headless → PNG 1080×1350

import { chromium } from 'playwright';
import sharp from 'sharp';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveTheme } from '../template/themes.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

export async function render(deck, data, themeName) {
  const theme = resolveTheme(themeName || deck.template || process.env.TEMPLATE || 'blueprint');
  console.error(`[render] template: ${theme.name}`);
  const outDir = path.join(root, 'out', data);
  await fs.mkdir(outDir, { recursive: true });
  
  // Salvar deck.json
  await fs.writeFile(path.join(outDir, 'deck.json'), JSON.stringify(deck, null, 2), 'utf-8');
  
  // Escrever legenda
  const captionLines = [deck.caption || '', '', (deck.hashtags || []).join(' ')].filter(Boolean);
  await fs.writeFile(path.join(outDir, 'legenda.txt'), captionLines.join('\n'), 'utf-8');
  
  // Baixar fontes se necessário
  await downloadFonts();
  
  console.error('[render] Starting Playwright rendering...');
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  
  try {
    const page = await browser.newPage({ 
      viewport: { width: 1080, height: 1350 },
      deviceScaleFactor: 1,
    });
    
    for (let i = 0; i < 9; i++) {
      const pngPath = path.join(outDir, `${String(i + 1).padStart(2, '0')}.png`);
      
      // Criar HTML inline para cada slide (self-contained, sem dependências externas)
      const fontFaces = await buildFontFaces();
      const htmlContent = await inlineImages(buildSlideHtml(deck, i, fontFaces, theme.css), outDir);
      
      await page.setContent(htmlContent, { waitUntil: 'domcontentloaded', timeout: 30000 });
      
      // Esperar fonts carregarem
      await page.evaluate(async () => {
        if (document.fonts && document.fonts.ready) {
          await document.fonts.ready;
        }
        // Give extra render time
        await new Promise(r => setTimeout(r, 1000));
      });
      

      // Ajuste automatico: reduz titulo, corpo, espacamento e altura da
      // imagem ate o conteudo caber nos 1350px. Sem isso o slide corta em
      // silencio quando o texto vem mais longo que o previsto.
      const fit = await page.evaluate(() => {
        const el = document.querySelector('.slide');
        if (!el) return { ok: false, reason: 'slide ausente' };
        const H = 1350;
        const over = () => el.scrollHeight > H + 1;
        const steps = [
          ['--gap',     [1, .9, .8, .7, .6, .5]],
          ['--img-max', ['480px', '420px', '360px', '300px', '250px']],
          ['--lh-body',  [1.4, 1.34, 1.28]],
          ['--fs-body', ['39px', '37px', '35px', '33px', '31px', '29px']],
          ['--fs-h1',   ['172px', '158px', '144px', '130px', '116px', '104px']],
        ];
        const applied = {};
        for (const [varName, values] of steps) {
          for (const v of values) {
            el.style.setProperty(varName, v);
            applied[varName] = v;
            if (!over()) return { ok: true, applied, height: el.scrollHeight };
          }
        }
        return { ok: !over(), applied, height: el.scrollHeight };
      });
      if (!fit.ok) {
        throw new Error(`Slide ${i + 1}: conteudo nao coube em 1350px (${fit.height}px). Encurte os textos no write-copy.`);
      }
      const tuned = Object.entries(fit.applied || {}).filter(([, v]) => v !== 1 && v !== '480px' && v !== '39px' && v !== '172px');
      if (tuned.length) console.error(`[render] slide ${String(i + 1).padStart(2, '0')} ajustado: ${tuned.map(([k, v]) => k + '=' + v).join(' ')}`);

      // Screenshot da pagina inteira
      await page.screenshot({ path: pngPath, type: 'png' });
      
      // Validar dimensões com sharp
      try {
        const meta = await sharp(pngPath).metadata();
        if (meta.width !== 1080 || meta.height !== 1350) {
          throw new Error(`PNG ${i+1} wrong dimensions: ${meta.width}x${meta.height}`);
        }
        console.error(`[render] ✅ Slide ${String(i+1).padStart(2,'0')}: ${meta.width}x${meta.height} (${(meta.size/1024).toFixed(0)}KB)`);
      } catch (err) {
        console.error(`[render] ⚠️ Validation warning: ${err.message}`);
      }
    }
    
    console.error('[render] ✅ All slides rendered successfully');
  } finally {
    await browser.close();
  }
  
  return {
    slides: Array.from({ length: 9 }, (_, i) => `${String(i + 1).padStart(2, '0')}.png`),
    deckPath: path.join(outDir, 'deck.json'),
    legendaPath: path.join(outDir, 'legenda.txt'),
  };
}

function buildSlideHtml(deck, slideIndex, fontFaces, themeCss = '') {
  const s = deck.slides[slideIndex];
  const brand = process.env.BRAND_NAME || 'CHASE AI';
  
  let slideHtml = '';
  
  if (s.type === 'cover') {
    const dateStr = s.date ? s.date.split('-').reverse().join('.') : '30.07.2026';
    const topSpecLeft = (s.specTop || []).map(l => `<span>${escHtml(l)}</span>`).join(' ');
    const topSpecRight = (s.specTopRight || []).map(l => `<span>${escHtml(l)}</span>`).join(' ');
    const botSpecLeft = (s.specBot || []).map(l => `<span>${escHtml(l)}</span>`).join(' ');
    const botSpecRight = (s.specBotRight || []).map(l => `<span>${escHtml(l)}</span>`).join(' ');
    
    let linesHtml = s.lines.map(line => {
      if (line.band) {
        return `<div class="band"><span>${escHtml(line.text)}</span></div>`;
      }
      return `<div>${escHtml(line.text)}</div>`;
    }).join('');
    
    const artImg = s.artImage 
      ? `<img src="${escAttr(s.artImage)}" alt="art">` 
      : `<img src="/placeholder-art.png" alt="art">`;
    
    slideHtml = `
    <div class="slide plain cover">
      <div class="topspec">
        <div class="l">${topSpecLeft}</div>
        <div class="r">${topSpecRight}</div>
      </div>
      <div class="ruler">
        <span>SPECIMEN A — VERIFIED</span>
        <div class="line"></div>
        <span>ISSUE ${dateStr} · #AI-DAILY</span>
      </div>
      ${linesHtml.replace(/<div[^>]*>([\s\S]*?)<\/div>/g, '$1')}
      <div class="art">${artImg}</div>
      ${s.pill ? `<div class="pill"><span class="tri">◆</span><span>${escHtml(s.pill)}</span></div>` : ''}
      <div class="botspec">
        <div class="l">${botSpecLeft}</div>
        <div class="r">${botSpecRight}</div>
      </div>
    </div>`;
  } else {
    const sheetNum = parseInt((s.sheet || '').match(/\d+/)?.[0] || String(slideIndex + 1));
    const sheetLabel = `SHEET ${String(sheetNum).padStart(2,'0')} / 09`;
    const chipLabel = escHtml(s.chip || '');
    const titleEsc = escHtml(s.title || '');
    const figEsc = escHtml(s.fig || '');
    const imageCaption = escHtml(s.imageCaption || '');
    const isLast = slideIndex === 8;
    const swipeLabel = isLast ? '' : 'SWIPE →';
    
    let bodyHtml = '';
    if (Array.isArray(s.body)) {
      bodyHtml = s.body.map(b => `<p>${b}</p>`).join('');
    }
    
    let imageFrame = '';
    if (s.image) {
      imageFrame = `
      <div class="imgframe">
        <img src="${escAttr(s.image)}" alt="fig">
        <div class="k a"></div><div class="k b"></div>
        <div class="k c"></div><div class="k d"></div>
      </div>
      ${imageCaption ? `<p class="imgcap">${imageCaption}</p>` : ''}`;
    }
    
    // Type-specific blocks
    let contentBlock = '';
    
    if (s.type === 'list' && Array.isArray(s.items)) {
      const rows = s.items.map(item => `
      <div class="row">
        <span class="n">${escHtml(item.n || '')}</span>
        <span class="t">${escHtml(item.t || '')}</span>
        <span class="d">${escHtml(item.d || '')}</span>
      </div>`).join('');
      contentBlock = `<div class="listbox">${rows}</div>`;
    }
    
    if (s.type === 'cards' && Array.isArray(s.cards)) {
      const cardsHtml = s.cards.map((c, ci) => `
        <div class="card${(c.style === 'dark' || (c.style == null && ci === 0)) ? ' dark' : ' out'}">
          <p class="lbl">${escHtml(c.lbl || '')}</p>
          <h3>${escHtml(c.h || '')}</h3>
          <p>${escHtml(c.p || '')}</p>
        </div>`).join('');
      
      const parts = cardsHtml.match(/<div class="card[^>]*>[\s\S]*?<\/div>/g);
      if (parts?.length >= 2) {
        contentBlock = `<div class="cards">${parts[0]}<div class="arrow">▸</div>${parts[1]}</div>`;
      }
    }
    
    if (s.type === 'checks' && Array.isArray(s.checks)) {
      const checksHtml = s.checks.map((c, i) => `
      <div class="c">
        <div class="dot">${i + 1}</div>
        <span>${escHtml(c)}</span>
      </div>`).join('');
      contentBlock = `
      <div class="checks">
        <p class="lbl">${escHtml(s.checksLabel || 'CHECKLIST')}</p>
        ${checksHtml}
      </div>`;
    }
    
    const smClass = s.titleSize === 'sm' ? ' sm' : '';
    
    slideHtml = `
    <div class="slide">
      <div class="crop tl"></div><div class="crop tr"></div>
      <div class="crop bl"></div><div class="crop br"></div>
      
      <div class="head">
        <div class="chip">${chipLabel}</div>
        <div class="rule"><div class="line"></div>1080 PX<span class="rev">REV A ▲</span></div>
      </div>
      
      <h1 class="display${smClass}">${titleEsc}</h1>
      
      <div class="figcap">
        <div class="bar-o"></div>
        <p>${figEsc}</p>
      </div>
      
      <div class="body">${bodyHtml}</div>
      
      ${imageFrame}
      
      <div class="spacer"></div>
      
      ${contentBlock}
      
      <div class="foot">
        <span>${sheetLabel}</span>
        <span>${swipeLabel}</span>
      </div>
    </div>`;
  }
  
  // CSS inline completo
  const css = `<style>
${fontFaces}

:root{--orange:#e8481f;--ink:#111111;--paper:#e9e3d6;--paper-2:#efeade;--muted:#8a8578;
      --grid:#1111110d;--grid-major:#11111117;--line:#11111138;--cream:#e9e3d6;--cream-2:#efeade}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--paper)}

.slide{--fs-h1:172px;--fs-body:39px;--lh-body:1.4;--gap:1;--img-max:480px;width:1080px;height:1350px;background:var(--paper);color:var(--ink);position:relative;overflow:hidden;
  background-image:
    linear-gradient(var(--grid-major) 1px,transparent 1px),linear-gradient(90deg,var(--grid-major) 1px,transparent 1px),
    linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:216px 216px,216px 216px,27px 27px,27px 27px;
  padding:76px 76px 60px;display:flex;flex-direction:column;
  font-family:Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}

/* marcas de corte nos 4 cantos */
.crop{display:block;position:absolute;width:34px;height:34px;pointer-events:none}
.crop::before,.crop::after{content:'';position:absolute;background:var(--orange)}
.crop::before{left:50%;top:0;width:2px;height:100%;transform:translateX(-50%)}
.crop::after{top:50%;left:0;height:2px;width:100%;transform:translateY(-50%)}
.crop.tl{top:30px;left:30px}.crop.tr{top:30px;right:30px}
.crop.bl{bottom:30px;left:30px}.crop.br{bottom:30px;right:30px}

.head{display:flex;align-items:center;gap:22px;margin-bottom:0;flex:none}
.chip{background:var(--ink);color:#fff;font:500 21px/1 'IBM Plex Mono',monospace;letter-spacing:.22em;padding:14px 22px}
.rule{flex:1;display:flex;align-items:center;gap:16px;color:var(--muted);font:400 19px 'IBM Plex Mono',monospace;letter-spacing:.2em}
.rule .line{flex:1;height:1px;background:var(--line)}
.rev{color:var(--orange);font:500 21px 'IBM Plex Mono',monospace;letter-spacing:.22em}

h1.display{font-family:Anton,sans-serif;font-weight:400;font-size:var(--fs-h1);line-height:.86;letter-spacing:-.015em;
  text-transform:uppercase;transform:scaleX(.94);transform-origin:left;margin-top:calc(42px * var(--gap));
  max-width:100%;overflow-wrap:anywhere}
h1.display.sm{font-size:calc(var(--fs-h1) * .73)}
h1.display em{font-style:normal;color:var(--orange)}

.figcap{margin-top:calc(34px * var(--gap));flex:none}
.figcap .bar-o{width:620px;height:2px;background:var(--orange);position:relative;margin-bottom:16px}
.figcap .bar-o::before,.figcap .bar-o::after{content:'';position:absolute;top:-7px;width:2px;height:16px;background:var(--orange)}
.figcap .bar-o::before{left:0}.figcap .bar-o::after{right:0}
.figcap p{color:var(--orange);font:400 23px 'IBM Plex Mono',monospace;letter-spacing:.06em}

.body{margin-top:calc(26px * var(--gap));font-size:var(--fs-body);line-height:var(--lh-body);font-weight:600;max-width:930px}
.body + .body{margin-top:calc(22px * var(--gap))}
.body em{font-style:normal;color:var(--orange);font-weight:700}
.body strong{font-weight:800}
.spacer{flex:1;min-height:0}

.imgframe{position:relative;padding:14px;margin-top:calc(30px * var(--gap));flex:none}
.imgframe img{width:100%;max-height:var(--img-max);object-fit:cover;display:block;
  border:1px solid #11111129;background:var(--cream-2)}
.imgframe .k{position:absolute;width:30px;height:30px;border:3px solid var(--orange);display:block}
.imgframe .k.a{top:0;left:0;border-right:0;border-bottom:0}
.imgframe .k.b{top:0;right:0;border-left:0;border-bottom:0}
.imgframe .k.c{bottom:0;left:0;border-right:0;border-top:0}
.imgframe .k.d{bottom:0;right:0;border-left:0;border-top:0}
.imgcap{color:var(--orange);font:400 22px 'IBM Plex Mono',monospace;letter-spacing:.1em;margin-bottom:14px;text-transform:uppercase}

.listbox{border:2px solid var(--ink);background:var(--cream-2);padding:8px 44px;margin-top:calc(24px * var(--gap));flex:none}
.listbox .row{display:flex;gap:34px;align-items:flex-start;padding:calc(30px * var(--gap)) 0;border-bottom:1px solid #11111124}
.listbox .row:last-child{border-bottom:0}
.listbox .n{color:var(--orange);font:400 31px 'IBM Plex Mono',monospace;width:54px;flex:none;padding-top:6px}
.listbox .t{font-family:Anton,sans-serif;font-weight:400;font-size:52px;line-height:1;text-transform:uppercase;width:300px;flex:none}
.listbox .d{font-size:29px;line-height:1.38;color:#5c574c;font-weight:400;flex:1}

.cards{display:grid;grid-template-columns:1fr 88px 1fr;align-items:stretch;margin-top:calc(24px * var(--gap));flex:none}
.card{padding:calc(38px * var(--gap)) 40px;min-height:0}
.card.dark{background:var(--ink);color:#f2ede1;box-shadow:14px 14px 0 #1111111f}
.card.out{border:2px solid var(--orange)}
.card .lbl{font:400 22px 'IBM Plex Mono',monospace;letter-spacing:.2em;margin-bottom:18px;color:var(--orange);text-transform:uppercase}
.card h3{font-family:Anton,sans-serif;font-weight:400;font-size:58px;line-height:1;text-transform:uppercase;margin-bottom:calc(22px * var(--gap))}
.card p{font-size:29px;line-height:1.42;color:#5c574c}
.card.dark p{color:#a9a396}
.arrow{display:flex;align-items:center;justify-content:center;color:var(--orange);font-size:52px}

.checks{display:flex;flex-direction:column;border:2px solid var(--ink);background:var(--cream-2);padding:calc(20px * var(--gap)) 40px;margin-top:calc(24px * var(--gap));flex:none}
.checks .lbl{font:400 22px 'IBM Plex Mono',monospace;letter-spacing:.2em;color:var(--orange);text-transform:uppercase;padding-bottom:calc(18px * var(--gap))}
.checks .c{display:flex;gap:24px;align-items:center;padding:calc(24px * var(--gap)) 0;border-top:1px solid #11111124}
.checks .dot{color:#fff;background:var(--orange);width:42px;height:42px;flex:none;
  display:flex;align-items:center;justify-content:center;font:400 22px 'IBM Plex Mono',monospace}
.checks .c span{font-size:31px;font-weight:600;line-height:1.28}

.foot{display:flex;justify-content:space-between;align-items:center;margin-top:calc(30px * var(--gap));flex:none;color:var(--muted);
  font:400 21px 'IBM Plex Mono',monospace;letter-spacing:.2em}

/* CAPA */
.cover{padding:70px 66px 56px}
.cover .topspec,.cover .botspec{display:flex;justify-content:space-between;
  font:400 20px/1.7 'IBM Plex Mono',monospace;letter-spacing:.13em;color:#3c3a36}
.cover .botspec{margin-top:34px}
.cover .topspec .r,.cover .botspec .r{text-align:right}
.cover .ruler{display:flex;align-items:center;gap:16px;margin:16px 0 30px;color:#3c3a36;font:400 20px 'IBM Plex Mono',monospace;letter-spacing:.2em}
.cover .ruler .line{flex:1;height:1px;background:var(--line);position:relative}
.cover .ruler .line::before,.cover .ruler .line::after{content:'';position:absolute;top:-7px;width:1px;height:15px;background:var(--line)}
.cover .ruler .line::before{left:0}.cover .ruler .line::after{right:0}
.cover h1{font-family:Anton,sans-serif;font-weight:400;text-transform:uppercase;
  font-size:172px;line-height:.9;letter-spacing:-.015em;text-align:center;transform:scaleX(.96)}
.cover .band{background:var(--orange);color:#fff;padding:8px 24px 20px;margin:14px 0;box-shadow:10px 10px 0 #11111121}
.cover .art{flex:1;display:flex;align-items:center;justify-content:center;min-height:0;padding:20px 0}
.cover .art img{max-height:100%;max-width:100%;object-fit:contain}
.cover .pill{border:3px solid var(--ink);border-radius:999px;background:var(--cream-2);
  padding:26px 54px;display:flex;align-items:center;gap:22px;align-self:center;box-shadow:8px 8px 0 #1111111f}
.cover .pill .tri{color:var(--orange);font-size:34px;line-height:1}
.cover .pill span{font-size:44px;font-weight:800;letter-spacing:-.01em}
${themeCss}
</style>`;
  
  return `<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Slide ${slideIndex+1}/9</title>${css}</head><body>${slideHtml}</body></html>`;
}



let _fontFaceCache = null;
async function buildFontFaces() {
  if (_fontFaceCache) return _fontFaceCache;
  const dir = path.join(root, 'template', 'fonts');
  const defs = [
    ['Anton', 'anton-400.woff2', 400],
    ['Inter', 'inter-400.woff2', 400],
    ['Inter', 'inter-600.woff2', 600],
    ['Inter', 'inter-700.woff2', 700],
    ['Inter', 'inter-800.woff2', 800],
    ['IBM Plex Mono', 'mono-400.woff2', 400],
    ['IBM Plex Mono', 'mono-500.woff2', 500],
  ];
  const out = [];
  for (const [family, file, weight] of defs) {
    const buf = await fs.readFile(path.join(dir, file));
    out.push(`@font-face{font-family:'${family}';src:url(data:font/woff2;base64,${buf.toString('base64')}) format('woff2');font-weight:${weight};font-style:normal;font-display:block}`);
  }
  _fontFaceCache = out.join('\n');
  console.error(`[render] ${defs.length} fontes embutidas em base64 (data URI)`);
  return _fontFaceCache;
}

async function inlineImages(html, outDir) {
  const re = /src="([^"]+\.(?:png|jpg|jpeg|webp))"/gi;
  for (const m of [...html.matchAll(re)]) {
    const p = m[1];
    if (p.startsWith('data:')) continue;
    const cands = [path.join(root, p.replace(/^\//, '')), path.resolve(outDir, p), path.resolve(root, p)];
    try {
      let buf = null, abs = null;
      for (const c of cands) { try { buf = await fs.readFile(c); abs = c; break; } catch {} }
      if (!buf) throw new Error('nao achou');
      const ext = path.extname(abs).slice(1).toLowerCase();
      const mime = ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';
      html = html.replace(m[0], `src="data:${mime};base64,${buf.toString('base64')}"`);
    } catch {
      console.error(`[render] imagem ausente, ocultando no slide: ${p}`);
      html = html.replace(m[0], 'src="" style="display:none"');
    }
  }
  return html;
}

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escAttr(str) {
  if (!str) return '';
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function downloadFonts() {
  const fontsDir = path.join(root, 'template', 'fonts');
  await fs.mkdir(fontsDir, { recursive: true });
  const nm = path.join(root, 'node_modules', '@fontsource');
  const map = [
    ['anton/files/anton-latin-400-normal.woff2',                 'anton-400.woff2'],
    ['inter/files/inter-latin-400-normal.woff2',                 'inter-400.woff2'],
    ['inter/files/inter-latin-600-normal.woff2',                 'inter-600.woff2'],
    ['inter/files/inter-latin-700-normal.woff2',                 'inter-700.woff2'],
    ['inter/files/inter-latin-800-normal.woff2',                 'inter-800.woff2'],
    ['inter/files/inter-latin-900-normal.woff2',                 'inter-900.woff2'],
    ['ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2', 'mono-400.woff2'],
    ['ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2', 'mono-500.woff2'],
  ];
  for (const [rel, out] of map) {
    try {
      await fs.copyFile(path.join(nm, rel), path.join(fontsDir, out));
    } catch (e) {
      console.error(`[render] FONTE AUSENTE: ${rel} - ${e.message}`);
      throw new Error(`Fonte obrigatoria ausente: ${rel}. Rode npm install.`);
    }
  }
  console.error(`[render] ${map.length} fontes copiadas de node_modules (sem rede)`);
}