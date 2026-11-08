# Agendador de Shorts

**Documentação:** https://dheiver2.github.io/shorts-agendador · **Painel:** https://dheiver2.github.io/shorts-painel · **Plano:** [docs/plano.md](docs/plano.md)

Pega os vídeos da pasta `~/Movies/Shorts-para-postar` e agenda **14 por dia** no YouTube, um por hora das 07:00 às 20:00 (horário de Brasília), 98 por semana.
Cada vídeo sobe como privado, com data de publicação marcada, e o YouTube publica no horário. O Mac não precisa estar ligado na hora do post.

## Como usar no dia a dia
- Coloque os vídeos na pasta. Eles saem em ordem alfabética (dá para numerar: `001-...mp4`, `002-...mp4`).
- Título: vem do nome do arquivo. Para definir título e descrição, crie um `.txt` com o mesmo nome do vídeo: a 1ª linha é o título e o resto é a descrição.
- Depois de agendado, o vídeo vai para `postados/`. Vídeos horizontais ou com mais de 3 min vão para `rejeitados/`.
- `./agente.py status` mostra a fila e os próximos posts.

## Configuração inicial (uma vez)
1. No Google Cloud Console: crie um projeto, ative a **YouTube Data API v3**, configure a tela de consentimento OAuth (tipo Externo, adicione seu e-mail como usuário de teste) e crie uma credencial **ID do cliente OAuth → App para computador**. Baixe o JSON e salve como `client_secret.json` nesta pasta.
2. `.venv/bin/python agente.py auth` abre o navegador para o login no canal.
3. `.venv/bin/python agente.py run --simular` mostra o que seria agendado.
4. `./instalar-agendamento.sh` faz rodar sozinho a cada 2 horas.
5. (Opcional) Avisos no Telegram: copie `.env.exemplo` para `.env` e preencha `TELEGRAM_CHAT_ID` e `TELEGRAM_KEYCHAIN_SERVICE` (token no Keychain do macOS) ou `TELEGRAM_BOT_TOKEN` e teste com `.venv/bin/python agente.py teste-aviso`.

## Painel de desempenho
`.venv/bin/python agente.py painel` coleta views, likes e comentários na API do YouTube e abre `painel/index.html`. O painel é montado sobre o template de dashboard [Tabler](https://tabler.io) e mostra:
- números principais: views, publicados e agendados, likes e fila em dias;
- gráficos de views acumuladas, views por produto, média de views por horário e gancho do Brier × views;
- tabelas dos vídeos publicados e dos próximos agendamentos.

Cada rodada automática regrava o painel. A página aberta no navegador recarrega sozinha a cada 10 min.

**Versão pública:** com `painel_publico_repo` no `config.json`, cada rodada também publica o painel em https://dheiver2.github.io/shorts-painel, num repositório próprio com um commit único e sem histórico. Nessa versão, vídeos que ainda não saíram aparecem só com horário e produto, e os alertas internos não aparecem.

## Mesma grade nas 3 redes
YouTube Shorts, Instagram Reels e TikTok seguem a mesma grade: 14 por dia, um por hora das 07:00 às 20:00, o mesmo vídeo na mesma hora. O YouTube agenda sozinho (`publishAt`). Instagram e TikTok não aceitam agendamento pela API, então o launchd `br.mangaba.shorts-instagram` roda `agente.py espelhos` às 07:01, 08:01 … 20:01 e publica nas duas o que acabou de sair no YouTube. Se o Mac estiver desligado, o atraso aceito é de 2 h; depois disso, o post é dado como perdido e você é avisado.

## Instagram (Reels de teste)
O mesmo vídeo sai também no Instagram como **Reel de teste**, na mesma hora em que vai ao ar no YouTube (14 por dia, 07:00–20:00). O Reel de teste é mostrado primeiro para quem não segue a conta e **não entra na grade do perfil**. Ele só vai para a grade se você promover pelo app (`graduation_strategy` MANUAL).

- A API do Instagram não agenda, então um segundo launchd (`br.mangaba.shorts-instagram`) roda `agente.py instagram` às 07:01, 08:01 … 20:01 e publica o que acabou de sair no YouTube.
- Legenda: título e descrição do YouTube, com as hashtags do Brier, sem o `#Shorts`.
- Se o Mac estiver desligado, o atraso aceito é de `instagram.max_atraso_h` (2 h). Depois disso, o vídeo não sai no Instagram e você é avisado.
- Só vale para vídeos que saem no YouTube a partir da primeira rodada com token.
- Configuração: uma conta profissional do Instagram ligada a uma Página do Facebook e um token com `instagram_basic` e `instagram_content_publish`. Guarde o token no Keychain (`security add-generic-password -s Instagram -a INSTAGRAM_TOKEN -w 'TOKEN'`) e preencha `INSTAGRAM_KEYCHAIN_SERVICE=Instagram` e `INSTAGRAM_USER_ID` no `.env`. Sem isso, o comando não faz nada.
- Para publicar como Reel comum, na grade, use `"instagram": {"teste": false}` no `config.json`.

## TikTok
- Publica pelo Content Posting API (Direct Post), com a mesma legenda do Instagram. Vídeo marcado como IA se `conteudo_sintetico` for `true`.
- **Enquanto o app não passar pela auditoria do TikTok, os posts saem privados (só você vê)** e a conta precisa estar privada. O agente pede `PUBLIC_TO_EVERYONE` e, se o TikTok não oferecer, usa `SELF_ONLY` e avisa no Telegram.
- Configuração: crie um app em developers.tiktok.com com **Login Kit** e **Content Posting API** (Direct Post ligado), escopos `user.info.basic` e `video.publish`, e o redirect `https://dheiver2.github.io/shorts-agendador/tiktok/`. Coloque `TIKTOK_CLIENT_KEY` no `.env`, o segredo no Keychain (`security add-generic-password -s TikTok -a TIKTOK_CLIENT_SECRET -w 'SEGREDO'` e `TIKTOK_KEYCHAIN_SERVICE=TikTok` no `.env`) e rode `.venv/bin/python agente.py tiktok-auth`. O token renova sozinho (o login vale 1 ano).

## Brier (decisões com probabilidade)
Cada vídeo passa pelo Brier (`brier-v5` no Mangaba Router, chave `BRIER_KEY` no `.env`), numa só chamada com 16 perguntas:
- **gancho** (escala 0–4): dentro de cada dia, o vídeo de gancho mais forte fica com o melhor horário (ordem de `horarios`). A sequência da fila continua valendo entre os dias.
- **enganoso** e **risco** (sim/não): acima de `corte_enganoso` (0,85) ou `corte_risco` (0,8), o vídeo vai para `revisar/` e você é avisado. Basta corrigir o `.txt` e devolver o vídeo para a pasta.
- **público** (gestor, técnico, provedor ou geral): fica registrado, para cruzar com as views na Fase 2.
- **hashtags** (12 candidatas, sim/não para cada): até 4 com probabilidade ≥ 0,6 entram na descrição, antes do `#Shorts`.

`agente.py avaliar` passa a fila inteira e mostra o ranking. Cada rodada avalia também até 6 vídeos ainda sem nota, para avisar cedo. As notas ficam em cache no `estado.json`: só são refeitas se o título ou a descrição mudarem. Se o Brier estiver fora do ar, o envio segue sem ele.

## O que cada rodada faz
- Agenda os horários vagos de hoje e de amanhã.
- **Repõe horário perdido** (por exemplo, com o Mac desligado): o vídeo vai para uma hora livre do mesmo dia ou do dia seguinte, sem passar da meta da semana nem de `max_por_dia`. Primeiro usa as horas da lista `horarios` que o dia não usa e depois as de `horarios_reserva`.
- **Confere no YouTube** os agendamentos futuros e os dos últimos 3 dias: vídeo apagado, rejeitado, agendamento alterado ou vídeo que não ficou público no horário geram aviso.
- **Recusa duplicados** pelo hash do arquivo. O duplicado vai para `rejeitados/`.
- **Avisa no Telegram** só quando há novidade (agendou, recusou, deu erro ou problema na conferência), e uma vez por dia quando a fila tem menos de `avisar_fila_abaixo_dias` dias de vídeo.
- **Cota da API:** se o YouTube recusar por cota, o agente guarda até quando ela vale (meia-noite no Pacífico, 05:00 em Brasília), não tenta de novo antes disso e avisa uma vez. Os melhores horários de cada dia sobem primeiro.
- Duas rodadas nunca rodam ao mesmo tempo (trava em `.agente.lock`).

## Testes
`.venv/bin/python -m pytest -q`

## Auditoria da API (não foi necessária neste canal)
Teste de 07/11/2026: o vídeo ficou "Agendado" no Studio, sem trava. Se um dia aparecer "privado (restrito)", vale o texto abaixo.

Projetos da API do YouTube que não passaram pela auditoria do Google só conseguem subir vídeos **privados**. Os vídeos ficam travados como privados e não publicam no horário.
Para liberar, peça a auditoria gratuita em https://support.google.com/youtube/contact/yt_api_form ("YouTube API Services – Audit and Quota Extension"). Costuma levar de alguns dias a algumas semanas.

## Ajustes (`config.json`)
- `posts_por_semana`: total da semana (98 = 14 por dia). A divisão por 7 dá a quantidade por dia; o que sobra vai para os dias em `dias_extra` (sábado e domingo).
- `horarios`: lista do melhor para o pior horário. Cada dia usa os primeiros N. Depois de algumas semanas, confira no YouTube Studio → Análises → Público → "Quando seus espectadores estão no YouTube" e ajuste.
- `dias_a_frente`: quantos dias à frente deixar agendado (padrão 1, para caber na cota diária).
- `tags`, `descricao_padrao`, `conteudo_sintetico` (marque `true` se o vídeo for gerado por IA).
