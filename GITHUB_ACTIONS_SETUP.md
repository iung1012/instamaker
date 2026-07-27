# GitHub Actions

O workflow `.github/workflows/instagram-daily.yml` executa diariamente as:

- 09:17
- 12:17
- 15:17
- 18:17
- 21:17

O timezone configurado e `America/Sao_Paulo`. Cada execucao publica 1 video como
Reel e Story, totalizando ate 5 Reels e 5 Stories por dia.

## 1. Trocar os cookies expostos

Este repositorio e publico e `cookies.json` ja apareceu no historico do Git.
Encerre todas as sessoes do X/Twitter, entre novamente e gere um novo
`cookies.json`. Nao reutilize os cookies antigos.

## 2. Incluir avatar e musica

Estes arquivos devem ser enviados junto com o projeto:

- `avatar_video.mp4`
- `nastelbom-minimal-technology-345194.mp3`

Eles ficam na raiz do repositorio e sao copiados automaticamente para o runner
pelo passo `actions/checkout`. O workflow interrompe a execucao se algum deles
estiver ausente ou vazio.

## 3. Criar secrets

No repositorio, abra `Settings > Secrets and variables > Actions` e crie:

| Secret | Valor |
| --- | --- |
| `IG_USER_ID` | ID da conta profissional do Instagram |
| `IG_ACCESS_TOKEN` | Token valido da Instagram Graph API |
| `TWITTER_COOKIES_JSON` | Conteudo completo do novo `cookies.json` |
| `VPS_HOST` | IP ou dominio da VPS |
| `VPS_USER` | Usuario SSH da VPS |
| `VPS_PORT` | Porta SSH da VPS, normalmente `22` |
| `VPS_SSH_KEY` | Conteudo completo da chave privada SSH |
| `VPS_PATH` | Caminho absoluto do projeto na VPS |

Nunca coloque os valores desses secrets no workflow, README ou commits.

## YouTube Shorts opcional

Para publicar tambem no YouTube, configure o OAuth uma vez na VPS:

```bash
./venv/bin/python youtube_shorts_publisher.py --auth-only
```

Deixe na VPS, fora do Git:

- `youtube_client_secret.json`
- `youtube_token.json`

Depois, em `Settings > Secrets and variables > Actions > Variables`, crie:

| Variable | Valor |
| --- | --- |
| `PUBLISH_YOUTUBE_SHORTS` | `true` para publicar cada video tambem como Short |
| `SKIP_INSTAGRAM` | `true` somente se quiser publicar apenas no YouTube |

Se essas variables nao existirem, o workflow continua publicando apenas Instagram.

## 4. Enviar e testar

Depois do `git push`, abra `Actions > Instagram Daily Automation > Run workflow`.

Primeiro mantenha:

- `dry_run`: marcado
- `publish_count`: `1`

Esse teste valida instalacao e comandos sem baixar nem publicar. Depois execute
manualmente com `dry_run` desmarcado e `publish_count` igual a `1`.

Para publicar imediatamente 5 Reels e 5 Stories, execute manualmente com:

- `dry_run`: desmarcado
- `publish_count`: `5`

## Execucao na VPS

O runner do GitHub nao publica diretamente. A cada execucao ele:

1. valida os secrets e testa a conexao SSH;
2. sincroniza o codigo com `VPS_PATH`, preservando `.env`, `venv`, estado e logs;
3. transfere as credenciais para uma pasta temporaria protegida;
4. registra um diagnostico seguro do estado e dos logs da VPS;
5. instala as dependencias quando `requirements.txt` mudar;
6. executa `github_vps_runner.sh` e `run_automation.sh` na VPS;
7. remove as credenciais temporarias ao terminar.

A VPS precisa ter `python3`. Se `python3-venv` nao estiver instalado, o workflow
usa o bootstrap oficial do PyPA para criar o ambiente virtual sem `sudo`.
FFmpeg e FFprobe tambem sao instalados dentro desse ambiente. O workflow valida
o Chromium e, quando necessario, baixa os pacotes oficiais do Ubuntu para uma
pasta local do projeto sem exigir `sudo`. Se isso nao for suficiente, tenta a
instalacao administrativa quando o usuario for `root` ou possuir `sudo` sem
senha. O primeiro teste deve ser manual com `dry_run` marcado.

## Estado e logs

O arquivo `.automation_state.json` permanece na VPS e nao e sobrescrito durante
a sincronizacao. Isso evita repostagens. Os logs de cada execucao permanecem na
VPS e tambem ficam nos artifacts da pagina da Action por 14 dias.

O GitHub pode atrasar execucoes agendadas em periodos de carga. Em repositorios
publicos sem atividade por 60 dias, workflows agendados podem ser desativados.
