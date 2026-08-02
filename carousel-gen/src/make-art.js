#!/usr/bin/env node
// src/make-art.js — geração de arte com personagem consistente

import { generate } from './providers/kie.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(root, '.env') });

export async function makeArt(deck, data) {
  const outDir = path.join(root, 'out', data);
  await fs.mkdir(outDir, { recursive: true });
  
  const refPath = path.join(root, 'ref', 'characters.png');
  let artUrl = null;

  // Se ref/characters.png já existe, usar como referência
  let refImageUrl = process.env.REF_IMAGE_URL;
  let needGenerateRef = false;

  try {
    await fs.access(refPath);
    console.error('[art] ✅ Found existing characters.png — using as reference');
    // Precisamos servir via HTTP ou ter uma URL pública
    if (!refImageUrl) {
      refImageUrl = `${process.env.PUBLIC_BASE_URL || `http://localhost:${process.env.KIE_REF_PORT || 8791}`}/ref/characters.png`;
    }
  } catch {
    // Primeiro rodado — gerar personagens de referência
    console.error('[art] 🔶 No characters.png — generating reference');
    needGenerateRef = true;
    
    if (!refImageUrl) {
      refImageUrl = `${process.env.PUBLIC_BASE_URL || `http://localhost:${process.env.KIE_REF_PORT || 8791}`}/ref/characters.png`;
    }
  }

  if (needGenerateRef) {
    // Gerar personagens base
    const result = await generate({
      prompt: 'Two blocky voxel LEGO-style characters side-by-side facing forward: a red-orange cube robot (#E8451F) and a golden sun with block rays (#F5B417), neutral pose, soft studio lighting, transparent background, no text.',
      refImageUrl: null, // sem referência para primeira vez
    });
    
    if (result && await fs.stat(result).catch(() => false)) {
      await fs.rename(result, refPath);
      console.error(`[art] ✅ Saved reference to ${refPath}`);
    } else {
      console.error('[art] ❌ Failed to generate reference — will use placeholder');
    }
  }

  // Para cada slide que precisa de arte, gerar/imagem
  for (let i = 0; i < deck.slides.length; i++) {
    const slide = deck.slides[i];
    
    if (slide.type === 'cover' && slide.artPrompt) {
      console.error(`[art] Generating cover art...`);
      const result = await generate({
        prompt: slide.artPrompt,
        refImageUrl,
      });
      
      if (result && await fs.stat(result).then(s => s.size > 1000).catch(() => false)) {
        const dest = path.join(outDir, `art-${i}.png`);
        await fs.copyFile(result, dest);
        console.error(`[art] ✅ Cover art saved to ${dest}`);
      } else {
        // Fallback: usar characters.png puro
        if (await fs.stat(refPath).then(s => s.size > 1000).catch(() => false)) {
          const dest = path.join(outDir, `art-${i}.png`);
          await fs.copyFile(refPath, dest);
          console.error(`[art] ⚠️ Using fallback characters.png for cover`);
        }
      }
    }
  }

  return { artDone: true };
}