---
title: Plano de capacidades
nav_order: 5
---

# Plano de ação: aumento de capacidades do agendador de Shorts

Situação em 07/11/2026:

- O agente roda no Mac via launchd às 08:00 e às 17:00.
- Ele agenda 30 Shorts por semana no canal Dheiver Santos: 4 por dia de segunda a sexta e 5 no sábado e no domingo. O YouTube publica sozinho no horário marcado (`publishAt`).
- Primeiro lote real: 5 vídeos agendados para 08/11 e 25 na fila.

## O que limita o agente hoje

1. **Horários fixos.** 12, 17, 19, 21 e 9h vêm de uma média do mercado, não do público deste canal.
2. **Depende do Mac.** Se o Mac ficar desligado por mais de um dia, horários passam sem vídeo. Na primeira rodada (07/11 às 20:55) o horário das 21:00 já ficou vazio por causa da margem de 20 min.
3. **Ninguém é avisado.** Erros de cota, token expirado e fila vazia só aparecem no `agente.log`.
4. **Título e descrição saem do nome do arquivo ou de um `.txt` escrito à mão.** Não há hashtags, legenda nem idioma.
5. **Vídeos fora do formato vão para `rejeitados/`.** O agente não tenta corrigir.
6. **Sem retorno de desempenho.** O agente não sabe quais vídeos funcionaram.
7. **Cota gratuita da API** (10.000 unidades por dia): dá no máximo 6 envios por dia. A regra atual usa 4 ou 5.

## Fase 1: confiabilidade (feita em 07/11/2026)

Tudo implementado e com 23 testes. Primeira reposição real: o horário perdido de 07/11 às 21:00 foi reposto em 08/11 às 15:00. Falta só o token do bot do Telegram no `.env`.

| # | Ação | Como | Pronto quando |
|---|------|------|---------------|
| 1.1 | Aviso no Telegram | Mandar uma mensagem por rodada: o que foi agendado, erros, quantos vídeos sobram na fila e em quantos dias ela acaba. Usar o bot que já existe. | Mensagem chega a cada rodada. Aviso quando a fila tiver menos de 3 dias. |
| 1.2 | Rodar a cada 2 horas | `StartCalendarInterval` de 2 em 2 h em vez de 08:00 e 17:00. O agente já não duplica posts. | Nenhum horário vazio com o Mac ligado. |
| 1.3 | Recuperar horário perdido | Se um horário passou sem vídeo, o vídeo vai para o próximo horário vago, sem ultrapassar 30 por semana. | `status` mostra 30 agendados por semana, mesmo com o Mac desligado um dia. |
| 1.4 | Conferir o que foi agendado | A cada rodada, `videos.list` dos agendamentos futuros. Se algum sumiu, ficou restrito ou perdeu o `publishAt`, avisar. | Vídeo apagado no Studio aparece no aviso. |
| 1.5 | Evitar duplicados | Calcular o hash de cada arquivo e guardar em `estado.json`. Recusar vídeo já enviado. | O mesmo vídeo colocado duas vezes não sobe duas vezes. |
| 1.6 | Testes | Testes com pytest para `horarios_do_dia`, `proximos_horarios`, `metadados` e para a validação. | `pytest` passa e cobre a regra de 30 por semana. |

## Brier no agendador (feito em 07/11/2026)

O Brier dá nota de gancho, trava títulos enganosos ou arriscados, classifica o público e escolhe as hashtags. Primeira passada na fila de 24 vídeos: ganchos de 0,50 a 0,71, nenhum vídeo segurado, cerca de 15 s por vídeo. A Fase 2 deve cruzar `gancho` e `publico` com as views das primeiras 48 h para calibrar a nota e os cortes.

## Fase 2: horários com dados do próprio canal (semana de 16/11)

| # | Ação | Como | Pronto quando |
|---|------|------|---------------|
| 2.1 | Ranking de horários | Usar o histórico dos 756 vídeos e dos Shorts novos. Pela YouTube Analytics API (escopo `yt-analytics.readonly`), pegar as views das primeiras 48 h e agrupar por dia da semana e hora de publicação. | Comando `agente.py horarios` imprime o ranking com o número de vídeos de cada faixa. |
| 2.2 | Ajuste automático | Uma vez por semana, reordenar `horarios` no `config.json` com o ranking. Manter 30 por semana. Trocar no máximo 1 horário por semana. | `config.json` atualizado e registrado no log. |
| 2.3 | Teste A/B de horário | Reservar 1 horário por semana para testar uma hora fora do ranking e comparar as views em 48 h. | Relatório mensal compara horário testado com horário do ranking. |

Atenção: a API não informa em que horário o público está online. O ranking só mede o desempenho de quando cada vídeo foi publicado. É preciso ter pelo menos 5 vídeos em cada faixa antes de mudar algum horário.

## Fase 3: conteúdo gerado pelo agente (semanas de 23/11 e 30/11)

| # | Ação | Como | Pronto quando |
|---|------|------|---------------|
| 3.1 | Transcrição local | Transcrever o áudio de cada vídeo com o whisper local antes do envio. | Um `.srt` para cada vídeo da fila. |
| 3.2 | Título, descrição e hashtags com LLM | Mandar a transcrição para o Mangaba Router (titan). O `.txt` escrito à mão continua tendo prioridade. Limites: título com até 100 caracteres, de 3 a 5 hashtags e chamada para o site. | Vídeo sem `.txt` sobe com título coerente com o que é falado. |
| 3.3 | Legenda no YouTube | Enviar o `.srt` por `captions.insert` (precisa do escopo `youtube.force-ssl` e de um novo login). | Legenda em PT-BR aparece no vídeo. |
| 3.4 | Correção de formato | Vídeo horizontal vira 9:16 com fundo desfocado, por ffmpeg. Vídeo com mais de 180 s é cortado no silêncio mais próximo, ou recusado se for longo demais. | `rejeitados/` só recebe arquivo corrompido. |
| 3.5 | Playlist por produto | Pelo prefixo do título (Router, Agent, Brier…), incluir o vídeo na playlist do produto com `playlistItems.insert`. | Cada produto tem sua playlist, atualizada sozinha. |

## Fase 4: escala e mais plataformas (dezembro)

| # | Ação | Como | Pronto quando |
|---|------|------|---------------|
| 4.1 | Sair do Mac | Rodar o agente como serviço systemd num servidor próprio (VPS ou Golden). A pasta é sincronizada por rsync ou por uma pasta compartilhada. | O agente roda com o Mac desligado. |
| 4.2 | Aumentar a cota | Pedir ao Google mais cota da API (formulário de auditoria) para passar de 6 envios por dia. | Cota liberada, ou a regra continua dentro de 6 por dia. |
| 4.3 | Outras plataformas | Feito para Instagram Reels (08/11/2026), na mesma grade e fora da grade do perfil. TikTok foi testado e removido: sem auditoria a API só posta privado. | O mesmo vídeo vai para YouTube e Instagram, com estado separado por plataforma. |
| 4.4 | Integração com o agente de comentários | Avisar o agente de comentários de cada vídeo novo, para ele responder nas primeiras horas. | Comentários respondidos até 2 h depois da publicação. |
| 4.5 | Relatório semanal | Mandar no Telegram: views, retenção e inscritos ganhos por vídeo, os 3 melhores e os 3 piores da semana, e quantos dias de fila restam. | Relatório toda segunda às 08:00. |

## Indicadores

- **Pontualidade:** % dos horários da semana que receberam vídeo. Meta: 100%.
- **Erros silenciosos:** falhas que não geraram aviso. Meta: 0.
- **Média de views em 48 h por Short:** guardar a média das 2 primeiras semanas como base e medir depois da Fase 2.
- **Trabalho manual por vídeo:** hoje é o `.txt` escrito à mão. Meta depois da Fase 3: só colocar o arquivo na pasta.

## Ordem de execução

Primeiro a Fase 1 inteira, porque sem confiabilidade e sem avisos as outras fases não dá para medir. Depois a 2.1 e a 3.2, que dão mais retorno pelo esforço. O resto segue na ordem das tabelas.
