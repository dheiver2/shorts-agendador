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
  agente.py avaliar         # passa a fila inteira pelo Brier e mostra o ranking
  agente.py teste-aviso     # manda uma mensagem de teste no Telegram
  agente.py painel          # atualiza as métricas e abre o painel de desempenho
  agente.py espelhos        # repete no Instagram (Reel de teste) e no TikTok o que já saiu no YouTube (de hora em hora)
  agente.py tiktok-auth     # login no TikTok (uma vez)
  agente.py tiktok-app      # tela local para aprovar cada post no TikTok (http://127.0.0.1:8770)
  agente.py logins          # confere os logins de YouTube, Instagram e TikTok (também roda 1x por dia)
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import brier
import instagram
import tiktok
import painel

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
ESTADO = BASE / "estado.json"
TOKEN = BASE / "token.json"
CLIENT_SECRET = BASE / "client_secret.json"
ENV = BASE / ".env"
LOG = BASE / "agente.log"
LOCK = BASE / ".agente.lock"
TIKTOK_TOKEN = BASE / "tiktok_token.json"
PAINEL = BASE / "painel" / "index.html"
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
    servico = env.get("TELEGRAM_KEYCHAIN_SERVICE")
    if not env.get("TELEGRAM_BOT_TOKEN") and servico:
        # token guardado no Keychain do macOS (não fica em arquivo)
        r = subprocess.run(["security", "find-generic-password", "-w", "-s", servico,
                            "-a", env.get("TELEGRAM_KEYCHAIN_ACCOUNT", "TELEGRAM_BOT_TOKEN")],
                           capture_output=True, text=True)
        if r.returncode == 0:
            env["TELEGRAM_BOT_TOKEN"] = r.stdout.strip()
    servico_ig = env.get("INSTAGRAM_KEYCHAIN_SERVICE")
    if not env.get("INSTAGRAM_TOKEN") and servico_ig:
        r = subprocess.run(["security", "find-generic-password", "-w", "-s", servico_ig,
                            "-a", env.get("INSTAGRAM_KEYCHAIN_ACCOUNT", "INSTAGRAM_TOKEN")],
                           capture_output=True, text=True)
        if r.returncode == 0:
            env["INSTAGRAM_TOKEN"] = r.stdout.strip()
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

def fim_da_cota(agora_utc):
    """A cota diária da API zera à meia-noite do horário do Pacífico."""
    pt = ZoneInfo("America/Los_Angeles")
    amanha = agora_utc.astimezone(pt).date() + timedelta(days=1)
    return datetime(amanha.year, amanha.month, amanha.day, tzinfo=pt).astimezone(timezone.utc)


def ordem_de_envio(plano, prioridade, tz):
    """Dia a dia, os melhores horários sobem primeiro: se a cota acabar no meio,
    os horários que ficam sem vídeo são os piores do dia."""
    def chave(p):
        local = p[0][0].astimezone(tz)
        hhmm = local.strftime("%H:%M")
        return (local.date(), prioridade.index(hhmm) if hhmm in prioridade else 99, p[0][0])
    return sorted(plano, key=chave)


def fila(pasta):
    vids = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith(".")]
    return sorted(vids, key=ordem_natural)


def ordem_natural(p):
    """'27 - x' antes de '031 - y': compara a numeração inicial como número, não como texto."""
    m = re.match(r"\d+", p.name)
    return (0, int(m.group()), p.name.lower()) if m else (1, 0, p.name.lower())


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
    titulo = titulo.replace("<", "").replace(">", "").strip() or video.stem
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


def conectar():
    """youtube() para a rodada automática: devolve (cliente, erro) em vez de derrubar o processo.
    O login vencido sai como SystemExit (sem token) ou RefreshError (token revogado ou expirado)."""
    try:
        return youtube(), None
    except (Exception, SystemExit) as e:
        texto = str(e)
        if "invalid_grant" in texto or "Sem login" in texto or "expired" in texto.lower():
            return None, "O login do YouTube expirou. Rode no Mac: .venv/bin/python agente.py auth"
        return None, f"Sem acesso ao YouTube: {texto[:200]}"


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


def coletar_metricas(yt, estado, agora_utc):
    """Views, likes e comentários dos vídeos já publicados (1 unidade de cota a cada 50 vídeos).
    Guarda o último valor em cada agendado e um ponto na série do total."""
    publicados = [a for a in estado["agendados"] if de_iso(a["publish_at"]) <= agora_utc]
    for i in range(0, len(publicados), 50):
        lote = publicados[i:i + 50]
        resp = yt.videos().list(part="statistics", id=",".join(a["video_id"] for a in lote)).execute()
        por_id = {it["id"]: it.get("statistics", {}) for it in resp.get("items", [])}
        for a in lote:
            st = por_id.get(a["video_id"])
            if st is not None:
                a["metricas"] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                                 "comentarios": int(st.get("commentCount", 0)), "em": iso(agora_utc)}
    com = [a["metricas"] for a in publicados if a.get("metricas")]
    serie = estado.setdefault("serie", [])
    if serie and agora_utc - de_iso(serie[-1]["em"]) < timedelta(minutes=10):
        serie.pop()  # coletas seguidas (rodada + painel manual) viram um ponto só
    serie.append({"em": iso(agora_utc), "views": sum(m["views"] for m in com),
                  "likes": sum(m["likes"] for m in com), "publicados": len(publicados)})
    del serie[:-2000]


def atualizar_painel(cfg, estado, yt=None):
    """Coleta métricas (se houver conexão) e regrava painel/index.html."""
    agora = datetime.now(timezone.utc)
    if yt is not None:
        try:
            coletar_metricas(yt, estado, agora)
            salvar_estado(estado)
        except Exception as e:
            log(f"Não consegui coletar métricas: {str(e)[:200]}")
    pasta = Path(cfg["pasta_videos"]).expanduser()
    n = len(fila(pasta)) if pasta.is_dir() else 0
    destino = painel.gerar(cfg, estado, n, dias_de_fila(cfg, n), agora, PAINEL)
    repo = cfg.get("painel_publico_repo")
    if repo and yt is not None:
        try:
            url = painel.publicar(cfg, estado, n, dias_de_fila(cfg, n), agora, BASE / ".painel-publico", repo)
            log(f"Painel publicado: {url}")
        except Exception as e:
            log(f"Não consegui publicar o painel: {str(getattr(e, 'stderr', '') or e)[:200]}")
    return destino


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

    agendados, pulados, revisar, outros, erro_envio = [], [], [], [], None
    cota_ate = estado.get("cota_esgotada_ate")
    cota_bloqueada = bool(cota_ate) and agora < de_iso(cota_ate)
    if cota_bloqueada:
        log(f"Cota do YouTube esgotada até {de_iso(cota_ate).astimezone(tz):%d/%m %H:%M}; "
            f"sem envios nesta rodada.")
        vagos = []
    videos = fila(pasta)
    yt, erro_conexao = None, None
    if vagos and videos and not simular:
        yt, erro_conexao = conectar()
        if erro_conexao:
            log(erro_conexao)
            vagos = []
    usar_brier = cfg["brier"]["ativo"] and bool(carregar_env().get("BRIER_KEY"))
    prioridade = list(dict.fromkeys(cfg["horarios"] + cfg["horarios_reserva"]))
    cache = estado.setdefault("brier_cache", {})
    brier_falhou = False

    def proximo_valido():
        """Próximo vídeo da fila que passa no formato, no anti-duplicado e no Brier."""
        nonlocal brier_falhou
        while videos:
            cand = videos.pop(0)
            info, erro = sondar(cand)
            erro = erro or validar(info, cfg)
            h = None
            if not erro:
                h = sha256(cand)
                if h in hashes:
                    erro = "duplicado (o mesmo arquivo já foi enviado)"
            destino = rejeitados
            titulo, desc, side = metadados(cand, cfg) if not erro else (None, None, None)
            av = None
            if not erro and usar_brier and not brier_falhou:
                k = brier.chave_cache(h, titulo, desc)
                av = cache.get(k)
                if av is None:
                    try:
                        t0 = time.time()
                        resp = brier.consultar(cfg["brier"], carregar_env()["BRIER_KEY"],
                                               brier.estado_texto(titulo, desc, cfg["canal"]))
                        av = brier.interpretar(resp, cfg["brier"])
                        cache[k] = av
                        log(f"BRIER {cand.name}: gancho {av['gancho']:.2f}, enganoso {av['enganoso']:.0%}, "
                            f"risco {av['risco']:.0%}, público {av['publico']}, #{' #'.join(av['hashtags'])} "
                            f"({time.time() - t0:.1f}s)")
                    except Exception as e:
                        brier_falhou = True  # segue sem Brier nesta rodada
                        log(f"Brier indisponível, seguindo sem ele: {e}")
                if av:
                    motivo = brier.segurar(av, cfg["brier"])
                    if motivo:
                        erro, destino = motivo + " — revise o título/descrição e devolva à pasta", pasta / "revisar"
            if erro:
                log(f"PULADO {cand.name}: {erro}")
                (revisar if destino.name == "revisar" else pulados).append(f"{cand.name}: {erro}")
                if not simular:
                    destino.mkdir(exist_ok=True)
                    shutil.move(str(cand), destino / cand.name)
                    side_txt = cand.with_suffix(".txt")
                    if destino.name == "revisar" and side_txt.exists():
                        shutil.move(str(side_txt), destino / side_txt.name)
                continue
            if av and av["hashtags"]:
                desc = brier.descricao_com_hashtags(desc, av["hashtags"])
            return {"video": cand, "sha": h, "titulo": titulo, "desc": desc, "side": side, "brier": av}
        return None

    # agrupa os horários vagos por dia; o vídeo de gancho mais forte do dia pega o melhor horário
    por_dia = {}
    for slot, eh_repo in vagos:
        por_dia.setdefault(slot.astimezone(tz).date(), []).append((slot, eh_repo))
    plano = []
    for dia in sorted(por_dia):
        slots = por_dia[dia]
        lote = [v for v in (proximo_valido() for _ in slots) if v]
        ranks = [(prioridade.index(s.astimezone(tz).strftime("%H:%M"))
                  if s.astimezone(tz).strftime("%H:%M") in prioridade else 99, (s, r)) for s, r in slots]
        if any(v["brier"] for v in lote):
            pares = brier.distribuir([(v, (v["brier"] or {}).get("gancho", 0.5)) for v in lote], ranks)
        else:  # sem Brier: ordem da fila nos horários em ordem cronológica
            pares = list(zip(sorted(slots), lote))
        plano += sorted(pares, key=lambda p: p[0][0])
        if len(lote) < len(slots):
            break  # a fila acabou

    salvar_estado(estado) if not simular else None
    for (slot, eh_repo), v in ordem_de_envio(plano, prioridade, tz):
        video, titulo, desc, side, av = v["video"], v["titulo"], v["desc"], v["side"], v["brier"]
        quando = slot.astimezone(tz).strftime("%d/%m %H:%M") + (" (reposição)" if eh_repo else "")
        nota = f" [gancho {av['gancho']:.2f}]" if av else ""
        if simular:
            log(f"[simulação] {video.name} → {quando}{nota} | {titulo}")
            continue
        try:
            vid = subir(yt, video, titulo, desc, iso(slot), cfg)
        except Exception as e:  # cota, rede, token
            msg = str(e)
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg:
                fim = fim_da_cota(agora)
                estado["cota_esgotada_ate"] = iso(fim)
                salvar_estado(estado)
                cota_bloqueada = True
                faltaram = len(plano) - len(agendados)
                erro_envio = (f"Cota diária do YouTube esgotada depois de {len(agendados)} envio(s) nesta rodada; "
                              f"{faltaram} horário(s) ficam sem vídeo. Volta a enviar às "
                              f"{fim.astimezone(tz):%H:%M} de {fim.astimezone(tz):%d/%m}.")
            else:
                erro_envio = f"Erro ao subir {video.name}: {msg[:300]}"
            log(erro_envio)
            break
        postados.mkdir(exist_ok=True)
        shutil.move(str(video), postados / video.name)
        if side:
            shutil.move(str(side), postados / side.name)
        reg = {"arquivo": video.name, "video_id": vid, "publish_at": iso(slot), "titulo": titulo,
               "enviado_em": iso(datetime.now(timezone.utc)), "sha256": v["sha"]}
        if eh_repo:
            reg["reposicao"] = True
        if av:
            reg["brier"] = av
        estado["agendados"].append(reg)
        hashes.add(v["sha"])
        salvar_estado(estado)
        agendados.append(f"{quando}{nota} — {titulo}")
        log(f"AGENDADO {video.name} → {quando}{nota} | https://youtube.com/shorts/{vid}")

    if brier_falhou:
        outros.append("Brier indisponível nesta rodada: vídeos seguiram sem ranking nem hashtags")
    sem_video = len(vagos) - len(agendados) if not erro_envio and not cota_bloqueada and not simular else 0
    if vagos and not fila(pasta) and sem_video:
        log(f"Fila vazia: {sem_video} horário(s) ficaram sem vídeo.")

    problemas = []
    if not simular:
        try:
            if yt is None and not erro_conexao:
                yt, erro_conexao = conectar()
            if erro_conexao:
                raise RuntimeError(erro_conexao)
            problemas = conferir(yt, cfg, estado, agora)
            salvar_estado(estado)
            for p in problemas:
                log(f"PROBLEMA {p}")
        except Exception as e:
            if not erro_conexao:
                problemas = [f"Não consegui conferir os agendamentos: {str(e)[:200]}"]
                log(problemas[0])

    if not simular and usar_brier and not brier_falhou:
        estado = carregar(ESTADO, estado)  # cmd_avaliar grava o cache por conta própria
        for a in cmd_avaliar(cfg, limite=cfg["brier"].get("avaliar_por_rodada", 6)):
            outros.append("Vai precisar de revisão quando chegar a vez: " + a)
        estado = carregar(ESTADO, estado)

    if not simular:
        estado = carregar(ESTADO, estado)
        atualizar_painel(cfg, estado, yt)

    # aviso: só quando houve novidade, ou fila baixa (1x por dia)
    restante = len(fila(pasta))
    dias = dias_de_fila(cfg, restante)
    hoje = agora.astimezone(tz).date().isoformat()
    fila_baixa = dias < cfg["avisar_fila_abaixo_dias"] and estado.get("aviso_fila_em") != hoje
    if simular:
        return
    login_ja_avisado = erro_conexao and estado.get("aviso_login_em") == hoje
    if erro_conexao and not login_ja_avisado:
        estado["aviso_login_em"] = hoje
        salvar_estado(estado)
    if agendados or pulados or revisar or outros or erro_envio or problemas or fila_baixa or sem_video \
            or (erro_conexao and not login_ja_avisado):
        partes = ["🎬 Agendador de Shorts"]
        if agendados:
            partes.append("Agendados:\n" + "\n".join(f"• {x}" for x in agendados))
        if pulados:
            partes.append("Recusados (foram para rejeitados/):\n" + "\n".join(f"• {x}" for x in pulados))
        if revisar:
            partes.append("Segurados pelo Brier (foram para revisar/):\n" + "\n".join(f"• {x}" for x in revisar))
        if outros:
            partes.append("\n".join(f"• {x}" for x in outros))
        if erro_conexao and not login_ja_avisado:
            partes.append("🔑 " + erro_conexao)
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


def espelhar(cfg, chave, nome, cab, conf, publicar_um):
    """Repete na rede `chave` cada vídeo cujo horário no YouTube já chegou (mesma grade:
    14 por dia, 07–20h). As APIs do Instagram e do TikTok não agendam, então isto roda
    de hora em hora. `publicar_um(video, legenda)` devolve (registro, observação)."""
    tz = ZoneInfo(cfg["fuso"])
    postados = Path(cfg["pasta_videos"]).expanduser() / "postados"
    agora = datetime.now(timezone.utc)
    estado = carregar(ESTADO, {"agendados": []})
    # começa a contar da primeira rodada com login: o que saiu antes no YouTube não é repetido
    marca = f"{chave}_desde"
    if not conf.get("desde") and marca not in estado:
        estado[marca] = iso(agora - timedelta(hours=conf["max_atraso_h"]))
        salvar_estado(estado)
    desde = conf.get("desde") or estado[marca]
    a_publicar, perdidos = instagram.pendentes(estado, agora, de_iso(desde), conf["max_atraso_h"], de_iso, chave)
    feitos, falhas, avisos_perdidos = [], [], []
    for a in perdidos:
        a[chave] = {"perdido": True}
        avisos_perdidos.append(f"{de_iso(a['publish_at']).astimezone(tz):%d/%m %H:%M} — {a['titulo']}")
        log(f"{nome.upper()} perdido (passou de {conf['max_atraso_h']}h): {a['arquivo']}")
    if perdidos:
        salvar_estado(estado)
    for a in a_publicar:
        video = postados / a["arquivo"]
        if not video.exists():
            a[chave] = {"perdido": True, "motivo": "arquivo não está em postados/"}
            salvar_estado(estado)
            falhas.append(f"{a['arquivo']}: arquivo não está mais em postados/")
            continue
        _, desc, _ = metadados(video, cfg)
        if a.get("brier", {}).get("hashtags"):
            desc = brier.descricao_com_hashtags(desc, a["brier"]["hashtags"])
        try:
            reg, obs = publicar_um(video, instagram.legenda(a["titulo"], desc))
        except Exception as e:
            falhas.append(f"{a['arquivo']}: {str(e)[:300]}")
            log(f"{nome.upper()} erro {a['arquivo']}: {e}")
            break  # login ou limite: tenta de novo na próxima hora
        a[chave] = {**reg, "publicado_em": iso(datetime.now(timezone.utc))}
        salvar_estado(estado)
        quando = de_iso(a["publish_at"]).astimezone(tz).strftime("%d/%m %H:%M")
        feitos.append(f"{quando} — {a['titulo']}" + (f" ({obs})" if obs else ""))
        log(f"{nome.upper()} publicado {a['arquivo']} → {reg}")
    if feitos or falhas or avisos_perdidos:
        partes = [cab]
        if feitos:
            partes.append("Publicados:\n" + "\n".join(f"• {x}" for x in feitos))
        if avisos_perdidos:
            partes.append("Não saíram (o Mac não rodou a tempo):\n" + "\n".join(f"• {x}" for x in avisos_perdidos))
        if falhas:
            partes.append("⚠️ Erro:\n" + "\n".join(f"• {x}" for x in falhas))
        avisar("\n\n".join(partes))


def cmd_instagram(cfg):
    """Reel de teste (fora da grade do perfil) na hora em que o vídeo sai no YouTube."""
    ig = cfg["instagram"]
    env = carregar_env()
    token, ig_user = env.get("INSTAGRAM_TOKEN"), env.get("INSTAGRAM_USER_ID")
    if not ig["ativo"] or not token or not ig_user:
        log("Instagram: desligado ou sem INSTAGRAM_TOKEN/INSTAGRAM_USER_ID no .env; nada a fazer.")
        return

    def um(video, texto):
        post = instagram.publicar_reel(token, ig_user, video, texto, teste=ig["teste"])
        if ig["teste"] and instagram.na_grade(token, post):
            # trava de segurança: o combinado é nunca aparecer na grade principal
            cfg_disco = carregar(CONFIG, {})
            cfg_disco["instagram"] = {**cfg_disco.get("instagram", {}), "ativo": False}
            CONFIG.write_text(json.dumps(cfg_disco, indent=2, ensure_ascii=False))
            raise RuntimeError(f"o Reel {post} entrou na grade do perfil. Instagram pausado; "
                               "tire o Reel da grade pelo app (Gerenciar post → Remover da grade do perfil)")
        return {"id": post, "teste": ig["teste"]}, None

    modo = "Reels de teste, fora da grade" if ig["teste"] else "Reels"
    espelhar(cfg, "instagram", "Instagram", f"📸 Instagram ({modo})", ig, um)


def tiktok_credenciais():
    env = carregar_env()
    chave = env.get("TIKTOK_CLIENT_KEY")
    segredo = env.get("TIKTOK_CLIENT_SECRET")
    if not segredo and env.get("TIKTOK_KEYCHAIN_SERVICE"):
        r = subprocess.run(["security", "find-generic-password", "-w", "-s", env["TIKTOK_KEYCHAIN_SERVICE"],
                            "-a", "TIKTOK_CLIENT_SECRET"], capture_output=True, text=True)
        segredo = r.stdout.strip() if r.returncode == 0 else None
    return chave, segredo


def cmd_tiktok(cfg):
    """Post no TikTok na hora em que o vídeo sai no YouTube. Sem login, não faz nada."""
    tk = cfg["tiktok"]
    chave, segredo = tiktok_credenciais()
    tok = carregar(TIKTOK_TOKEN, None)
    if not tk["ativo"] or not chave or not segredo or not tok:
        log("TikTok: desligado, sem TIKTOK_CLIENT_KEY/segredo ou sem login (agente.py tiktok-auth); nada a fazer.")
        return
    try:
        tok, mudou = tiktok.token_valido(tok, chave, segredo)
    except Exception as e:
        hoje = datetime.now(ZoneInfo(cfg["fuso"])).date().isoformat()
        estado = carregar(ESTADO, {"agendados": []})
        if estado.get("aviso_tiktok_login_em") != hoje:
            estado["aviso_tiktok_login_em"] = hoje
            salvar_estado(estado)
            avisar(f"🎵 TikTok: {e}")
        log(f"TikTok: {e}")
        return
    if mudou:
        TIKTOK_TOKEN.write_text(json.dumps(tok))
        TIKTOK_TOKEN.chmod(0o600)

    if tk["modo"] == "aprovacao":
        # o TikTok exige que cada post seja aprovado numa tela (prévia, privacidade escolhida na mão,
        # consentimento). Aqui só avisamos que há vídeo esperando; o envio sai pela tela local.
        import tiktok_app
        estado = carregar(ESTADO, {"agendados": []})
        if "tiktok_desde" not in estado:
            estado["tiktok_desde"] = iso(datetime.now(timezone.utc))
        novos = [p for p in tiktok_app.pendentes(cfg, estado, datetime.now(timezone.utc))
                 if p["arquivo"] not in estado.setdefault("tiktok_avisados", [])]
        if novos:
            estado["tiktok_avisados"] = (estado["tiktok_avisados"] + [p["arquivo"] for p in novos])[-500:]
            salvar_estado(estado)
            avisar("🎵 TikTok: vídeo pronto para aprovar\n" + "\n".join(f"• {p['legenda'].splitlines()[0]}" for p in novos)
                   + f"\n\nAbra no Mac: http://127.0.0.1:{tiktok_app.PORTA}")
        else:
            salvar_estado(estado)
        return

    def um(video, texto):
        r = tiktok.publicar(tok["access_token"], video, texto, privacidade=tk["privacidade"],
                            ia=cfg.get("conteudo_sintetico", False))
        obs = "privado: o app ainda não passou na auditoria do TikTok" if r["privacidade"] != tk["privacidade"] else None
        return r, obs

    espelhar(cfg, "tiktok", "TikTok", "🎵 TikTok", tk, um)


def cmd_tiktok_auth(cfg):
    chave, segredo = tiktok_credenciais()
    if not chave or not segredo:
        raise SystemExit("Falta TIKTOK_CLIENT_KEY no .env e o segredo (TIKTOK_CLIENT_SECRET ou Keychain).")
    redirect = cfg["tiktok"]["redirect_uri"]
    url = tiktok.url_login(chave, redirect)
    print("Abrindo o login do TikTok. Se não abrir, use:\n" + url)
    subprocess.run(["open", url])
    codigo = input("Cole aqui o código que aparece na página depois de autorizar: ")
    tok = tiktok.trocar_codigo(chave, segredo, urllib.parse.unquote(codigo), redirect)
    TIKTOK_TOKEN.write_text(json.dumps(tok))
    TIKTOK_TOKEN.chmod(0o600)
    print("Login do TikTok OK.")


def checar_logins(cfg):
    """Testa os três logins sem publicar nada. Devolve (ok, problemas) em texto.
    YouTube renova o acesso pelo refresh token, o token da Página do Instagram não expira
    e o do TikTok é renovado aqui mesmo (o refresh vale 1 ano e é trocado a cada renovação)."""
    ok, problemas = [], []
    yt, erro = conectar()
    if erro:
        problemas.append("YouTube: " + erro)
    else:
        try:
            canal = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
            ok.append(f"YouTube: {canal}")
        except Exception as e:
            problemas.append(f"YouTube: {str(e)[:200]}")
    env = carregar_env()
    if cfg["instagram"]["ativo"] and env.get("INSTAGRAM_TOKEN") and env.get("INSTAGRAM_USER_ID"):
        try:
            nome, vence = instagram.checar_login(env["INSTAGRAM_TOKEN"], env["INSTAGRAM_USER_ID"])
            dias = (vence - time.time()) / 86400 if vence else None
            if dias is not None and dias < 14:
                quando = datetime.fromtimestamp(vence, ZoneInfo(cfg["fuso"])).strftime("%d/%m")
                problemas.append(f"Instagram: o acesso da Meta vence em {quando}. Renove: no Explorador da Graph API "
                                 "gere um token novo (app Mangaba Agent), estenda e troque o token da Página.")
            else:
                ok.append(f"Instagram: @{nome}" + (f" (renovar até {datetime.fromtimestamp(vence):%d/%m/%Y})" if vence else ""))
        except Exception as e:
            problemas.append("Instagram: o login caiu (senha trocada ou acesso do app removido). "
                             f"Gere um token novo no Explorador da Graph API. Detalhe: {str(e)[:150]}")
    chave, segredo = tiktok_credenciais()
    tok = carregar(TIKTOK_TOKEN, None)
    if cfg["tiktok"]["ativo"] and chave and segredo and tok:
        try:
            # renova já, mesmo longe de vencer: mantém o refresh de 1 ano sempre novo
            tok, _ = tiktok.token_valido({**tok, "expira_em": 0}, chave, segredo)
            TIKTOK_TOKEN.write_text(json.dumps(tok))
            TIKTOK_TOKEN.chmod(0o600)
            ok.append("TikTok: login renovado")
        except Exception as e:
            problemas.append(f"TikTok: {str(e)[:200]}")
    return ok, problemas


def cmd_logins(cfg, so_uma_vez_por_dia=False):
    """Checagem diária dos logins (roda junto com a primeira rodada de espelhos do dia)."""
    hoje = datetime.now(ZoneInfo(cfg["fuso"])).date().isoformat()
    estado = carregar(ESTADO, {"agendados": []})
    if so_uma_vez_por_dia and estado.get("logins_checados_em") == hoje:
        return
    ok, problemas = checar_logins(cfg)
    estado = carregar(ESTADO, estado)
    estado["logins_checados_em"] = hoje
    salvar_estado(estado)
    for x in ok:
        log("LOGIN ok — " + x)
    for x in problemas:
        log("LOGIN PROBLEMA — " + x)
    if not so_uma_vez_por_dia:
        print("\n".join(["✅ " + x for x in ok] + ["⚠️ " + x for x in problemas]))
    if problemas:
        avisar("🔑 Login das redes:\n" + "\n".join(f"• {x}" for x in problemas))


def cmd_espelhos(cfg):
    for fn in (lambda c: cmd_logins(c, so_uma_vez_por_dia=True), cmd_instagram, cmd_tiktok):
        try:
            fn(cfg)
        except Exception as e:  # uma rede com problema não segura a outra
            log(f"Erro em {getattr(fn, '__name__', 'checagem')}: {e}")


def cmd_avaliar(cfg, limite=None):
    """Passa a fila inteira (ou até `limite` vídeos ainda sem nota) pelo Brier e guarda no cache.
    Assim a rodada de envio já encontra tudo avaliado e os vídeos arriscados são avisados cedo."""
    pasta = Path(cfg["pasta_videos"]).expanduser()
    env = carregar_env()
    if not env.get("BRIER_KEY"):
        print("Sem BRIER_KEY no .env.")
        return []
    estado = carregar(ESTADO, {"agendados": []})
    cache = estado.setdefault("brier_cache", {})
    linhas, novos, alertas = [], 0, []
    for v in fila(pasta):
        titulo, desc, _ = metadados(v, cfg)
        k = brier.chave_cache(sha256(v), titulo, desc)
        av = cache.get(k)
        if av is None:
            if limite is not None and novos >= limite:
                continue
            try:
                av = brier.interpretar(brier.consultar(cfg["brier"], env["BRIER_KEY"],
                                                       brier.estado_texto(titulo, desc, cfg["canal"])), cfg["brier"])
            except Exception as e:
                log(f"Brier indisponível: {e}")
                break
            cache[k] = av
            novos += 1
            salvar_estado(estado)
            motivo = brier.segurar(av, cfg["brier"])
            if motivo:
                alertas.append(f"{v.name}: {motivo}")
        linhas.append((av["gancho"], v.name, av))
    if limite is None:
        print(f"{'gancho':>6}  {'engan.':>6}  {'risco':>5}  {'público':<9} vídeo  →  hashtags")
        for g, nome, av in sorted(linhas, key=lambda x: -x[0]):
            flag = " ⚠️" if brier.segurar(av, cfg["brier"]) else ""
            print(f"{g:6.2f}  {av['enganoso']:6.0%}  {av['risco']:5.0%}  {str(av['publico']):<9} {nome[:60]}{flag}  →  "
                  + " ".join("#" + t for t in av["hashtags"]))
        print(f"{novos} avaliado(s) agora, {len(linhas) - novos} do cache.")
    return alertas


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
    if estado.get("cota_esgotada_ate") and agora < de_iso(estado["cota_esgotada_ate"]):
        print(f"Cota do YouTube esgotada até {de_iso(estado['cota_esgotada_ate']).astimezone(tz):%d/%m %H:%M}")
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
    cfg.setdefault("canal", "Dheiver Santos")
    cfg["brier"] = {"ativo": True, "url": "https://mangabarouter.store/v1", "modelo": "brier-v5",
                    "corte_risco": 0.8, "corte_enganoso": 0.85, "corte_hashtag": 0.6, "max_hashtags": 4,
                    **cfg.get("brier", {})}
    cfg["instagram"] = {"ativo": True, "teste": True, "max_atraso_h": 2,
                        **cfg.get("instagram", {})}
    cfg["tiktok"] = {"ativo": True, "modo": "aprovacao", "privacidade": "PUBLIC_TO_EVERYONE", "max_atraso_h": 2,
                     "redirect_uri": "https://dheiver2.github.io/shorts-agendador/tiktok/",
                     **cfg.get("tiktok", {})}
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
    elif args[0] == "avaliar":
        with LOCK.open("w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cmd_avaliar(cfg)
    elif args[0] == "painel":
        with LOCK.open("w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # espera a rodada em andamento, senão ela regrava o estado antigo
            estado = carregar(ESTADO, {"agendados": []})
            destino = atualizar_painel(cfg, estado, None if "--sem-rede" in args else youtube())  # manual: erro aparece na tela
        print(f"Painel: {destino}")
        if "--nao-abrir" not in args:
            subprocess.run(["open", str(destino)])
    elif args[0] in ("instagram", "tiktok", "espelhos"):
        fn = {"instagram": cmd_instagram, "tiktok": cmd_tiktok, "espelhos": cmd_espelhos}[args[0]]
        with LOCK.open("w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # não disputa o estado.json com a rodada do YouTube
            fn(cfg)
    elif args[0] == "logins":
        with LOCK.open("w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cmd_logins(cfg)
    elif args[0] == "tiktok-app":
        import tiktok_app
        tiktok_app.servir(cfg)
    elif args[0] == "tiktok-auth":
        cmd_tiktok_auth(cfg)
    elif args[0] == "teste-aviso":
        print("Enviado." if avisar("✅ Teste do agendador de Shorts.") else "Não enviado (veja o .env).")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
