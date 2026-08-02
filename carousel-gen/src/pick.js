#!/usr/bin/env node
// src/pick.js — escolhe a melhor notícia do dia (LLM)

import dotenv from 'dotenv';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

export async function pick(candidates) {
  if (candidates.length === 0) throw new Error('Sem candidatos');
  
  const prompt = `Selecione A ÚNICA melhor notícia sobre IA deste dia para um carrossel técnico. Priorize: impacto real > novidade > relevância pra quem constrói com IA. Evite artigos genéricos ou marketing puro.

CANDIDATOS:
${candidates.map((c, i) => `${i}. "${c.title}" (${c.source}) - ${c.summary?.substring(0, 200)}\n   Pontos HN: ${c.points}`).join('\n')}

Sua resposta final deve ser UMA linha contendo apenas um objeto JSON com duas chaves:
- "index": o numero inteiro do candidato escolhido, entre 0 e ${candidates.length - 1}
- "motivo": uma frase curta em portugues

Nao repita estas instrucoes. Escreva o JSON ja preenchido com o valor real como a ultima linha da resposta.`;

  for (let attempt = 1; attempt <= 3; attempt++) {
    console.log(`[pick] LLM call attempt ${attempt}/3...`);
    
    const res = await fetch(`${process.env.LLM_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.LLM_API_KEY}`,
      },
      body: JSON.stringify({
        model: process.env.LLM_MODEL || 'opus-5',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.3,
        max_tokens: 3000,
      }),
    });

    console.log(`[pick] HTTP ${res.status} for pick attempt ${attempt}`);
    
    if (!res.ok) {
      const errText = await res.text();
      console.warn(`[pick] Error response: ${errText.substring(0, 500)}`);
      if (res.status === 429 && attempt < 3) {
        await new Promise(r => setTimeout(r, 2000 * attempt));
        continue;
      }
      throw new Error(`LLM HTTP ${res.status}: ${errText.substring(0, 300)}`);
    }

    const data = await res.json();
    // content e a resposta; reasoning e so o rascunho do modelo
    let text = data?.choices?.[0]?.message?.content
             || data?.choices?.[0]?.message?.reasoning 
             || '';
    // Se content é array de blocks, concatena
    if (Array.isArray(text)) text = text.map(b => b.text || '').join('');
    
    console.log(`[pick] LLM response type: ${typeof text}, length: ${text.length}`);
    
    if (!text.trim()) {
      console.warn(`[pick] Empty response, retrying... attempt ${attempt}`);
      if (attempt < 3) { await new Promise(r => setTimeout(r, 1500 * attempt)); continue; }
      throw new Error('Pick failed — all attempts returned empty LLM response');
    }
    
    // O modelo cita o formato pedido antes de responder, entao o texto contem
    // varios objetos. Varre todos e fica com o ULTIMO que tenha "index"
    // numerico dentro do intervalo — o primeiro costuma ser o exemplo ecoado.
    const objs = [];
    for (let i = 0; i < text.length; i++) {
      if (text[i] !== '{') continue;
      let depth = 0;
      for (let j = i; j < text.length; j++) {
        if (text[j] === '{') depth++;
        else if (text[j] === '}') {
          depth--;
          if (depth === 0) { objs.push(text.slice(i, j + 1)); i = j; break; }
        }
      }
    }

    let picked = null;
    for (const cand of objs) {
      try {
        const o = JSON.parse(cand);
        if (Number.isInteger(o.index) && o.index >= 0 && o.index < candidates.length) picked = o;
      } catch { /* candidato invalido, segue */ }
    }

    if (!picked) {
      console.warn(`[pick] nenhum JSON valido entre ${objs.length} candidatos (tentativa ${attempt})`);
      if (attempt < 3) { await new Promise(r => setTimeout(r, 1500 * attempt)); continue; }
      throw new Error(`Pick failed — nenhum JSON com index valido. Final do texto: ${text.slice(-300)}`);
    }

    console.log(`[pick] escolhido #${picked.index}: ${candidates[picked.index].title.substring(0, 80)}`);
    return { candidate: candidates[picked.index], reason: picked.motivo };
  }
  
  throw new Error('Pick exhausted all attempts');
}