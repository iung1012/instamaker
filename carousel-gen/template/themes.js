// template/themes.js — peles do carrossel.
//
// O CSS base vive no render.js e define TODA a estrutura (grade, marcas de
// corte, ritmo vertical, ajuste automatico). Um tema aqui e apenas um bloco
// de override aplicado depois do base — assim mexer no layout vale para
// todos os temas de uma vez, sem duplicacao.

export const THEMES = {
  // Padrao: papel bege, tinta preta, laranja de acento.
  blueprint: '',

  // Inversao do blueprint: papel quase preto, tinta clara.
  // Nao e so trocar cores — contraste, peso de linha e as areas cheias
  // (chip, card preto) precisam inverter junto, senao o slide "suja".
  dark: `
:root{
  --orange:#ff5a2a;          /* laranja abre um tom: #e8481f fica opaco no escuro */
  --ink:#f2ede1;             /* tinta agora e o texto claro */
  --paper:#12110f;
  --cream-2:#1c1a17;
  --muted:#8c8578;
  --grid:#ffffff0a;
  --grid-major:#ffffff14;
  --line:#ffffff2e;
}

/* O chip era area preta sobre papel claro; inverte para nao sumir. */
.chip{background:var(--ink);color:#12110f}

/* Sunburst do canto precisa de mais presenca sobre fundo escuro. */
.slide::after{opacity:.22}

/* Moldura da imagem: borda clara e fundo escuro atras do transparente. */
.imgframe img{border:1px solid #ffffff26;background:var(--cream-2)}

/* Caixas de conteudo: fundo levemente acima do papel, borda clara. */
.listbox,.checks{background:var(--cream-2);border-color:#ffffff2e}
.listbox .row,.checks .c{border-color:#ffffff1f}
.listbox .d{color:#a39d90}

/* O par de cards inverte: o "dark" vira o card CLARO, que agora e o
   que salta no fundo escuro. O contorno laranja segue igual. */
.card.dark{background:var(--ink);color:#12110f;box-shadow:14px 14px 0 #00000047}
.card.dark p{color:#4a4740}
.card p{color:#a39d90}

/* Capa: specs e regua precisam clarear junto. */
.cover .topspec,.cover .botspec,.cover .ruler{color:#a39d90}
.cover .pill{background:var(--cream-2);border-color:var(--ink);box-shadow:8px 8px 0 #00000047}
.cover .band{box-shadow:10px 10px 0 #00000047}
`,


  // Minimalista: papel branco, sem grade, sem marcas de corte, sem sunburst.
  // A hierarquia fica toda por conta da tipografia e do espaco vazio — as
  // caixas viram filetes, e o laranja aparece so onde precisa apontar algo.
  minimal: `
:root{
  --orange:#e8481f;
  --ink:#0d0d0d;
  --paper:#ffffff;
  --cream-2:#ffffff;
  --muted:#9a9a9a;
  --grid:transparent;
  --grid-major:transparent;
  --line:#e2e2e2;
}

/* Tira toda a camada de "planta tecnica". */
.slide{background-image:none;padding:96px 96px 76px}
.slide::after{display:none}
.crop{display:none}

/* Cabecalho vira uma linha discreta: sem chip preenchido, sem regua. */
.head{margin-bottom:0}
.chip{background:transparent;color:var(--muted);border:0;padding:0;
  font:500 20px 'IBM Plex Mono',monospace;letter-spacing:.24em}
.rule .line{display:none}
.rule{flex:1;justify-content:flex-end;white-space:nowrap;gap:18px}
.rev{color:var(--muted);font-size:19px;white-space:nowrap}

/* O titulo e o unico elemento pesado da pagina. */
h1.display{margin-top:calc(56px * var(--gap));letter-spacing:-.025em}

/* Legenda de figura sem a barra com ticks — so um filete curto. */
.figcap .bar-o{width:64px;height:3px;background:var(--orange)}
.figcap .bar-o::before,.figcap .bar-o::after{display:none}
.figcap p{color:var(--muted);font-size:21px;letter-spacing:.14em;text-transform:uppercase}

.body{font-weight:500;color:#1a1a1a;max-width:880px}
.body em{font-weight:700}

/* Imagem sem moldura nem cantos: so a foto, respirando. */
.imgframe{padding:0}
.imgframe .k{display:none}
.imgframe img{border:0;border-radius:2px}

/* Listas e checks perdem a caixa e viram filetes horizontais. */
.listbox,.checks{border:0;background:transparent;padding:0}
.listbox .row{border-bottom:1px solid var(--line)}
.listbox .row:first-child{border-top:1px solid var(--line)}
.listbox .n{color:var(--orange);font-weight:500}
.listbox .t{font-size:44px}
.listbox .d{color:#6e6e6e}
.checks .lbl{color:var(--muted);padding-bottom:calc(14px * var(--gap))}
.checks .c{border-top:1px solid var(--line)}
.checks .dot{background:transparent;color:var(--orange);border:0;
  font:500 22px 'IBM Plex Mono',monospace;justify-content:flex-start;width:34px}
.checks .c span{font-weight:500}

/* Cards sem borda nem fundo: separados por um filete no topo. */
.card{background:transparent;border:0;box-shadow:none;padding:34px 0 0}
.card.dark{background:transparent;color:var(--ink);border-top:4px solid var(--ink);box-shadow:none}
.card.out{border:0;border-top:4px solid var(--orange)}
.card .lbl{color:var(--muted)}
.card h3{font-size:56px}
.card p,.card.dark p{color:#6e6e6e}
.cards{grid-template-columns:1fr 72px 1fr}
.arrow{color:var(--muted);font-size:38px;align-items:flex-start;padding-top:40px}

.foot{border-top:1px solid var(--line);padding-top:26px;color:var(--muted)}

/* Capa: mesma logica — tipografia e vazio. */
.cover{padding:96px 88px 76px}
.cover .topspec,.cover .botspec{color:var(--muted);font-size:19px}
.cover .ruler{display:none}
.cover .band{box-shadow:none;border-radius:0}
.cover .pill{background:transparent;border:3px solid var(--ink);box-shadow:none}
`,

  // Editorial: a imagem deixa de ser um bloco dentro do slide e passa a ser
  // o fundo inteiro, sangrando ate a borda. O texto vive por cima, sobre um
  // veu escuro. Slides sem imagem caem num fundo solido — resolvido com
  // :has(), que o Chromium do Playwright suporta.
  editorial: `
:root{
  --orange:#ff5a2a;
  --ink:#ffffff;
  --paper:#0a0a0a;
  --cream-2:#ffffff14;
  --muted:#ffffffa8;
  --grid:transparent;
  --grid-major:transparent;
  --line:#ffffff3d;
}

.slide{background-image:none;padding:74px 74px 62px;position:relative;isolation:isolate}
.slide::after{display:none}
.crop{display:none}

/* Slide SEM imagem: fundo solido com um brilho quente no canto. */
.slide:not(:has(.imgframe)){
  background:
    radial-gradient(120% 90% at 85% 0%,#ff5a2a26 0%,transparent 55%),
    var(--paper);
}

/* A imagem sai do fluxo e vira o fundo do slide inteiro. */
.imgframe{
  position:absolute;inset:0;padding:0;margin:0;z-index:-2;
}
.imgframe img{
  width:100%;height:100%;max-height:none;object-fit:cover;
  border:0;border-radius:0;display:block;
}
.imgframe .k{display:none}
.imgcap{display:none}

/* Veu que garante leitura do texto sobre qualquer foto. */
.slide:has(.imgframe)::before{
  content:'';position:absolute;inset:0;z-index:-1;pointer-events:none;
  background:
    linear-gradient(to bottom,#0a0a0ae6 0%,#0a0a0a8c 38%,#0a0a0acc 72%,#0a0a0af7 100%);
}

/* Cabecalho: chip solido laranja, o resto translucido. */
.chip{background:var(--orange);color:#0a0a0a;border-radius:0;font-weight:800}
.rule{color:var(--muted)}
.rule .line{background:var(--line)}
.rev{color:#fff}

h1.display{color:#fff;text-shadow:0 2px 24px #0a0a0a99;margin-top:calc(36px * var(--gap))}

.figcap .bar-o{background:var(--orange);width:120px}
.figcap .bar-o::before,.figcap .bar-o::after{display:none}
.figcap p{color:#fff;opacity:.86}

.body{color:#fff;font-weight:600;text-shadow:0 1px 16px #0a0a0a80;max-width:900px}
.body em{color:var(--orange);font-weight:800}
.body strong{color:#fff;font-weight:800}

/* Caixas viram vidro fosco sobre a foto. */
.listbox,.checks{
  background:#0a0a0a8f;border:1px solid var(--line);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
}
.listbox .row,.checks .c{border-color:#ffffff26}
.listbox .n{color:var(--orange)}
.listbox .t{color:#fff}
.listbox .d{color:var(--muted)}
.checks .lbl{color:var(--orange)}
.checks .dot{background:var(--orange);color:#0a0a0a}
.checks .c span{color:#fff}

.card{
  background:#0a0a0a94;border:1px solid var(--line);box-shadow:none;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
}
.card.dark{background:var(--orange);color:#0a0a0a;border-color:var(--orange)}
.card.dark .lbl{color:#0a0a0acc}
.card.dark h3{color:#0a0a0a}
.card.dark p{color:#0a0a0ad9}
.card.out{border-color:#ffffff5c}
.card h3{color:#fff}
.card p{color:var(--muted)}
.arrow{color:#fff}

.foot{color:#ffffffb8}

/* Capa: mesma logica, com a arte ocupando o quadro todo. */
.cover .topspec,.cover .botspec,.cover .ruler{color:#ffffffb8}
.cover .ruler .line{background:var(--line)}
.cover h1{color:#fff;text-shadow:0 2px 28px #0a0a0aa6}
.cover .band{background:var(--orange);color:#0a0a0a;box-shadow:none}
.cover .pill{background:#0a0a0a8f;border-color:#fff;color:#fff;box-shadow:none;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.cover .pill .tri{color:var(--orange)}
`,
};

export function resolveTheme(name) {
  const key = String(name || 'blueprint').trim().toLowerCase();
  if (!(key in THEMES)) {
    throw new Error(`Template desconhecido: "${key}". Disponiveis: ${Object.keys(THEMES).join(', ')}`);
  }
  return { name: key, css: THEMES[key] };
}
