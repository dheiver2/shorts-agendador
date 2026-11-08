---
title: Configuração
nav_order: 2
---

# Configuração
{: .no_toc }

1. TOC
{:toc}

## Primeira vez

Precisa de Python 3.10 ou mais novo e do `ffmpeg` (`brew install ffmpeg`).

```bash
git clone https://github.com/dheiver2/shorts-agendador && cd shorts-agendador
python3 -m venv .venv && .venv/bin/pip install google-api-python-client google-auth-oauthlib pytest
```

### 1. Credencial do Google
No [Google Cloud Console](https://console.cloud.google.com/), crie um projeto e ative a **YouTube Data API v3**. Em seguida, configure a tela de consentimento OAuth como **Externo**, com o seu e-mail como usuário de teste. Por fim, crie um **ID do cliente OAuth → App para computador** e salve o JSON baixado como `client_secret.json` na pasta do projeto.

### 2. Login no canal
```bash
.venv/bin/python agente.py auth
```
Se aparecer **"O Google não verificou este app"**, clique em **Avançado → Acessar**. É normal, porque o app é seu e está em modo de teste.

### 3. Avisos e Brier (`.env`)
```bash
cp .env.exemplo .env
```

| Variável | Para quê |
|---|---|
| `TELEGRAM_CHAT_ID` | seu chat no Telegram |
| `TELEGRAM_KEYCHAIN_SERVICE` | nome do item no Keychain do macOS com o token do bot (recomendado) |
| `TELEGRAM_BOT_TOKEN` | token direto no arquivo (alternativa ao Keychain) |
| `BRIER_KEY` | chave do Mangaba Router para o Brier |

Teste os avisos com `.venv/bin/python agente.py teste-aviso`.

### 4. Rodar sozinho
```bash
./instalar-agendamento.sh
```
Isso instala um LaunchAgent que roda a cada 2 horas e quando você faz login.

## `config.json`

| Campo | Padrão | O que faz |
|---|---|---|
| `pasta_videos` | `~/Movies/Shorts-para-postar` | de onde saem os vídeos |
| `posts_por_semana` | `30` | total da semana; a divisão por 7 dá a quantidade por dia |
| `dias_extra` | `["sab","dom"]` | dias que recebem a sobra da divisão |
| `horarios` | `12:00, 19:00, 21:00, 17:00, 09:00` | do melhor para o pior; cada dia usa os primeiros N |
| `horarios_reserva` | `15:00, 10:00` | usados só para repor horário perdido |
| `max_por_dia` | `6` | teto por dia (cota gratuita da API: cerca de 6 envios por dia) |
| `dias_a_frente` | `1` | quantos dias à frente deixar agendado |
| `avisar_fila_abaixo_dias` | `3` | avisa quando a fila tiver menos dias que isso |
| `brier` | ver [Brier](brier) | modelo, URL e cortes |

## Auditoria da API
Projetos da API que não passaram pela auditoria do Google costumam ter os uploads **travados como privados**. Neste canal o teste de 07/11/2026 mostrou o vídeo como **"Agendado"**, sem trava. Se um dia aparecer "privado (restrito)", peça a auditoria no [formulário do YouTube API Services](https://support.google.com/youtube/contact/yt_api_form).
