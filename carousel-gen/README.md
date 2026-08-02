# carousel-gen

Gerador de carrossel em Node + Playwright. **Nao e a implementacao ativa** — quem
monta e publica hoje e o modulo `carousel/` em Python, chamado pelo bot. Este
projeto ficou como referencia e como ferramenta de linha de comando.

## Por que esta aqui

Os 4 temas (`template/themes.js`) nasceram aqui e foram portados para
`carousel/template.html`. Os nomes de classe sao os mesmos nos dois, entao o CSS
de um vale no outro quase sem adaptacao.

## O que ele tem que a implementacao ativa nao tem

- **Ajuste automatico anti-corte** (`src/render.js`): mede o slide depois de
  montado e reduz espacamento, altura de imagem, entrelinha e fonte — nessa
  ordem — ate caber nos 1350px. Se nao couber, falha alto em vez de cortar
  em silencio.
- **Fontes embutidas em base64**: `page.setContent()` nao resolve caminho
  relativo, entao `@font-face` apontando para arquivo local nunca carrega e o
  Anton cai para Arial. Aqui as fontes vao inline.
- **`retema`**: re-renderiza um deck ja gerado com outro tema em ~12s, sem
  gastar token, porque o `deck.json` fica salvo.
- **`publish.sh`**: valida antes de publicar (contagem, 1080x1350, legenda
  nao vazia) e exige `--publish` explicito.

## Uso

```bash
npm install
npx playwright install chromium

npm run link -- "https://x.com/..."                 # gera (4-5 min)
npm run link -- "https://..." --template=dark       # ja com tema
npm run retema -- minimal                            # troca o tema (~12s)
./publish.sh                                         # valida, nao publica
./publish.sh --publish                               # publica
```

Temas: `blueprint` (padrao), `dark`, `minimal`, `editorial`.

Copie `.env.example` para `.env` e preencha as chaves.
