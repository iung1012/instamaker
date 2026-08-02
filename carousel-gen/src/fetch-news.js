#!/usr/bin/env node
// src/fetch-news.js — RSS + Hacker News → lista de candidatos

import Parser from 'rss-parser';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

const KEYWORDS = [
  'ai', 'llm', 'gpt', 'claude', 'gemini', 'model', 'agent',
  'openai', 'anthropic', 'transformer', 'inference', 'benchmark'
];

const RSS_URLS = [
  { url: 'https://techcrunch.com/category/artificial-intelligence/feed/', source: 'TechCrunch AI' },
  { url: 'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml', source: 'The Verge AI' },
  { url: 'https://openai.com/blog/rss.xml', source: 'OpenAI Blog' },
  { url: 'https://www.anthropic.com/rss.xml', source: 'Anthropic' },
  { url: 'https://blog.google/technology/ai/rss/', source: 'Google AI' },
  { url: 'https://huggingface.co/blog/feed.xml', source: 'Hugging Face Blog' },
  { url: 'https://export.arxiv.org/rss/cs.AI', source: 'arXiv CS.AI' },
];

function matchesKeywords(title, description) {
  const text = (title + ' ' + (description || '')).toLowerCase();
  return KEYWORDS.some(kw => text.includes(kw));
}

async function extractOGImage(url, timeoutMs = 8000) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) return null;
    const html = await res.text();
    // Tenta <meta property="og:image" content="..."> ou <meta name="twitter:image" content="...">
    const match = html.match(/(?:property|name)=["']og:image["'][^>]*content=["']([^"'>]+)["']/i)
      || html.match(/(?:property|name)=["']twitter:image["'][^>]*content=["']([^"'>]+)["']/i);
    if (match) {
      let img = match[1].trim();
      if (!img.startsWith('http')) img = 'https:' + img;
      return img;
    }
    return null;
  } catch {
    return null;
  }
}

async function fetchJSON(urlString, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(urlString, { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

export async function fetchCandidates() {
  const candidates = [];

  // --- RSS feeds ---
  const parser = new Parser({ timeout: 8000, headers: { 'User-Agent': 'Mozilla/5.0' } });
  for (const { url, source } of RSS_URLS) {
    try {
      console.log(`[fetch] RSS: ${source}`);
      const feed = await parser.parseURL(url);
      for (const item of feed.items.slice(0, 15)) {
        if (!matchesKeywords(item.title, item.contentSnippet || '')) continue;
        let ogImage = null;
        try {
          ogImage = await extractOGImage(item.link, 5000);
        } catch {}
        candidates.push({
          title: item.title.trim(),
          url: item.link?.trim() || '',
          source,
          publishedAt: item.pubDate ? new Date(item.pubDate).toISOString() : new Date().toISOString(),
          points: 0,
          summary: (item.contentSnippet || item.content || '').substring(0, 500),
          ogImage,
        });
      }
    } catch (err) {
      console.error(`[fetch] RSS ${source} failed:`, err.message);
    }
  }

  // --- Hacker News via Algolia ---
  try {
    console.log('[fetch] HN: searching');
    const nowSec = Math.floor(Date.now() / 1000);
    const oneDayAgo = nowSec - 86400;
    const hnUrl = `https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i>${oneDayAgo},points>80`;
    const hnData = await fetchJSON(hnUrl);
    const hits = hnData.hits || [];
    for (const hit of hits) {
      if (!matchesKeywords(hit.title, hit.story_text || '')) continue;
      let ogImage = null;
      try {
        ogImage = await extractOGImage(hit.url, 5000);
      } catch {}
      // Fix: created_at pode vir como string do Algolia
      const createdAtMs = parseInt(hit.created_at, 10) * 1000;
      const dateStr = isNaN(createdAtMs) ? new Date().toISOString() : new Date(createdAtMs).toISOString();
      candidates.push({
        title: hit.title || '',
        url: hit.url || '',
        source: 'Hacker News',
        publishedAt: dateStr,
        points: hit.points || 0,
        summary: (hit.story_text || hit.url || '').substring(0, 500),
        ogImage,
      });
    }
    console.log(`[fetch] HN: ${hits.length} results`);
  } catch (err) {
    console.error('[fetch] HN failed:', err.message);
  }

  return candidates;
}