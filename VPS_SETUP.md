# Setup em VPS Ubuntu

Este caminho deixa a automacao simples: 5 horarios por dia, cada horario publica 1 video como Reel e Story. Resultado padrao: 5 Reels + 5 Stories por dia.

## 1) Preparar servidor

Use Ubuntu 22.04/24.04 com pelo menos 2 GB de RAM. No servidor:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg cron
```

Entre na pasta do projeto e instale as dependencias:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m playwright install chromium
```

Se o Playwright pedir bibliotecas do sistema:

```bash
sudo ./venv/bin/python -m playwright install-deps chromium
```

## 2) Configurar Instagram

Crie o `.env` com suas credenciais da Meta:

```bash
cp .env.example .env
nano .env
```

Obrigatorio:

```env
IG_USER_ID=SEU_IG_USER_ID
IG_ACCESS_TOKEN=SEU_ACCESS_TOKEN
```

A conta precisa ser Instagram Business ou Creator conectada a uma Pagina do Facebook. O token precisa ter permissao de publicacao via Instagram Graph API.

## 3) Testar sem publicar

```bash
chmod +x run_automation.sh setup_vps_cron.sh
./run_automation.sh --dry-run
```

## 4) Publicar manualmente

Uma execucao padrao publica 1 video como Reel e Story:

```bash
./run_automation.sh
```

Para subir 5 Reels + 5 Stories de uma vez:

```bash
./run_automation.sh --count 5 --kind both
```

Para publicar o mesmo video tambem como YouTube Short:

```bash
./run_automation.sh --youtube-shorts
```

Para publicar apenas no YouTube Shorts:

```bash
./run_automation.sh --skip-instagram --youtube-shorts
```

## 5) Agendar 5 Reels + 5 Stories por dia

Padrao: 09:00, 12:00, 15:00, 18:00 e 21:00 no horario do servidor.

```bash
./setup_vps_cron.sh
```

Horarios personalizados:

```bash
./setup_vps_cron.sh --times "08:00,11:00,14:00,17:00,20:00"
```

Agendar tambem YouTube Shorts:

```bash
./setup_vps_cron.sh --youtube-shorts
```

Antes de habilitar Shorts no cron, gere o token OAuth na VPS:

```bash
./venv/bin/python youtube_shorts_publisher.py --auth-only
```

Ver cron instalado:

```bash
crontab -l
```

Ver logs:

```bash
tail -f logs/cron.log
ls -lh logs
```

## Observacoes

- Cada horario usa `--kind both`, entao o mesmo video vira 1 Reel e 1 Story.
- A pipeline usa estado em `.automation_state.json` para nao repostar a mesma midia no mesmo formato.
- No inicio de cada execucao, o conteudo de `timeline_downloads` e `outputs_ig` e apagado.
- Stories aceitam apenas video neste projeto.
- YouTube Shorts usa `youtube_client_secret.json` e `youtube_token.json`, ambos fora do Git.
- Se nao houver videos novos suficientes, a execucao publica menos ou nada.
- Para usar timezone de Sao Paulo na VPS: `sudo timedatectl set-timezone America/Sao_Paulo`.
