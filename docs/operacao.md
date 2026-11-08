---
title: Operação
nav_order: 4
---

# Operação

## Comandos

```bash
.venv/bin/python agente.py status        # fila, semana e próximos agendamentos
.venv/bin/python agente.py run --simular # o que seria feito, sem enviar nada
.venv/bin/python agente.py run           # rodada manual (o launchd já faz a cada 2 h)
.venv/bin/python agente.py avaliar       # ranking do Brier para a fila inteira
.venv/bin/python agente.py teste-aviso   # mensagem de teste no Telegram
.venv/bin/python -m pytest -q            # testes
```

## Pastas

| Pasta | O que tem |
|---|---|
| `Shorts-para-postar/` | a fila: `.mp4` e, se quiser, um `.txt` com o mesmo nome (1ª linha = título, resto = descrição) |
| `postados/` | o que já foi agendado no YouTube |
| `rejeitados/` | formato errado (horizontal, mais de 3 min, arquivo corrompido) ou duplicado |
| `revisar/` | segurado pelo Brier; corrija o `.txt` e devolva o vídeo para a fila |

## O que cada rodada faz

1. Agenda os horários vagos de hoje e de amanhã.
2. **Repõe horário perdido** (por exemplo, com o Mac desligado) numa hora livre da mesma semana, sem passar de 30 na semana nem de 6 no dia.
3. **Confere no YouTube** os agendamentos futuros e os dos últimos 3 dias. Vídeo apagado, rejeitado, com agendamento alterado ou que não ficou público até 30 minutos depois do horário gera aviso.
4. **Recusa duplicados** pelo hash do arquivo.
5. **Avisa no Telegram** só quando há novidade, e uma vez por dia quando a fila está acabando.

Duas rodadas nunca rodam ao mesmo tempo (trava em `.agente.lock`).

## Limites conhecidos

- **Roda no Mac.** Se o Mac ficar desligado por mais de um dia, os horários seguintes ficam sem vídeo até ele voltar. A partir daí o agente repõe o que perdeu dentro da mesma semana.
- **Cota da API:** cerca de 6 envios por dia no plano gratuito.
- **Vídeos já enviados não são editados.** Mudar a descrição exigiria uma permissão maior no YouTube e um novo login.
