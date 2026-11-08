# Agendador de Shorts

Pega os vídeos da pasta `~/Movies/Shorts-para-postar` e agenda **30 por semana** no YouTube: 4 por dia de segunda a sexta (12:00, 17:00, 19:00, 21:00) e 5 no sábado e no domingo (mais 09:00), horário de Brasília.
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
4. `./instalar-agendamento.sh` faz rodar sozinho todo dia às 08:00 e 17:00.

## Auditoria da API (não foi necessária neste canal)
Teste de 07/11/2026: o vídeo ficou "Agendado" no Studio, sem trava. Se um dia aparecer "privado (restrito)", vale o texto abaixo.

Projetos da API do YouTube que não passaram pela auditoria do Google só conseguem subir vídeos **privados**. Os vídeos ficam travados como privados e não publicam no horário.
Para liberar, peça a auditoria gratuita em https://support.google.com/youtube/contact/yt_api_form ("YouTube API Services – Audit and Quota Extension"). Costuma levar de alguns dias a algumas semanas.

## Ajustes (`config.json`)
- `posts_por_semana`: total da semana (30). A divisão por 7 dá a quantidade por dia; o que sobra vai para os dias em `dias_extra` (sábado e domingo).
- `horarios`: lista do melhor para o pior horário. Cada dia usa os primeiros N. Limite da cota gratuita da API: 6 envios por dia. Depois de algumas semanas, confira no YouTube Studio → Análises → Público → "Quando seus espectadores estão no YouTube" e ajuste.
- `dias_a_frente`: quantos dias à frente deixar agendado (padrão 1, para caber na cota diária).
- `tags`, `descricao_padrao`, `conteudo_sintetico` (marque `true` se o vídeo for gerado por IA).
