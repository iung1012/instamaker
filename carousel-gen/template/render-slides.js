/**
 * template/render-slides.js
 * 
 * Renderiza um deck.json em HTML puro com as classes CSS esperadas.
 * Importado pelo slide.html via <script type="module">.
 */

export function renderDeck(deck) {
  let html = '';
  
  for (let i = 0; i < deck.slides.length; i++) {
    const s = deck.slides[i];
    if (s.type === 'cover') {
      html += renderCover(s);
    } else {
      html += renderContentSlide(s, i + 1);
    }
  }
  
  return html;
}

function renderCover(d) {
  const dateStr = d.date ? d.date.split('-').reverse().join('.') : '30.07.2026';
  const brand = window.BRAND_NAME || 'CHASE AI';
  
  // Top specs
  let topSpecLeft = '';
  (d.specTop || []).forEach(l => { topSpecLeft += `<span>${escHtml(l)}</span>`; });
  let topSpecRight = '';
  (d.specTopRight || []).forEach(l => { topSpecRight += `<span>${escHtml(l)}</span>`; });
  
  // Lines with possible band
  let linesHtml = '';
  d.lines.forEach(line => {
    if (line.band) {
      linesHtml += `<div class="band"><span>${escHtml(line.text)}</span></div>`;
    } else {
      linesHtml += `<div>${escHtml(line.text)}</div>`;
    }
  });
  
  // Art image
  let artImg = '';
  if (d.artImage) {
    artImg = `<img src="${escAttr(d.artImage)}" alt="art">`;
  }
  
  // Bot specs
  let botSpecLeft = '';
  (d.specBot || []).forEach(l => { botSpecLeft += `<span>${escHtml(l)}</span>`; });
  let botSpecRight = '';
  (d.specBotRight || []).forEach(l => { botSpecRight += `<span>${escHtml(l)}</span>`; });
  
  return `
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
    
    ${linesHtml.replace(/<div[^>]*>/g, '').replace(/<\/div>/g, '')}
    
    <div class="art">${artImg}</div>
    
    ${d.pill ? `<div class="pill"><span class="tri">◆</span><span>${escHtml(d.pill)}</span></div>` : ''}
    
    <div class="botspec">
      <div class="l">${botSpecLeft}</div>
      <div class="r">${botSpecRight}</div>
    </div>
  </div>`;
}

function renderContentSlide(d, index) {
  const sheetNum = parseInt((d.sheet || '').match(/\d+/)?.[0] || String(index));
  const sheetLabel = `SHEET ${String(sheetNum).padStart(2,'0')} / 09`;
  
  const chipLabel = escHtml(d.chip || '');
  const titleEsc = escHtml(d.title || '');
  const figEsc = escHtml(d.fig || '');
  const imageCaption = escHtml(d.imageCaption || '');
  const noSwipe = d.noSwipe || (index === 9); // last slide
  
  let bodyHtml = '';
  if (Array.isArray(d.body)) {
    bodyHtml = d.body.map(b => `<p>${b}</p>`).join('');
  } else if (d.body) {
    bodyHtml = `<p>${escHtml(d.body)}</p>`;
  }
  
  let imageFrame = '';
  if (d.image) {
    imageFrame = `
    <div class="imgframe">
      <img src="${escAttr(d.image)}" alt="fig">
      <div class="k a"></div><div class="k b"></div>
      <div class="k c"></div><div class="k d"></div>
    </div>
    ${imageCaption ? `<p class="imgcap">${imageCaption}</p>` : ''}`;
  }
  
  // Type-specific blocks
  let contentBlock = '';
  
  if (d.type === 'list' && Array.isArray(d.items)) {
    let rows = '';
    d.items.forEach(item => {
      rows += `
      <div class="row">
        <span class="n">${escHtml(item.n || '')}</span>
        <span class="t">${escHtml(item.t || '')}</span>
        <span class="d">${escHtml(item.d || '')}</span>
      </div>`;
    });
    contentBlock = `<div class="listbox">${rows}</div>`;
  }
  
  if (d.type === 'cards' && Array.isArray(d.cards)) {
    const darkCard = d.cards.find(c => c.style === 'dark');
    const outCard = d.cards.find(c => c.style !== 'dark');
    
    const makeCard = (c) => `
      <div class="card${c.style === 'dark' ? ' dark' : ' out'}">
        <p class="lbl">${escHtml(c.lbl || '')}</p>
        <h3>${escHtml(c.h || '')}</h3>
        <p>${escHtml(c.p || '')}</p>
      </div>`;
    
    contentBlock = `<div class="cards">
      ${makeCard(darkCard || d.cards[0])}
      <div class="arrow">▸</div>
      ${makeCard(outCard || d.cards[d.cards.length - 1])}
    </div>`;
  }
  
  if (d.type === 'checks' && Array.isArray(d.checks)) {
    let checksHtml = '';
    d.checks.forEach((c, i) => {
      checksHtml += `
      <div class="c">
        <div class="dot">${i + 1}</div>
        <span>${escHtml(c)}</span>
      </div>`;
    });
    contentBlock = `
    <div class="checks">
      <p class="lbl">${escHtml(d.checksLabel || 'CHECKLIST')}</p>
      ${checksHtml}
    </div>`;
  }
  
  const smClass = d.titleSize === 'sm' ? ' sm' : '';
  const swipeLabel = noSwipe ? '' : 'SWIPE →';
  
  return `
  <div class="slide">
    <div class="crop tl"></div>
    <div class="crop tr"></div>
    <div class="crop bl"></div>
    <div class="crop br"></div>
    
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

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escAttr(str) {
  if (!str) return '';
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}