#!/usr/bin/env python3
"""Agente de Shorts: pega vídeos de uma pasta e agenda N posts por semana no YouTube.

Cada vídeo sobe como privado com `publishAt` no horário escolhido; o próprio
YouTube publica na hora certa, então o Mac não precisa estar ligado no horário.
Rodar várias vezes por dia é seguro: só preenche os horários que estão vagos.

Uso:
  agente.py auth            # login no Google (uma vez)
  agente.py run             # agenda os próximos horários vagos, confere e avisa
  agente.py run --simular   # mostra o que faria, sem subir nada
  agente.py status          # fila e próximos agendamentos
  agente.py teste-aviso     # manda uma mensagem de teste no Telegram
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
ESTADO = BASE / "estado.json"
TOKEN = BASE / "token.json"
CLIENT_SECRET = BASE / "client_secret.json"
ENV = BASE / ".env"
LOG = BASE / "agente.log"
LOCK = BASE / ".agente.lock"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MARGEM_MIN = 20  # publishAt precisa estar no futuro; ignora horários muito próximos
DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
FMT = "%Y-%m-%dT%H:%M:%SZ"


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha)
    with LOG.open("a") as f:
        f.write(linha + "\n")


def carregar(path, padrao):
    return json.loads(path.read_text()) if path.exists() else padrao


def salvar_estado(estado):
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=2, ensure_ascii=False))
    tmp.replace(ESTADO)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def de_iso(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


# ---------- avisos (Telegram) ----------

def carregar_env():
    env = {}
    if ENV.exists():
        for linha in ENV.read_text().splitlines():
            if "=" in linha and not linha.strip().startswith("#"):
                k, v = linha.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
    return env


def avisar(texto):
    """Manda no Telegram se TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID estiverem no .env.
    Sem configuração, só registra no log. Falha de envio nunca derruba a rodada."""
    env = carregar_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("(aviso não enviado: Telegram não configurado no .env)")
        return False
    dados = urllib.parse.urlencode({"chat_id": chat, "text": texto[:4000],
                                    "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", dados, timeout=20) as r:
            return json.load(r).get("ok", False)
    except Exception as e:
        log(f"Falha ao avisar no Telegram: {str(e)[:200]}")
        return False


# ---------- horários ----------

def hora_utc(cfg, dia, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=ZoneInfo(cfg["fuso"])).astimezone(timezone.utc)


def horarios_do_dia(cfg, dia):
    """Divide posts_por_semana pelos 7 dias; a sobra vai para dias_extra (fim de semana).
    Usa os N primeiros horários da lista (ordenada do melhor para o pior)."""
    base, sobra = divmod(cfg["posts_por_semana"], 7)
    n = base + (1 if DIAS[dia.weekday()] in cfg["dias_extra"][:sobra] else 0)
    return sorted(cfg["horarios"][:n])


def proximos_horarios(cfg, agora_utc):
    hoje = agora_utc.astimezone(ZoneInfo(cfg["fuso"])).date()
    limite = agora_utc + timedelta(minutes=MARGEM_MIN)
    for d in range(cfg["dias_a_frente"] + 1):
        dia = hoje + timedelta(days=d)
        for hhmm in horarios_do_dia(cfg, dia):
            slot = hora_utc(cfg, dia, hhmm)
            if slot > limite:
                yield slot


def semana_de(cfg, dt_utc):
    """Segunda-feira (data local) da semana do instante."""
    d = dt_utc.astimezone(ZoneInfo(cfg["fuso"])).date()
    return d - timedelta(days=d.weekday())


def garantir_inicio(estado, agora_utc):
    """Marca quando o agente começou (1º envio), para não contar horários de antes como perdidos."""
    if "inicio" not in estado:
        datas = [a["enviado_em"] for a in estado["agendados"] if a.get("enviado_em")]
        estado["inicio"] = min(datas) if datas else iso(agora_utc)


def horarios_perdidos(cfg, estado, agora_utc):
    """Horários regulares desta semana que já passaram sem vídeo, contados só depois
    que o agente começou a rodar (estado['inicio'])."""
    inicio = de_iso(estado["inicio"]) if estado.get("inicio") else agora_utc
    seg = semana_de(cfg, agora_utc)
    ocupados = {a["publish_at"] for a in estado["agendados"]}
    limite = agora_utc + timedelta(minutes=MARGEM_MIN)
    perdidos = []
    for i in range(7):
        dia = seg + timedelta(days=i)
        for hhmm in horarios_do_dia(cfg, dia):
            slot = hora_utc(cfg, dia, hhmm)
            if inicio < slot <= limite and iso(slot) not in ocupados:
                perdidos.append(slot)
    return perdidos


def horarios_reposicao(cfg, estado, agora_utc):
    """Horários extras para repor o que foi perdido nesta semana, sem passar da meta semanal.
    Usa as horas que o dia não usa (da lista `horarios` e depois `horarios_reserva`),
    respeitando max_por_dia (cota da API)."""
    seg = semana_de(cfg, agora_utc)
    ja_repostos = sum(1 for a in estado["agendados"]
                      if a.get("reposicao") and semana_de(cfg, de_iso(a["publish_at"])) == seg)
    faltam = len(horarios_perdidos(cfg, estado, agora_utc)) - ja_repostos
    if faltam <= 0:
        return []
    ocupados = {a["publish_at"] for a in estado["agendados"]}
    limite = agora_utc + timedelta(minutes=MARGEM_MIN)
    tz = ZoneInfo(cfg["fuso"])
    hoje = agora_utc.astimezone(tz).date()
    prioridade = cfg["horarios"] + cfg.get("horarios_reserva", [])
    candidatos = []
    for d in range(cfg["dias_a_frente"] + 1):
        dia = hoje + timedelta(days=d)
        if dia - timedelta(days=dia.weekday()) != seg:
            break  # reposição só dentro da mesma semana
        regulares = set(horarios_do_dia(cfg, dia))
        extras = [h for h in dict.fromkeys(prioridade) if h not in regulares]
        ja_extra = sum(1 for h in extras if iso(hora_utc(cfg, dia, h)) in ocupados)
        cabe = cfg["max_por_dia"] - len(regulares) - ja_extra
        do_dia = [(rank, dia, hora_utc(cfg, dia, h)) for rank, h in enumerate(extras)]
        do_dia = [c for c in do_dia if c[2] > limite and iso(c[2]) not in ocupados]
        candidatos += do_dia[:max(0, cabe)]
    candidatos.sort(key=lambda c: (c[0], c[2]))
    return sorted(c[2] for c in candidatos[:faltam])


# ---------- vídeos ----------

def fila(pasta):
    vids = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith(".")]
    return sorted(vids, key=lambda p: p.name.lower())


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def sondar(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None, "ffprobe falhou (arquivo corrompido?)"
    j = json.loads(out.stdout)
    st = (j.get("streams") or [{}])[0]
    w, h = st.get("width", 0), st.get("height", 0)
    rot = abs(int(float(st.get("tags", {}).get("rotate", 0) or 0)))
    for sd in st.get("side_data_list", []) or []:
        rot = abs(int(float(sd.get("rotation", rot) or rot)))
    if rot in (90, 270):
        w, h = h, w
    dur = float(j.get("format", {}).get("duration", 0) or 0)
    return {"largura": w, "altura": h, "duracao": dur}, None


def validar(info, cfg):
    if info["duracao"] > cfg["duracao_max_seg"]:
        return f"tem {info['duracao']:.0f}s (máx. {cfg['duracao_max_seg']}s para Shorts)"
    if info["largura"] > info["altura"]:
        return f"é horizontal ({info['largura']}x{info['altura']}); Shorts precisa ser vertical ou quadrado"
    return None


def metadados(video, cfg):
    """Título/descrição: usa video.txt ao lado (1ª linha = título, resto = descrição) se existir."""
    side = video.with_suffix(".txt")
    if side.exists():
        linhas = side.read_text().strip().splitlines()
        titulo = linhas[0].strip() if linhas else video.stem
        desc = "\n".join(linhas[1:]).strip() or cfg["descricao_padrao"]
    else:
        titulo = re.sub(r"^\d+[\s._-]*", "", video.stem)  # tira numeração de ordem "001-"
        titulo = titulo.replace("_", " ").replace("-", " ").strip() or video.stem
        desc = cfg["descricao_padrao"]
    if "#shorts" not in titulo.lower() and len(titulo) + 8 <= 100:
        titulo += " #Shorts"
    if "#shorts" not in desc.lower():
        desc = (desc + "\n\n#Shorts").strip()
    return titulo[:100], desc[:5000], side if side.exists() else None


def preencher_hashes(estado, postados):
    """Calcula o hash dos vídeos já enviados que ainda não têm (estado antigo)."""
    mudou = False
    for a in estado["agendados"]:
        if "sha256" not in a and (postados / a["arquivo"]).exists():
            a["sha256"] = sha256(postados / a["arquivo"])
            mudou = True
    return mudou


# ---------- YouTube ----------

def credenciais(interativo=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interativo:
            raise SystemExit("Sem login válido. Rode: ./agente.py auth")
        if not CLIENT_SECRET.exists():
            raise SystemExit(f"Falta {CLIENT_SECRET.name}. Veja o README (passo 1).")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return creds


def youtube():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credenciais(), cache_discovery=False)


def subir(yt, video, titulo, desc, publish_at, cfg):
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": titulo, "description": desc, "tags": cfg["tags"],
                    "categoryId": cfg["categoria_id"]},
        "status": {"privacyStatus": "private", "publishAt": publish_at,
                   "selfDeclaredMadeForKids": cfg["feito_para_criancas"],
                   "containsSyntheticMedia": cfg["conteudo_sintetico"]},
    }
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    return resp["id"]


def diagnosticar(item, publish_at, agora_utc):
    """Compara o que o YouTube tem com o que foi agendado. Devolve o problema ou None."""
    if item is None:
        return "sumiu do canal (apagado?)"
    st = item.get("status", {})
    if st.get("uploadStatus") in ("rejected", "failed"):
        motivo = st.get("rejectionReason") or st.get("failureReason") or ""
        return f"upload {st['uploadStatus']} {motivo}".strip()
    quando = de_iso(publish_at)
    if agora_utc < quando:
        if st.get("privacyStatus") != "private" or st.get("publishAt") != publish_at:
            return f"agendamento mudou (status={st.get('privacyStatus')}, publishAt={st.get('publishAt')})"
    elif agora_utc > quando + timedelta(minutes=30) and st.get("privacyStatus") != "public":
        return f"não ficou público no horário (status={st.get('privacyStatus')})"
    return None


def conferir(yt, cfg, estado, agora_utc):
    """Confere os agendamentos futuros e os dos últimos 3 dias que ainda não foram vistos públicos."""
    alvo = [a for a in estado["agendados"]
            if not a.get("publico_ok") and de_iso(a["publish_at"]) > agora_utc - timedelta(days=3)]
    problemas = []
    for i in range(0, len(alvo), 50):
        lote = alvo[i:i + 50]
        resp = yt.videos().list(part="status", id=",".join(a["video_id"] for a in lote)).execute()
        por_id = {it["id"]: it for it in resp.get("items", [])}
        for a in lote:
            item = por_id.get(a["video_id"])
            prob = diagnosticar(item, a["publish_at"], agora_utc)
            if item and not prob and item["status"].get("privacyStatus") == "public":
                a["publico_ok"] = True
            if prob and a.get("alerta") != prob:
                problemas.append(f"{a['titulo'][:60]} (youtu.be/{a['video_id']}): {prob}")
            a["alerta"] = prob
    return problemas


# ---------- comandos ----------

def dias_de_fila(cfg, n_videos):
    return n_videos / (cfg["posts_por_semana"] / 7)


def cmd_run(cfg, simular=False):
    pasta = Path(cfg["pasta_videos"]).expanduser()
    postados, rejeitados = pasta / "postados", pasta / "rejeitados"
    if not pasta.is_dir():
        raise SystemExit(f"Pasta não existe: {pasta}")
    agora = datetime.now(timezone.utc)
    tz = ZoneInfo(cfg["fuso"])
    estado = carregar(ESTADO, {"agendados": []})
    garantir_inicio(estado, agora)
    if preencher_hashes(estado, postados) and not simular:
        salvar_estado(estado)
    hashes = {a["sha256"] for a in estado["agendados"] if a.get("sha256")}

    ocupados = {a["publish_at"] for a in estado["agendados"]}
    regulares = [s for s in proximos_horarios(cfg, agora) if iso(s) not in ocupados]
    reposicao = horarios_reposicao(cfg, estado, agora)
    vagos = sorted([(s, False) for s in regulares] + [(s, True) for s in reposicao])

    agendados, pulados, erro_envio = [], [], None
    videos = fila(pasta)
    yt = None
    if vagos and videos:
        yt = None if simular else youtube()
    for slot, eh_repo in vagos:
        video = None
        while videos and video is None:
            cand = videos.pop(0)
            info, erro = sondar(cand)
            erro = erro or validar(info, cfg)
            h = None
            if not erro:
                h = sha256(cand)
                if h in hashes:
                    erro = "duplicado (o mesmo arquivo já foi enviado)"
            if erro:
                log(f"PULADO {cand.name}: {erro}")
                pulados.append(f"{cand.name}: {erro}")
                if not simular:
                    rejeitados.mkdir(exist_ok=True)
                    shutil.move(str(cand), rejeitados / cand.name)
                continue
            video = cand
        if video is None:
            break
        titulo, desc, side = metadados(video, cfg)
        quando = slot.astimezone(tz).strftime("%d/%m %H:%M") + (" (reposição)" if eh_repo else "")
        if simular:
            log(f"[simulação] {video.name} → {quando} | {titulo}")
            continue
        try:
            vid = subir(yt, video, titulo, desc, iso(slot), cfg)
        except Exception as e:  # cota, rede, token
            msg = str(e)
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg:
                erro_envio = f"Cota do YouTube atingida em {video.name}; continua na próxima rodada."
            else:
                erro_envio = f"Erro ao subir {video.name}: {msg[:300]}"
            log(erro_envio)
            break
        postados.mkdir(exist_ok=True)
        shutil.move(str(video), postados / video.name)
        if side:
            shutil.move(str(side), postados / side.name)
        reg = {"arquivo": video.name, "video_id": vid, "publish_at": iso(slot), "titulo": titulo,
               "enviado_em": iso(datetime.now(timezone.utc)), "sha256": h}
        if eh_repo:
            reg["reposicao"] = True
        estado["agendados"].append(reg)
        hashes.add(h)
        salvar_estado(estado)
        agendados.append(f"{quando} — {titulo}")
        log(f"AGENDADO {video.name} → {quando} | https://youtube.com/shorts/{vid}")

    sem_video = len(vagos) - len(agendados) if not erro_envio and not simular else 0
    if vagos and not fila(pasta) and sem_video:
        log(f"Fila vazia: {sem_video} horário(s) ficaram sem vídeo.")

    problemas = []
    if not simular:
        try:
            yt = yt or youtube()
            problemas = conferir(yt, cfg, estado, agora)
            salvar_estado(estado)
            for p in problemas:
                log(f"PROBLEMA {p}")
        except Exception as e:
            problemas = [f"Não consegui conferir os agendamentos: {str(e)[:200]}"]
            log(problemas[0])

    # aviso: só quando houve novidade, ou fila baixa (1x por dia)
    restante = len(fila(pasta))
    dias = dias_de_fila(cfg, restante)
    hoje = agora.astimezone(tz).date().isoformat()
    fila_baixa = dias < cfg["avisar_fila_abaixo_dias"] and estado.get("aviso_fila_em") != hoje
    if simular:
        return
    if agendados or pulados or erro_envio or problemas or fila_baixa or sem_video:
        partes = ["🎬 Agendador de Shorts"]
        if agendados:
            partes.append("Agendados:\n" + "\n".join(f"• {x}" for x in agendados))
        if pulados:
            partes.append("Recusados (foram para rejeitados/):\n" + "\n".join(f"• {x}" for x in pulados))
        if erro_envio:
            partes.append("⚠️ " + erro_envio)
        if problemas:
            partes.append("⚠️ Conferência no YouTube:\n" + "\n".join(f"• {x}" for x in problemas))
        if sem_video:
            partes.append(f"⚠️ {sem_video} horário(s) ficaram sem vídeo: a fila acabou.")
        partes.append(f"Fila: {restante} vídeo(s), dá para ~{dias:.1f} dia(s).")
        if fila_baixa:
            partes.append("⚠️ Fila acabando: coloque mais vídeos em " + str(pasta))
            estado["aviso_fila_em"] = hoje
            salvar_estado(estado)
        avisar("\n\n".join(partes))


def cmd_status(cfg):
    tz = ZoneInfo(cfg["fuso"])
    pasta = Path(cfg["pasta_videos"]).expanduser()
    estado = carregar(ESTADO, {"agendados": []})
    agora = datetime.now(timezone.utc)
    garantir_inicio(estado, agora)
    futuros = [a for a in estado["agendados"] if de_iso(a["publish_at"]) > agora]
    n = len(fila(pasta)) if pasta.is_dir() else 0
    seg = semana_de(cfg, agora)
    na_semana = sum(1 for a in estado["agendados"] if semana_de(cfg, de_iso(a["publish_at"])) == seg)
    print(f"Pasta: {pasta}")
    print(f"Na fila: {n} vídeo(s) (~{dias_de_fila(cfg, n):.1f} dias)")
    print(f"Regra: {cfg['posts_por_semana']} posts/semana; horários por prioridade: {', '.join(cfg['horarios'])} ({cfg['fuso']})")
    print(f"Nesta semana (desde {seg:%d/%m}): {na_semana} agendado(s)/publicado(s)")
    repostos = sum(1 for a in estado["agendados"]
                   if a.get("reposicao") and semana_de(cfg, de_iso(a["publish_at"])) == seg)
    print(f"Horários perdidos nesta semana: {len(horarios_perdidos(cfg, estado, agora))} (repostos: {repostos})")
    print(f"Total enviado: {len(estado['agendados'])}")
    print("Próximos agendamentos:" if futuros else "Nenhum agendamento futuro.")
    for a in sorted(futuros, key=lambda a: a["publish_at"]):
        dt = de_iso(a["publish_at"]).astimezone(tz)
        extra = " (reposição)" if a.get("reposicao") else ""
        alerta = f"  ⚠️ {a['alerta']}" if a.get("alerta") else ""
        print(f"  {DIAS[dt.weekday()]} {dt:%d/%m %H:%M}{extra}  {a['titulo']}  (youtu.be/{a['video_id']}){alerta}")


def main():
    args = sys.argv[1:] or ["status"]
    cfg = carregar(CONFIG, None)
    if cfg is None:
        raise SystemExit("Falta config.json")
    cfg.setdefault("horarios_reserva", [])
    cfg.setdefault("max_por_dia", 6)
    cfg.setdefault("avisar_fila_abaixo_dias", 3)
    if args[0] == "auth":
        credenciais(interativo=True)
        canal = youtube().channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        print(f"Login OK. Canal: {canal}")
    elif args[0] == "run":
        with LOCK.open("w") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log("Outra rodada já está em andamento; saindo.")
                return
            cmd_run(cfg, simular="--simular" in args)
    elif args[0] == "status":
        cmd_status(cfg)
    elif args[0] == "teste-aviso":
        print("Enviado." if avisar("✅ Teste do agendador de Shorts.") else "Não enviado (veja o .env).")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
