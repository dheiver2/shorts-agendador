"""Publicação dos mesmos vídeos como Reels de teste no Instagram.

A API do Instagram não agenda: o post sai na hora da chamada. Por isso o agente
roda de hora em hora e publica o vídeo cujo horário do YouTube acabou de chegar.
O Reel sai em modo de teste (trial reel): é mostrado primeiro para quem não segue
a conta e não entra na grade do perfil. Com `graduation_strategy` MANUAL ele só
vai para a grade se você promover pelo app.

Fluxo da API (Content Publishing, login pelo Facebook):
  1. POST /{ig_user}/media  media_type=REELS, upload_type=resumable, trial_params
  2. POST rupload.facebook.com/ig-api-upload/{versão}/{container}  (bytes do vídeo)
  3. GET  /{container}?fields=status_code  até FINISHED
  4. POST /{ig_user}/media_publish  creation_id={container}
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

VERSAO = "v23.0"
GRAPH = f"https://graph.facebook.com/{VERSAO}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSAO}"
LEGENDA_MAX = 2200


def legenda(titulo, desc):
    """Título + descrição do YouTube, sem o #Shorts (no Instagram vira #reels)."""
    corpo = (desc or "").replace("#Shorts", "").replace("#shorts", "").strip()
    texto = titulo.strip() + ("\n\n" + corpo if corpo else "")
    return texto[:LEGENDA_MAX]


def pendentes(estado, agora_utc, desde_utc, max_atraso_h, de_iso, chave="instagram"):
    """Divide os vídeos do YouTube ainda sem Instagram em (a_publicar, perdidos).
    a_publicar: horário já chegou, dentro do atraso aceito; o mais antigo primeiro.
    perdidos: passaram do atraso (Mac desligado, por exemplo) e não saem mais."""
    a_publicar, perdidos = [], []
    for a in estado.get("agendados", []):
        if chave in a:
            continue
        quando = de_iso(a["publish_at"])
        if quando < desde_utc or quando > agora_utc:
            continue
        if agora_utc - quando > timedelta(hours=max_atraso_h):
            perdidos.append(a)
        else:
            a_publicar.append(a)
    a_publicar.sort(key=lambda a: a["publish_at"])
    return a_publicar, perdidos


def _req(url, dados=None, cabecalhos=None, metodo=None, corpo=None, timeout=60):
    if dados is not None:
        corpo = urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, headers=cabecalhos or {}, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Instagram HTTP {e.code}: {detalhe}") from None


def publicar_reel(token, ig_user, video, texto, teste=True, espera_max_s=600, chamar=_req, dormir=time.sleep):
    """Sobe o arquivo e publica. Devolve o id do post no Instagram."""
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": texto, "access_token": token}
    if teste:
        params["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
    container = chamar(f"{GRAPH}/{ig_user}/media", dados=params)["id"]

    tamanho = video.stat().st_size
    up = chamar(f"{RUPLOAD}/{container}", corpo=video.read_bytes(), metodo="POST", timeout=600,
                cabecalhos={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(tamanho)})
    if not up.get("success", True):
        raise RuntimeError(f"Upload do vídeo recusado: {up}")

    inicio = time.time()
    while True:
        st = chamar(f"{GRAPH}/{container}?" + urllib.parse.urlencode(
            {"fields": "status_code,status", "access_token": token}))
        codigo = st.get("status_code")
        if codigo == "FINISHED":
            break
        if codigo in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram não processou o vídeo: {st.get('status') or codigo}")
        if time.time() - inicio > espera_max_s:
            raise RuntimeError("Instagram demorou demais para processar o vídeo")
        dormir(10)

    return chamar(f"{GRAPH}/{ig_user}/media_publish",
                  dados={"creation_id": container, "access_token": token})["id"]
