"""Brier no agendador: uma chamada por vídeo responde todas as perguntas numa passada.

O que o Brier decide:
  - gancho (escala 0–4): força do título para parar o feed → o mais forte do dia pega o melhor horário;
  - enganoso / risco (sim/não): título que promete o que não entrega, ou conteúdo arriscado → vai para revisar/;
  - publico (escolha): para quem é o vídeo (registro; base para a Fase 2);
  - hashtags (sim/não, uma por candidata): entram na descrição as que passam do corte.

Se o Brier estiver fora do ar, o agendador segue sem ele (ordem da fila, sem hashtags extras).
Resultados ficam em cache no estado.json pelo hash do arquivo + texto (não paga duas vezes).
"""
import hashlib
import json
import time
import urllib.error
import urllib.request

VERSAO = 1  # mude ao alterar perguntas: invalida o cache

HASHTAGS = {
    "InteligenciaArtificial": "trata de inteligência artificial em geral",
    "IA": "fala de IA de forma direta",
    "AgentesDeIA": "mostra agentes de IA que executam tarefas sozinhos",
    "Automacao": "mostra automação de processos ou atendimento",
    "IAparaEmpresas": "é sobre usar IA em empresas e negócios",
    "Telecom": "é para provedores de internet ou telecom",
    "Tecnologia": "é sobre tecnologia de forma ampla",
    "Programacao": "fala de código, API ou desenvolvimento",
    "Empreendedorismo": "fala com donos de negócio e empreendedores",
    "ChatGPT": "compara ou cita ChatGPT, OpenAI ou modelos de linguagem conhecidos",
    "Atendimento": "é sobre atendimento ao cliente (WhatsApp, Telegram, chat)",
    "DadosEPrivacidade": "fala de dados, LGPD, privacidade ou soberania de dados",
}

PERGUNTAS = {
    "gancho": {
        "type": "score",
        "instructions": "Num feed de Shorts, quão forte é este título para fazer alguém parar de rolar e assistir? "
                        "Considere curiosidade, dor clara, benefício concreto e clareza em poucas palavras.",
        "criteria": ["fraco", "regular", "bom", "muito bom", "excelente"],
    },
    "enganoso": {
        "type": "noul",
        "instructions": "O título promete algo que a descrição não sustenta, ou exagera a ponto de enganar quem clica?",
        "criteria": {"true": "enganoso", "false": "honesto"},
    },
    "risco": {
        "type": "noul",
        "instructions": "O texto tem algo que não deveria ir ao ar: dado pessoal (telefone, CPF, e-mail de pessoa), "
                        "ofensa, promessa garantida de dinheiro ou resultado, afirmação de saúde, ou ataque a concorrente pelo nome?",
        "criteria": {"true": "arriscado", "false": "seguro"},
    },
    "publico": {
        "type": "choice",
        "instructions": "Para quem é este vídeo principalmente?",
        "criteria": {
            "gestor": "donos e gestores de empresa",
            "tecnico": "desenvolvedores e equipe de TI",
            "provedor": "provedores de internet e telecom",
            "geral": "público geral curioso por tecnologia",
        },
    },
}
for _tag, _quando in HASHTAGS.items():
    PERGUNTAS[f"tag_{_tag}"] = {
        "type": "noul",
        "instructions": f"A hashtag #{_tag} descreve bem este vídeo? Só sim se o vídeo {_quando}.",
        "criteria": {"true": "combina", "false": "não combina"},
    }


def estado_texto(titulo, descricao, canal):
    return "\n".join([
        f"Short do YouTube (vídeo vertical de até 3 minutos) do canal {canal}.",
        f"Título: {titulo}",
        f"Descrição: {descricao[:1500] or '(sem descrição)'}",
    ])


def chave_cache(sha, titulo, descricao):
    return hashlib.sha256(f"{VERSAO}|{sha}|{titulo}|{descricao}".encode()).hexdigest()[:24]


def consultar(cfg_brier, chave_api, estado, timeout=90, tentativas=2):
    corpo = json.dumps({"model": cfg_brier["modelo"], "state": estado, "questions": PERGUNTAS}).encode()
    req = urllib.request.Request(
        cfg_brier["url"].rstrip("/") + "/systemone", corpo,
        {"Content-Type": "application/json", "Authorization": "Bearer " + chave_api,
         "User-Agent": "shorts-agendador/1.0"})
    erro = None
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            erro = f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # rede, timeout
            erro = str(e)[:200]
        time.sleep(5 * (i + 1))
    raise RuntimeError(erro or "falha desconhecida")


def interpretar(resp, cfg_brier):
    """Converte a resposta do Brier no que o agendador usa."""
    a = resp.get("answers", {})
    g = a.get("gancho", {}).get("probabilities", {})
    gancho = sum(int(k) * float(p) for k, p in g.items()) / 4 if g else 0.5
    tags = sorted(((float(v.get("noul") or 0), k[4:]) for k, v in a.items() if k.startswith("tag_")), reverse=True)
    escolhidas = [t for p, t in tags if p >= cfg_brier["corte_hashtag"]][:cfg_brier["max_hashtags"]]
    pub = a.get("publico", {})
    return {
        "gancho": round(gancho, 3),
        "enganoso": round(float(a.get("enganoso", {}).get("noul") or 0), 3),
        "risco": round(float(a.get("risco", {}).get("noul") or 0), 3),
        "publico": pub.get("choice"),
        "publico_conf": round(float(pub.get("confidence") or 0), 3),
        "hashtags": escolhidas,
        "modelo": resp.get("model"),
    }


def segurar(av, cfg_brier):
    """Motivo para segurar o vídeo em revisar/, ou None."""
    if av["risco"] >= cfg_brier["corte_risco"]:
        return f"Brier viu risco no texto ({av['risco']:.0%})"
    if av["enganoso"] >= cfg_brier["corte_enganoso"]:
        return f"Brier achou o título enganoso ({av['enganoso']:.0%})"
    return None


def descricao_com_hashtags(desc, hashtags):
    """Acrescenta as hashtags que ainda não estão no texto, antes do #Shorts final."""
    baixo = desc.lower()
    novas = [f"#{t}" for t in hashtags if f"#{t.lower()}" not in baixo]
    if not novas:
        return desc
    linhas = desc.rstrip().split("\n")
    if linhas and linhas[-1].strip().lower() == "#shorts":
        linhas[-1] = " ".join(novas + ["#Shorts"])
    else:
        linhas.append(" ".join(novas))
    return "\n".join(linhas)[:5000]


def distribuir(videos_aval, slots_rank):
    """Casa vídeos com horários do mesmo dia: maior gancho → melhor horário (menor rank).
    videos_aval: [(video, gancho)]; slots_rank: [(rank, slot)]. Devolve [(slot, video)]."""
    vs = sorted(videos_aval, key=lambda x: -x[1])
    ss = sorted(slots_rank, key=lambda x: x[0])
    return [(s, v) for (_, s), (v, _) in zip(ss, vs)]
