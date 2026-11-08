---
title: Brier
nav_order: 3
---

# Brier no agendador

Cada vídeo passa pelo **Brier** (`brier-v5`, pelo Mangaba Router em `/v1/systemone`) numa chamada só, com 16 perguntas, cada uma respondida com uma probabilidade. A chamada leva cerca de 15 s por vídeo.

| Pergunta | Tipo | O que o agendador faz com a resposta |
|---|---|---|
| `gancho` | escala 0–4 | dentro do dia, o vídeo de gancho mais forte fica com o melhor horário |
| `enganoso` | sim/não | ≥ 0,85: o vídeo vai para `revisar/` e você recebe um aviso |
| `risco` | sim/não | ≥ 0,80 (dado pessoal, promessa garantida, ofensa, ataque a concorrente): `revisar/` |
| `publico` | escolha | gestor, técnico, provedor ou geral; fica registrado para comparar com as views |
| `tag_*` (12) | sim/não | até 4 hashtags com probabilidade ≥ 0,6 entram na descrição |

Hashtags candidatas: `#InteligenciaArtificial` `#IA` `#AgentesDeIA` `#Automacao` `#IAparaEmpresas` `#Telecom` `#Tecnologia` `#Programacao` `#Empreendedorismo` `#ChatGPT` `#Atendimento` `#DadosEPrivacidade`.

## Regras

- **A ordem da fila entre os dias continua valendo.** O Brier só troca a ordem dentro do mesmo dia.
- **Cache:** as notas ficam no `estado.json` e só são refeitas se o arquivo, o título ou a descrição mudarem.
- **Sem Brier, o envio continua.** Se o Brier estiver fora do ar, os vídeos seguem a ordem da fila e saem sem hashtags extras, e você recebe um aviso.
- **Aviso antecipado:** cada rodada avalia até 6 vídeos da fila que ainda não têm nota. Assim, um vídeo arriscado é avisado dias antes da vez dele.

## Primeira passada (07/11/2026, 24 vídeos)

- Gancho de **0,50 a 0,71**. Os mais fortes: "Brier roda no seu servidor sem custo por chamada" (0,71), "Agente no Telegram sem programar" (0,70) e "Personalidade e memória" (0,69).
- **Nenhum vídeo segurado.** O maior risco foi 29% e o maior enganoso foi 22%.
- As hashtags específicas apareceram onde fazem sentido: `#Telecom` no vídeo de provedores, `#DadosEPrivacidade` no de IA on-premises e `#Atendimento` no de atendimento fora do expediente.

{: .note }
A nota de gancho ainda **não foi comparada** com views reais. Por isso ela só decide a ordem dentro do dia, que tem impacto pequeno. Essa comparação é a [Fase 2 do plano](plano).

```bash
.venv/bin/python agente.py avaliar   # ranking da fila inteira
```
