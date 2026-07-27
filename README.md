# Instagram Automation

Automacao para capturar videos do X/Twitter, montar videos verticais e publicar no Instagram como Reels/Stories e, opcionalmente, no YouTube como Shorts.

## O que faz

- Baixa videos da timeline do X/Twitter.
- Gera videos verticais para Instagram.
- Remove o audio original do video do Twitter por padrao.
- Coloca musica de fundo no video final.
- Publica Reels, Stories e carrosseis no Instagram.
- Publica videos verticais como YouTube Shorts quando habilitado.
- Roda manualmente no Windows ou agendado em VPS Linux.

## Arquivos principais

- `automation_pipeline.py`: orquestra captura, composicao e publicacao.
- `twitter_timeline_scraper.py`: baixa videos do X/Twitter.
- `compose_test_video.py`: monta o video final vertical.
- `instagram_graph_publisher.py`: publica no Instagram via Graph API.
- `youtube_shorts_publisher.py`: publica Shorts via YouTube Data API.
- `carousel_generator.py`: gera carrosseis.
- `run_automation.ps1`: runner Windows com log e lock.
- `run_automation.sh`: runner Linux/VPS com log e lock.
- `setup_vps_cron.sh`: agenda 5 publicacoes por dia na VPS.
- `VPS_SETUP.md`: passo a passo para VPS Ubuntu.
- `.github/workflows/instagram-daily.yml`: agenda no GitHub Actions.
- `GITHUB_ACTIONS_SETUP.md`: configuracao dos secrets e teste do workflow.

## Requisitos

- Python 3.10 ou superior.
- FFmpeg/FFprobe instalados.
- Conta Instagram Business ou Creator conectada a uma Pagina do Facebook.
- `IG_USER_ID` e `IG_ACCESS_TOKEN` validos da Meta.
- Para Shorts: OAuth client do Google Cloud com YouTube Data API v3 habilitada.
- `cookies.json` local e valido para capturar videos do X/Twitter. Nunca envie esse arquivo ao Git.

## Instalacao Windows

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\venv\Scripts\python.exe -m playwright install chromium
winget install --id Gyan.FFmpeg -e
```

Crie o `.env`:

```powershell
Copy-Item .env.example .env
```

Preencha:

```env
IG_USER_ID=SEU_IG_USER_ID
IG_ACCESS_TOKEN=SEU_ACCESS_TOKEN
META_APP_ID=SEU_META_APP_ID
META_APP_SECRET=SEU_META_APP_SECRET
```

### Renovar o token do Instagram

O projeto usa a Instagram Graph API via Facebook Login. Um token já expirado
não pode ser renovado diretamente; gere um novo token de usuário no Meta Graph
API Explorer com as permissões usadas pelo app e troque-o por um token de longa
duração:

```powershell
python .\renew_instagram_token.py --exchange --prompt-token --save
```

O comando lê `META_APP_ID` e `META_APP_SECRET` do `.env`, pede o token novo sem
mostrá-lo na tela, valida a conta e salva somente `IG_ACCESS_TOKEN` no `.env`.
Depois, atualize manualmente o secret `IG_ACCESS_TOKEN` do GitHub Actions com o
valor salvo localmente. Para verificar um token sem trocá-lo:

```powershell
python .\renew_instagram_token.py --check
```

Se as credenciais do app não estiverem no `.env`, acrescente
`--prompt-app-credentials` aos comandos. Nunca versione `META_APP_SECRET` ou
qualquer token.

## Testar sem publicar

Gerar videos finais sem publicar:

```powershell
python .\automation_pipeline.py `
  --skip-publish `
  --publish-kind both `
  --publish-count 5 `
  --max-compose 5
```

Os arquivos saem em `outputs_ig`.

Ao iniciar qualquer execucao real, a pipeline apaga todo o conteudo de:

- `timeline_downloads`
- `outputs_ig`

O arquivo `.automation_state.json` nao e apagado, para evitar repostar a mesma midia.
No modo `--dry-run`, a limpeza e apenas exibida e nenhum arquivo e removido.

## Publicar agora

Subir ate 5 videos como Reels e Stories:

```powershell
python .\automation_pipeline.py `
  --publish-kind both `
  --publish-count 5 `
  --max-compose 5
```

Cada video vira:

- 1 Reel
- 1 Story

Com `--publish-count 5`, o resultado esperado e ate 5 Reels + 5 Stories.

A pipeline publica apenas os videos gerados na execucao atual. Arquivos deixados em
`outputs_ig` por uma execucao anterior sao apagados no inicio da proxima.

Para publicar tambem no YouTube Shorts:

```powershell
python .\automation_pipeline.py `
  --publish-kind both `
  --publish-youtube-shorts `
  --publish-count 1
```

Para publicar apenas no YouTube Shorts:

```powershell
python .\automation_pipeline.py `
  --skip-instagram `
  --publish-youtube-shorts `
  --publish-count 1
```

## Audio

Por padrao, a pipeline usa `--music-only` na composicao:

- remove o audio original do Twitter;
- mantem somente a musica de fundo;
- usa `nastelbom-minimal-technology-345194.mp3`;
- volume padrao: `0.10`.

Para manter o audio original junto da musica:

```powershell
python .\automation_pipeline.py --keep-original-audio
```

## Quadros atras do texto

Os retangulos pretos/cinzas atras dos textos ficam desligados por padrao.

Para reativar com baixa opacidade:

```powershell
python .\automation_pipeline.py --text-box-opacity 0.35
```

## VPS Linux

Veja `VPS_SETUP.md`.

Uso rapido:

```bash
chmod +x run_automation.sh setup_vps_cron.sh
./setup_vps_cron.sh
```

Padrao do cron:

- 09:00
- 12:00
- 15:00
- 18:00
- 21:00

Cada horario publica 1 video como Reel e Story.

## GitHub Actions

Veja `GITHUB_ACTIONS_SETUP.md`.

O workflow executa 5 vezes por dia no timezone `America/Sao_Paulo`. Cada
execucao publica 1 Reel e 1 Story. O avatar e a musica ficam versionados na
raiz do repositorio e seguem junto no checkout da Action.

## YouTube Shorts

Antes do primeiro upload:

1. Crie um projeto no Google Cloud.
2. Habilite a YouTube Data API v3.
3. Configure a OAuth consent screen.
4. Crie um OAuth Client ID do tipo Desktop app.
5. Baixe o JSON e salve na raiz como `youtube_client_secret.json`.

No `.env`, ajuste se necessario:

```env
YOUTUBE_CLIENT_SECRETS_FILE=youtube_client_secret.json
YOUTUBE_TOKEN_FILE=youtube_token.json
YOUTUBE_PRIVACY_STATUS=public
YOUTUBE_SHORTS_TAGS=Shorts,IA,automacao,tecnologia,programacao
PUBLISH_YOUTUBE_SHORTS=false
SKIP_INSTAGRAM=false
```

Faca o login uma vez para gerar `youtube_token.json`:

```powershell
python .\youtube_shorts_publisher.py --auth-only
```

Teste com upload privado:

```powershell
python .\youtube_shorts_publisher.py `
  --video .\avatar_video.mp4 `
  --privacy-status private
```

Observacoes:

- `youtube_token.json` e `youtube_client_secret.json` ficam ignorados pelo Git.
- O script adiciona `#Shorts` na descricao quando ainda nao existir.
- O YouTube classifica videos quadrados ou verticais de ate 3 minutos como Shorts.
- Projetos de API nao verificados pelo Google podem ter uploads restritos a privado ate passarem por auditoria.

## TikTok Sandbox

Para testar Login Kit + Direct Post no TikTok Sandbox, use o script abaixo:

```powershell
python .\tiktok_sandbox_uploader.py `
  --client-key $env:TIKTOK_CLIENT_KEY `
  --client-secret $env:TIKTOK_CLIENT_SECRET `
  --redirect-uri http://127.0.0.1:8765/tiktok/callback/ `
  --video .\avatar_video.mp4
```

Antes de rodar:

- cadastre a app no TikTok for Developers;
- adicione os produtos `Login Kit` e `Content Posting API`;
- registre o `redirect_uri` acima na configuracao do Login Kit para Desktop;
- adicione sua conta TikTok como `Target user` no Sandbox;
- garanta que o scope `video.publish` esteja habilitado para o app.

Fluxo do script:

- abre a tela de login/autorizacao do TikTok;
- recebe o `code` no callback local;
- troca o `code` por `access_token` e `refresh_token`;
- consulta `creator_info` para escolher um `privacy_level` valido;
- inicializa o Direct Post e recebe `publish_id` + `upload_url`;
- envia o arquivo para os servidores do TikTok;
- acompanha o status ate `PUBLISH_COMPLETE`.

Observacao:

- no Sandbox, uploads nao auditados podem ficar restritos a visualizacao privada;
- se o TikTok retornar `unaudited_client_can_only_post_to_private_accounts`, a conta TikTok usada no teste precisa estar em modo privado no app antes da publicacao;
- se o TikTok retornar `scope_not_authorized`, o app ainda nao tem o scope liberado para esse fluxo.

Depois do primeiro login, o script salva no `.env`:

- `TIKTOK_ACCESS_TOKEN`
- `TIKTOK_REFRESH_TOKEN`
- `TIKTOK_TOKEN_EXPIRES_IN`
- `TIKTOK_TOKEN_EXPIRES_AT_UTC`

Para reutilizar um token salvo sem passar pelo login:

```powershell
python .\tiktok_sandbox_uploader.py --token-only --video .\avatar_video.mp4
```

Se o `access_token` estiver expirado, o script tenta renovar usando `TIKTOK_REFRESH_TOKEN` e atualiza o `.env` com os novos valores.

Observacao:

- como o fluxo agora usa `video.publish`, se voce tinha token antigo de `video.upload`, rode o login completo novamente para gerar um token com o scope novo.

## Pastas geradas

- `timeline_downloads`: videos baixados do X/Twitter.
- `outputs_ig`: videos finais prontos para publicacao.
- `outputs_ig/carousels`: pacotes de carrossel.
- `logs`: logs de execucao.

O conteudo dessas pastas e apagado automaticamente no inicio de cada execucao real.
A pipeline usa `.automation_state.json` para evitar repostagem.
