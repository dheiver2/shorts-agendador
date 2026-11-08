#!/usr/bin/env python3
"""Agente de Shorts: pega vídeos de uma pasta e agenda 2 posts por dia no YouTube.

Cada vídeo sobe como privado com `publishAt` no horário escolhido; o próprio
YouTube publica na hora certa, então o Mac não precisa estar ligado no horário.
Rodar várias vezes por dia é seguro: só preenche os horários que estão vagos.

Uso:
  agente.py auth        # login no Google (uma vez)
  agente.py run         # agenda os próximos horários vagos
  agente.py run --simular   # mostra o que faria, sem subir nada
  agente.py status      # fila e próximos agendamentos
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
ESTADO = BASE / "estado.json"
TOKEN = BASE / "token.json"
CLIENT_SECRET = BASE / "client_secret.json"
LOG = BASE / "agente.log"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MARGEM_MIN = 20  # publishAt precisa estar no futuro; ignora horários muito próximos


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


# ---------- horários ----------

DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def horarios_do_dia(cfg, dia):
    """Divide posts_por_semana pelos 7 dias; a sobra vai para dias_extra (fim de semana).
    Usa os N primeiros horários da lista (ordenada do melhor para o pior)."""
    total = cfg["posts_por_semana"]
    base, sobra = divmod(total, 7)
    n = base + (1 if DIAS[dia.weekday()] in cfg["dias_extra"][:sobra] else 0)
    return sorted(cfg["horarios"][:n])


def proximos_horarios(cfg, agora_utc):
    tz = ZoneInfo(cfg["fuso"])
    hoje = agora_utc.astimezone(tz).date()
    limite = agora_utc + timedelta(minutes=MARGEM_MIN)
    for d in range(cfg["dias_a_frente"] + 1):
        dia = hoje + timedelta(days=d)
        for hhmm in horarios_do_dia(cfg, dia):
            h, m = map(int, hhmm.split(":"))
            slot = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=tz).astimezone(timezone.utc)
            if slot > limite:
                yield slot


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- vídeos ----------

def fila(pasta):
    vids = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith(".")]
    return sorted(vids, key=lambda p: p.name.lower())


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
        import re
        titulo = re.sub(r"^\d+[\s._-]*", "", video.stem)  # tira numeração de ordem "001-"
        titulo = titulo.replace("_", " ").replace("-", " ").strip() or video.stem
        desc = cfg["descricao_padrao"]
    if "#shorts" not in titulo.lower() and len(titulo) + 8 <= 100:
        titulo += " #Shorts"
    if "#shorts" not in desc.lower():
        desc = (desc + "\n\n#Shorts").strip()
    return titulo[:100], desc[:5000], side if side.exists() else None


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


# ---------- comandos ----------

def cmd_run(cfg, simular=False):
    pasta = Path(cfg["pasta_videos"]).expanduser()
    postados = pasta / "postados"
    rejeitados = pasta / "rejeitados"
    if not pasta.is_dir():
        raise SystemExit(f"Pasta não existe: {pasta}")
    estado = carregar(ESTADO, {"agendados": []})
    ocupados = {a["publish_at"] for a in estado["agendados"]}
    vagos = [s for s in proximos_horarios(cfg, datetime.now(timezone.utc)) if iso(s) not in ocupados]
    videos = fila(pasta)
    if not vagos:
        log("Nenhum horário vago no período. Nada a fazer.")
        return
    if not videos:
        log(f"Fila vazia em {pasta}. {len(vagos)} horário(s) vago(s) sem vídeo.")
        return

    yt = None if simular else youtube()
    tz = ZoneInfo(cfg["fuso"])
    for slot in vagos:
        video = None
        while videos and video is None:
            cand = videos.pop(0)
            info, erro = sondar(cand)
            erro = erro or validar(info, cfg)
            if erro:
                log(f"PULADO {cand.name}: {erro}")
                if not simular:
                    rejeitados.mkdir(exist_ok=True)
                    shutil.move(str(cand), rejeitados / cand.name)
                continue
            video = cand
        if video is None:
            log("Acabaram os vídeos válidos na fila.")
            break
        titulo, desc, side = metadados(video, cfg)
        quando = slot.astimezone(tz).strftime("%d/%m %H:%M")
        if simular:
            log(f"[simulação] {video.name} → {quando} | {titulo}")
            continue
        try:
            vid = subir(yt, video, titulo, desc, iso(slot), cfg)
        except Exception as e:  # cota, rede, token
            msg = str(e)
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg:
                log(f"Cota/limite do YouTube atingido; continua na próxima rodada. ({video.name})")
            else:
                log(f"ERRO ao subir {video.name}: {msg[:300]}")
            break
        postados.mkdir(exist_ok=True)
        shutil.move(str(video), postados / video.name)
        if side:
            shutil.move(str(side), postados / side.name)
        estado["agendados"].append({"arquivo": video.name, "video_id": vid, "publish_at": iso(slot),
                                    "titulo": titulo, "enviado_em": iso(datetime.now(timezone.utc))})
        salvar_estado(estado)
        log(f"AGENDADO {video.name} → {quando} | https://youtube.com/shorts/{vid}")


def cmd_status(cfg):
    tz = ZoneInfo(cfg["fuso"])
    pasta = Path(cfg["pasta_videos"]).expanduser()
    estado = carregar(ESTADO, {"agendados": []})
    agora = iso(datetime.now(timezone.utc))
    futuros = [a for a in estado["agendados"] if a["publish_at"] > agora]
    print(f"Pasta: {pasta}")
    print(f"Na fila: {len(fila(pasta)) if pasta.is_dir() else 'pasta não existe'}")
    print(f"Regra: {cfg['posts_por_semana']} posts/semana; horários por prioridade: {', '.join(cfg['horarios'])} ({cfg['fuso']})")
    print(f"Já publicados/agendados no total: {len(estado['agendados'])}")
    print("Próximos agendamentos:" if futuros else "Nenhum agendamento futuro.")
    for a in sorted(futuros, key=lambda a: a["publish_at"]):
        dt = datetime.strptime(a["publish_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(tz)
        print(f"  {dt:%d/%m %H:%M}  {a['titulo']}  (youtu.be/{a['video_id']})")


def main():
    args = sys.argv[1:] or ["status"]
    cfg = carregar(CONFIG, None)
    if cfg is None:
        raise SystemExit("Falta config.json")
    if args[0] == "auth":
        credenciais(interativo=True)
        from googleapiclient.discovery import build
        yt = build("youtube", "v3", credentials=credenciais(), cache_discovery=False)
        canal = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        print(f"Login OK. Canal: {canal}")
    elif args[0] == "run":
        cmd_run(cfg, simular="--simular" in args)
    elif args[0] == "status":
        cmd_status(cfg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
