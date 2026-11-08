"""Publicação dos mesmos vídeos no TikTok (Content Posting API, Direct Post).

Como no Instagram, a API não agenda: o agente roda de hora em hora e publica o
vídeo cujo horário do YouTube acabou de chegar.

Atenção: enquanto o app do TikTok não passar pela auditoria do Content Posting
API, o TikTok só aceita post PRIVADO (SELF_ONLY) e a conta precisa estar privada.
O agente pede a privacidade de `tiktok.privacidade` (PUBLIC_TO_EVERYONE) e, se a
conta não oferecer essa opção, usa SELF_ONLY e avisa.

Login (uma vez): `agente.py tiktok-auth` abre o navegador; depois de autorizar,
a página de retorno mostra o código, que você cola no terminal.
"""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://open.tiktokapis.com/v2"
AUTORIZAR = "https://www.tiktok.com/v2/auth/authorize/"
ESCOPOS = "user.info.basic,video.publish"
LEGENDA_MAX = 2200
MB = 1024 * 1024


def _req(url, json_corpo=None, form=None, token=None, metodo=None, corpo=None, cabecalhos=None, timeout=60):
    cab = dict(cabecalhos or {})
    if token:
        cab["Authorization"] = f"Bearer {token}"
    if json_corpo is not None:
        corpo = json.dumps(json_corpo).encode()
        cab["Content-Type"] = "application/json; charset=UTF-8"
    elif form is not None:
        corpo = urllib.parse.urlencode(form).encode()
        cab["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=corpo, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode() or "{}"
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"TikTok HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from None
    return json.loads(txt) if txt.strip().startswith("{") else {}


def _checar(resp):
    erro = resp.get("error") or {}
    if erro.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok: {erro.get('code')} — {erro.get('message', '')}")
    return resp.get("data") or {}


# ---------- login ----------

def url_login(client_key, redirect_uri, estado=None):
    q = {"client_key": client_key, "scope": ESCOPOS, "response_type": "code",
         "redirect_uri": redirect_uri, "state": estado or secrets.token_urlsafe(16)}
    return AUTORIZAR + "?" + urllib.parse.urlencode(q)


def _guardar(resp, agora):
    if "access_token" not in resp:
        raise RuntimeError(f"TikTok recusou o login: {resp.get('error_description') or resp}")
    return {"access_token": resp["access_token"], "refresh_token": resp["refresh_token"],
            "open_id": resp.get("open_id"), "expira_em": agora + int(resp.get("expires_in", 86400)),
            "refresh_expira_em": agora + int(resp.get("refresh_expires_in", 31536000))}


def trocar_codigo(client_key, client_secret, codigo, redirect_uri, chamar=_req, agora=None):
    resp = chamar(f"{API}/oauth/token/", form={
        "client_key": client_key, "client_secret": client_secret, "code": codigo.strip(),
        "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    return _guardar(resp, int(agora or time.time()))


def token_valido(tok, client_key, client_secret, chamar=_req, agora=None):
    """Devolve (token, mudou). Renova com o refresh_token quando falta menos de 1 h."""
    agora = int(agora or time.time())
    if tok["expira_em"] - agora > 3600:
        return tok, False
    if tok["refresh_expira_em"] <= agora:
        raise RuntimeError("Login do TikTok venceu. Rode no Mac: .venv/bin/python agente.py tiktok-auth")
    resp = chamar(f"{API}/oauth/token/", form={
        "client_key": client_key, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    return _guardar(resp, agora), True


# ---------- publicação ----------

def pedacos(tamanho):
    """Divisão exigida pelo TikTok: até 64 MB vai inteiro; acima, pedaços de 10 MB
    e o último absorve o resto. Devolve (chunk_size, total, [(início, fim)])."""
    if tamanho <= 64 * MB:
        return tamanho, 1, [(0, tamanho - 1)]
    tam = 10 * MB
    total = tamanho // tam
    faixas = [(i * tam, (i + 1) * tam - 1) for i in range(total - 1)]
    faixas.append(((total - 1) * tam, tamanho - 1))
    return tam, total, faixas


def publicar(token, video, texto, privacidade="PUBLIC_TO_EVERYONE", ia=False,
             espera_max_s=600, chamar=_req, dormir=time.sleep):
    """Sobe e publica. Devolve dict com publish_id, privacidade usada e id do post (se houver)."""
    info = _checar(chamar(f"{API}/post/publish/creator_info/query/", json_corpo={}, token=token))
    opcoes = info.get("privacy_level_options") or []
    usada = privacidade if privacidade in opcoes else ("SELF_ONLY" if "SELF_ONLY" in opcoes else privacidade)
    tamanho = video.stat().st_size
    chunk, total, faixas = pedacos(tamanho)
    post_info = {"title": texto[:LEGENDA_MAX], "privacy_level": usada,
                 "disable_comment": False, "disable_duet": False, "disable_stitch": False}
    if ia:
        post_info["is_aigc"] = True
    corpo = {"post_info": post_info,
             "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho,
                             "chunk_size": chunk, "total_chunk_count": total}}
    try:
        ini = _checar(chamar(f"{API}/post/publish/video/init/", token=token, json_corpo=corpo))
    except RuntimeError as e:
        # app sem auditoria: o TikTok lista "público" nas opções mas só aceita privado
        if "unaudited_client" not in str(e) or usada == "SELF_ONLY":
            raise
        usada = post_info["privacy_level"] = "SELF_ONLY"
        ini = _checar(chamar(f"{API}/post/publish/video/init/", token=token, json_corpo=corpo))
    publish_id, upload_url = ini["publish_id"], ini["upload_url"]

    with video.open("rb") as f:
        for a, b in faixas:
            f.seek(a)
            pedaco = f.read(b - a + 1)
            chamar(upload_url, corpo=pedaco, metodo="PUT", timeout=600, cabecalhos={
                "Content-Type": "video/mp4", "Content-Length": str(len(pedaco)),
                "Content-Range": f"bytes {a}-{b}/{tamanho}"})

    inicio = time.time()
    while True:
        st = _checar(chamar(f"{API}/post/publish/status/fetch/", token=token,
                            json_corpo={"publish_id": publish_id}))
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
            return {"publish_id": publish_id, "privacidade": usada, "post_id": str(ids[0]) if ids else None}
        if status == "FAILED":
            raise RuntimeError(f"TikTok não publicou: {st.get('fail_reason', 'sem motivo')}")
        if time.time() - inicio > espera_max_s:
            raise RuntimeError("TikTok demorou demais para processar o vídeo")
        dormir(10)


# ---------- envio com aprovação manual (tela local, padrão exigido pela auditoria) ----------

def creator_info(token, chamar=_req):
    return _checar(chamar(f"{API}/post/publish/creator_info/query/", json_corpo={}, token=token))


def enviar(token, video, post_info, chamar=_req):
    """Inicia o post com as escolhas feitas na tela e sobe o arquivo. Devolve o publish_id.
    Não escolhe nada sozinho: privacidade, interações e divulgação vêm de quem aprovou."""
    tamanho = video.stat().st_size
    chunk, total, faixas = pedacos(tamanho)
    ini = _checar(chamar(f"{API}/post/publish/video/init/", token=token, json_corpo={
        "post_info": post_info,
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho,
                        "chunk_size": chunk, "total_chunk_count": total}}))
    with video.open("rb") as f:
        for a, b in faixas:
            f.seek(a)
            pedaco = f.read(b - a + 1)
            chamar(ini["upload_url"], corpo=pedaco, metodo="PUT", timeout=600, cabecalhos={
                "Content-Type": "video/mp4", "Content-Length": str(len(pedaco)),
                "Content-Range": f"bytes {a}-{b}/{tamanho}"})
    return ini["publish_id"]


def status(token, publish_id, chamar=_req):
    return _checar(chamar(f"{API}/post/publish/status/fetch/", token=token, json_corpo={"publish_id": publish_id}))


def montar_post_info(escolhas, info):
    """Valida as escolhas da tela contra o creator_info, como pedem as regras do TikTok."""
    priv = escolhas.get("privacidade")
    if priv not in (info.get("privacy_level_options") or []):
        raise ValueError("Escolha a privacidade.")
    com = escolhas.get("comercial") or {}
    if com.get("ativo") and not (com.get("sua_marca") or com.get("conteudo_marca")):
        raise ValueError("Marque se é a sua marca ou conteúdo de marca de terceiros.")
    if com.get("conteudo_marca") and priv == "SELF_ONLY":
        raise ValueError("Conteúdo de marca não pode ser privado.")
    titulo = (escolhas.get("titulo") or "").strip()
    if not titulo:
        raise ValueError("A legenda está vazia.")
    return {"title": titulo[:LEGENDA_MAX], "privacy_level": priv,
            "disable_comment": info.get("comment_disabled") or not escolhas.get("comentar"),
            "disable_duet": info.get("duet_disabled") or not escolhas.get("duet"),
            "disable_stitch": info.get("stitch_disabled") or not escolhas.get("stitch"),
            "brand_content_toggle": bool(com.get("ativo") and com.get("conteudo_marca")),
            "brand_organic_toggle": bool(com.get("ativo") and com.get("sua_marca")),
            **({"is_aigc": True} if escolhas.get("ia") else {})}
