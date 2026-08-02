#!/usr/bin/env node
// src/write-copy.js — LLM → deck.json em PT-BR + validação

import dotenv from 'dotenv';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

// --- VALIDAÇÃO ---
function validateDeck(deck) {
  const errors = [];

  // Cap constraints
  const cover = deck.slides[0];
  if (cover?.type !== 'cover') errors.push('Slide 1 deve ser type "cover"');
  else {
    if (!cover.lines || cover.lines.length !== 3) errors.push('Cover precisa exatamente 3 lines');
    else {
      let bandCount = 0;
      for (const l of cover.lines) {
        if (l.text.length > 14) errors.push(`Cap line "${l.text}" excede 14 chars`);
        if (l.band) bandCount++;
      }
      if (bandCount !== 1) errors.push('Cover precisa exatamente 1 line com band:true');
    }
    if (cover.pill && cover.pill.length > 45) errors.push(`Pill ${cover.pill.length} chars > 45`);
  }

  // Data slides
  const dataSlides = deck.slides.slice(1, -1);
  const lastSlide = deck.slides[deck.slides.length - 1];
  if (lastSlide.type !== 'text' || !lastSlide.title?.includes('ME-SEGUE')) {
    errors.push('Último slide deve ser type "text" com ME-SEGUE no title');
  }

  for (let i = 1; i < deck.slides.length; i++) {
    const s = deck.slides[i];
    if (s.chip && s.chip.length > 14) errors.push(`Slide ${i+1} chip "${s.chip}" excede 14 chars`);
    if (s.title && !s.title.startsWith('/')) errors.push(`Slide ${i+1} title "${s.title}" não começa com /`);
    if (s.title && s.title.length > 13) errors.push(`Slide ${i+1} title "${s.title}" excede 13 chars (${s.title.length})`);
    
    // body validation
    const bodyArr = Array.isArray(s.body) ? s.body : [];
    for (const para of bodyArr) {
      if (para.length > 180) errors.push(`Slide ${i+1} body excede 180 chars (${para.length})`);
      if (!para.includes('<em>')) errors.push(`Slide ${i+1} corpo sem <em>`);
    }
    
    // items list
    if (s.items && s.items.length !== 4) errors.push(`Slide ${i+1} tem ${s.items.length} items (precisa 4)`);
    if (s.items) {
      for (const it of s.items) {
        if (it.t.length > 10) errors.push(`Item "${it.t}" excede 10 chars`);
        if (it.d.length > 70) errors.push(`Item desc "${it.d}" excede 70 chars`);
      }
    }
    
    // cards
    if (s.cards && s.cards.length !== 2) errors.push(`Slide ${i+1} tem ${s.cards.length} cards (precisa 2)`);
    if (s.cards) {
      for (const c of s.cards) {
        if (c.h && c.h.length > 12) errors.push(`Card h "${c.h}" excede 12 chars`);
        if (c.p && c.p.length > 110) errors.push(`Card p "${c.p}" excede 110 chars`);
      }
    }
    
    // checks
    if (s.checks && s.checks.length !== 4) errors.push(`Slide ${i+1} tem ${s.checks.length} checks (precisa 4)`);
    if (s.checks) {
      for (const ch of s.checks) {
        if (ch.length > 28) errors.push(`Check "${ch}" excede 28 chars`);
      }
    }
  }

  // caption/hashtags
  if (!deck.caption || deck.caption.split('\n').length < 3) errors.push('Caption precisa 3+ linhas');
  if (!deck.hashtags || deck.hashtags.length < 8 || deck.hashtags.length > 12) {
    errors.push(`Hashtags: ${deck.hashtags?.length ?? 0}, precisa 8-12`);
  }

  return errors;
}


// Chamada em streaming ao endpoint OpenAI-compativel.
// Acumula content e reasoning separadamente: content e a resposta,
// reasoning e so o rascunho (usado apenas se content vier vazio).
async function llmStream({ prompt, maxTokens = 8000, temperature = 0.5, label = 'llm' }) {
  const res = await fetch(`${process.env.LLM_BASE_URL}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${process.env.LLM_API_KEY}`,
      'Accept': 'text/event-stream',
      'User-Agent': 'curl/8.5.0',
    },
    body: JSON.stringify({
      model: process.env.LLM_MODEL || 'qwen3.6-35b-a3b',
      messages: [{ role: 'user', content: prompt }],
      temperature,
      max_tokens: maxTokens,
      stream: true,
    }),
  });

  if (!res.ok) {
    const errText = await res.text();
    const err = new Error(`LLM HTTP ${res.status}: ${errText.substring(0, 300)}`);
    err.status = res.status;
    throw err;
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let content = '';
  let reasoning = '';
  const t0 = Date.now();

  for await (const chunk of res.body) {
    buffer += decoder.decode(chunk, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const raw of lines) {
      const line = raw.trim();
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (payload === '[DONE]') continue;
      let d;
      try { d = JSON.parse(payload); } catch { continue; }
      const delta = d?.choices?.[0]?.delta || {};
      if (typeof delta.content === 'string') content += delta.content;
      const r = delta.reasoning ?? delta.reasoning_content;
      if (typeof r === 'string') reasoning += r;
    }
  }

  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`[${label}] stream: ${content.length} chars de content, ${reasoning.length} de reasoning em ${secs}s`);
  return content.trim() ? content : reasoning;
}

export async function writeCopy(chosenArticle) {
  const { candidate, reason } = chosenArticle;
  const today = new Date().toISOString().split('T')[0];

  // Tenta extrair texto completo do artigo
  let fullText = null;
  try {
    console.log('[copy] Tentando extrair conteúdo do artigo...');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(candidate.url, { signal: controller.signal });
    clearTimeout(timer);
    if (res.ok) {
      const html = await res.text();
      // Remove scripts/styles, pega texto principal
      const textOnly = html
        .replace(/<script[\s\S]*?<\/script>/gi, '')
        .replace(/<style[\s\S]*?<\/style>/gi, '')
        .replace(/<[^>]+>/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
      if (textOnly.length > 200) {
        fullText = textOnly.substring(0, 2000);
        console.log(`[copy] Artigo extraído: ${fullText.length} chars`);
      }
    }
  } catch {
    console.log('[copy] Não conseguiu extrair artigo completo, usando summary');
  }

  const maxAttempts = 3;
  let lastErrors = [];

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    console.log(`[copy] LLM call attempt ${attempt}/${maxAttempts}...`);

    const prompt = `Você é um redator técnico especializado em carrosséis de IA sobre tecnologia. Escreva UM carrossel de 9 slides em PORTUGUÊS DO BRASIL, tom direto e techie.

NOTÍCIA ESCOLHIDA:
Título: ${candidate.title}
Fonte: ${candidate.source}
Summary: ${candidate.summary}
URL: ${candidate.url}
Motivo da escolha: ${reason}
${fullText ? `\nCONTEÚDO COMPLETO:\n${fullText}` : ''}

REGRAS CRÍTICAS DE FORMATAÇÃO (RESPEITE CADA UMA):
1. deck.json EXATO neste schema:
{
  "date": "${today}",
  "source": {"title": "...", "url": "...", "outlet": "..."},
  "caption": "legenda Instagram 3-5 linhas com CTA, PT-BR",
  "hashtags": ["#ia", "#inteligenciaartificial"], // 8 a 12 hashtags
  "slides": [
    {
      "type": "cover",
      "specTop": ["GRID SYSTEM 2.0", "UNIT: PX · SNAP: ON"],
      "specTopRight": ["VERSION 5.0", "DATE: DD.MM.YYYY", "BY: CHASE AI"],
      "lines": [{"text":"LINHA 1"},{"text":"DESTAQUE","band":true},{"text":"LINHA 3"}],
      // cada linha máx 14 chars, exatamente 3 lines, exatamente 1 band:true
      "artPrompt": "descrição breve dos personagens (voxel LEGO) para a cena",
      "pill": "frase de gancho máx 45 chars",
      "specBot": ["TECHNICAL SPECS","...","...","..."],
      "specBotRight": ["LAYOUT NOTES","1. ...","2. ...","3. ..."]
    },
    { "type": "text", "chip": "O CONTEXTO", "title": "/O-QUE-MUDOU", "body": ["parágrafo com <em>texto importante</em>"], "fig": "FIG.1 — descrição minúsculas", "image": "auto", "sheet": "SHEET 02 / 09" },
    // … continue até SHEET 09 / 09. Tipos disponíveis: text, list, cards, checks
    // Slide 9 (último): type "text", title "/ME-SEGUE", chip "OUTRO", com CTA follow. Sem SWIPE→.
  ]
}

REGRAS DE TEXTO — VIOLAÇÕES SÃO REPROVADAS AUTOMATICAMENTE:
- titles: CAIXA ALTA começando com /, máx 13 chars TOTAL incluindo barra
- chip: máx 14 chars, caixa alta
- body: 1-2 parágrafos, cada máx 180 chars, SEMPRE ter <em> por slide
- fig: sempre FIG.N — descrição minúsculas
- items[].t: máx 10 chars · d: máx 70 chars
- cards[].h: máx 12 chars · p: máx 110 chars
- checks[]: máx 28 chars cada
- pill capa: máx 45 chars
- Cap lines: máx 14 chars cada
- Português BR, sem "revolucionário", sem "game changer", sem emoji no corpo

Responda APENAS com o JSON, sem markdown, sem explicação.`;

    let text = '';
    try {
      text = await llmStream({ prompt, maxTokens: 24000, temperature: 0.5, label: 'copy' });
    } catch (err) {
      console.warn(`[copy] ${err.message.substring(0, 300)}`);
      const retriable = err.status === 429 || err.status === 504 || err.status >= 500;
      if (retriable && attempt < maxAttempts) {
        await new Promise(r => setTimeout(r, 3000 * attempt));
        continue;
      }
      throw err;
    }
    console.log(`[copy] Response type: ${typeof text}, length: ${text.length} chars`);

    if (!text.trim()) {
      if (attempt < maxAttempts) { await new Promise(r => setTimeout(r, 2000 * attempt)); continue; }
      throw new Error('All attempts returned empty response');
    }

    // Extrai JSON do texto (remove markdown code blocks se houver)
    let jsonStr = text.trim();
    const codeBlockMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (codeBlockMatch) jsonStr = codeBlockMatch[1].trim();
    else jsonStr = jsonStr.match(/\{[\s\S]*\}/)?.[0] || jsonStr;

    let deck;
    try {
      deck = JSON.parse(jsonStr);
    } catch (parseErr) {
      console.warn(`[copy] Parse error attempt ${attempt}:`, parseErr.message);
      if (attempt < maxAttempts) {
        await new Promise(r => setTimeout(r, 2000 * attempt));
        continue;
      }
      throw new Error(`Failed to parse deck JSON after ${maxAttempts} attempts`);
    }

    // Valida contra regras
    lastErrors = validateDeck(deck);
    if (lastErrors.length === 0) {
      console.log('[copy] ✅ Validation passed on attempt ' + attempt);
      return deck;
    }

    console.log(`[copy] ⚠️ Validation errors (${lastErrors.length}):`, lastErrors.join('; '));

    if (attempt < maxAttempts) {
      // Reenvia ao LLM com erros corrigidos
      const retryPrompt = `ERROS DE VALIDAÇÃO na resposta anterior:
${lastErrors.join('\n')}
Corrigindo AGORA. Responda apenas com o deck.json corrigido. Mantenha toda a estrutura.`;
      
      const retryRes = await fetch(`${process.env.LLM_BASE_URL}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.LLM_API_KEY}`,
        },
        body: JSON.stringify({
          model: process.env.LLM_MODEL || 'qwen3.6-35b-a3b',
          messages: [
            { role: 'user', content: prompt },
            { role: 'assistant', content: jsonStr },
            { role: 'user', content: retryPrompt },
          ],
          temperature: 0.5,
          max_tokens: 24000,
        }),
      });
      
      if (retryRes.ok) {
        const retryData = await retryRes.json();
        // content e a resposta; reasoning e so o rascunho do modelo
        const retryText = retryData?.choices?.[0]?.message?.content 
                       || retryData?.choices?.[0]?.message?.reasoning 
                       || '';
        console.log(`[copy] Retry response: ${retryText.length} chars`);
        
        let retryJsonStr = retryText.trim();
        const rbm = retryJsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
        if (rbm) retryJsonStr = rbm[1].trim();
        else retryJsonStr = retryJsonStr.match(/\{[\s\S]*\}/)?.[0] || retryJsonStr;

        try {
          deck = JSON.parse(retryJsonStr);
        } catch(e) {
          console.warn(`[copy] Retry parse failed, trying original`);
          deck = JSON.parse(jsonStr); // falls back
        }
        lastErrors = validateDeck(deck);
        if (lastErrors.length === 0) {
          console.log('[copy] ✅ Correction succeeded');
          return deck;
        }
        console.log(`[copy] Still ${lastErrors.length} errors after correction`);
      }
    }
  }

  // Se ainda há erros na última tentativa, truncamento agressivo
  console.warn('[copy] ⚠️ Final errors:', lastErrors.join('; '), '| proceeding with truncation');
  
  // Trunca campos estourados no limite
  for (const slide of deck.slides) {
    if (slide.title && slide.title.length > 13) slide.title = slide.title.substring(0, 13);
    if (slide.chip && slide.chip.length > 14) slide.chip = slide.chip.substring(0, 14).toUpperCase();
    if (slide.body) {
      slide.body = slide.body.map(b => b.substring(0, 180));
      if (!slide.body.some(b => b.includes('<em>'))) {
        const midIdx = Math.floor(slide.body.length / 2);
        slide.body = slide.body.map((b, i) => i === midIdx ? `<em>${b}</em>` : b);
      }
    }
    if (slide.items) {
      slide.items.forEach(it => {
        it.t = it.t.substring(0, 10);
        it.d = it.d.substring(0, 70);
      });
    }
    if (slide.cards) {
      slide.cards.forEach(c => {
        c.h = c.h.substring(0, 12);
        c.p = c.p.substring(0, 110);
      });
    }
    if (slide.checks) {
      slide.checks = slide.checks.map(ch => ch.substring(0, 28));
    }
  }
  if (deck.cover?.pill && deck.cover.pill.length > 45) deck.cover.pill = deck.cover.pill.substring(0, 45);
  if (deck.cover?.lines) {
    deck.cover.lines = deck.cover.lines.map(l => ({
      ...l,
      text: l.text.substring(0, 14)
    }));
  }
  if (deck.caption) deck.caption = deck.caption.substring(0, 500);
  if (!deck.hashtags || deck.hashtags.length < 8) {
    while ((deck.hashtags || []).length < 8) deck.hashtags.push('#ia');
    deck.hashtags = deck.hashtags.slice(0, 12);
  }

  return deck;
}