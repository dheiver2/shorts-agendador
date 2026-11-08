"""Tela local para aprovar cada post no TikTok (http://127.0.0.1:8770).

Segue a parte "Required UX Implementation" das Content Sharing Guidelines do TikTok:
nome da conta, prévia do vídeo, privacidade escolhida na mão (sem padrão), interações
desligadas por padrão, divulgação de conteúdo comercial, texto de consentimento de
música e envio só depois do clique em Post. Uso: `agente.py tiktok-app`.
"""
import fcntl
import json
import mimetypes
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agente
import tiktok

PORTA = 8770
JANELA_H = 48  # vídeos que saíram no YouTube nas últimas 48 h e ainda não foram ao TikTok


def _token():
    chave, segredo = agente.tiktok_credenciais()
    tok = agente.carregar(agente.TIKTOK_TOKEN, None)
    if not (chave and segredo and tok):
        raise RuntimeError("TikTok sem login. Rode: .venv/bin/python agente.py tiktok-auth")
    tok, mudou = tiktok.token_valido(tok, chave, segredo)
    if mudou:
        agente.TIKTOK_TOKEN.write_text(json.dumps(tok))
        agente.TIKTOK_TOKEN.chmod(0o600)
    return tok["access_token"]


def pendentes(cfg, estado, agora):
    desde = agora - timedelta(hours=JANELA_H)
    marca = estado.get("tiktok_desde")
    if marca:
        desde = max(desde, agente.de_iso(marca))
    postados = Path(cfg["pasta_videos"]).expanduser() / "postados"
    itens = []
    for a in estado.get("agendados", []):
        quando = agente.de_iso(a["publish_at"])
        if "tiktok" in a or quando > agora or quando < desde:
            continue
        video = postados / a["arquivo"]
        if not video.exists():
            continue
        _, desc, _ = agente.metadados(video, cfg)
        if a.get("brier", {}).get("hashtags"):
            desc = agente.brier.descricao_com_hashtags(desc, a["brier"]["hashtags"])
        info, _ = agente.sondar(video)
        itens.append({"arquivo": a["arquivo"], "publish_at": a["publish_at"],
                      "legenda": agente.instagram.legenda(a["titulo"], desc),
                      "duracao": round((info or {}).get("duracao", 0))})
    return sorted(itens, key=lambda i: i["publish_at"])


def _gravar(arquivo, registro):
    """Grava o resultado no estado.json com a mesma trava da rodada automática."""
    with agente.LOCK.open("w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        estado = agente.carregar(agente.ESTADO, {"agendados": []})
        for a in estado["agendados"]:
            if a["arquivo"] == arquivo:
                a["tiktok"] = registro
        agente.salvar_estado(estado)


class Tela(BaseHTTPRequestHandler):
    cfg = None

    def log_message(self, *a):
        pass

    def _json(self, obj, codigo=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/":
                corpo = PAGINA.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
            elif url.path == "/api/dados":
                info = tiktok.creator_info(_token())  # sempre fresco ao abrir a tela de post
                estado = agente.carregar(agente.ESTADO, {"agendados": []})
                self._json({"creator": info, "pendentes": pendentes(self.cfg, estado, datetime.now(timezone.utc))})
            elif url.path.startswith("/api/status/"):
                self._json(tiktok.status(_token(), url.path.rsplit("/", 1)[1]))
            elif url.path.startswith("/video/"):
                nome = urllib.parse.unquote(url.path[len("/video/"):])
                video = Path(self.cfg["pasta_videos"]).expanduser() / "postados" / Path(nome).name
                if not video.exists():
                    return self._json({"erro": "vídeo não encontrado"}, 404)
                dados = video.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(video.name)[0] or "video/mp4")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)
            else:
                self._json({"erro": "não encontrado"}, 404)
        except Exception as e:
            self._json({"erro": str(e)[:300]}, 500)

    def do_POST(self):
        if self.path != "/api/publicar":
            return self._json({"erro": "não encontrado"}, 404)
        try:
            escolhas = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            token = _token()
            info = tiktok.creator_info(token)
            post_info = tiktok.montar_post_info(escolhas, info)
            video = Path(self.cfg["pasta_videos"]).expanduser() / "postados" / Path(escolhas["arquivo"]).name
            publish_id = tiktok.enviar(token, video, post_info)
            _gravar(escolhas["arquivo"], {"publish_id": publish_id, "privacidade": post_info["privacy_level"],
                                          "aprovado_em": agente.iso(datetime.now(timezone.utc))})
            agente.log(f"TIKTOK aprovado na tela {escolhas['arquivo']} → {publish_id} ({post_info['privacy_level']})")
            self._json({"publish_id": publish_id})
        except ValueError as e:
            self._json({"erro": str(e)}, 400)
        except Exception as e:
            self._json({"erro": str(e)[:300]}, 500)


def servir(cfg, porta=PORTA):
    Tela.cfg = cfg
    srv = ThreadingHTTPServer(("127.0.0.1", porta), Tela)
    agente.log(f"Tela do TikTok em http://127.0.0.1:{porta}")
    srv.serve_forever()


PAGINA = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agendador de Shorts · Post to TikTok</title>
<style>
:root{--c:#E94A12;--bg:#FFF8EE;--tx:#222;--mu:#6b6b6b;--ln:#e7dccd}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx)}
header{background:#fff;border-bottom:1px solid var(--ln);padding:14px 20px;display:flex;align-items:center;gap:10px}
header b{color:var(--c)}main{max-width:980px;margin:0 auto;padding:20px 16px}
.lista{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}.lista button{border:1px solid var(--ln);background:#fff;border-radius:8px;padding:6px 10px;cursor:pointer}
.lista button.on{border-color:var(--c);color:var(--c);font-weight:600}
.grade{display:grid;grid-template-columns:300px 1fr;gap:24px}@media(max-width:760px){.grade{grid-template-columns:1fr}}
video{width:100%;border-radius:12px;background:#000;aspect-ratio:9/16}
.card{background:#fff;border:1px solid var(--ln);border-radius:12px;padding:16px;margin-bottom:12px}
label.l{display:block;font-weight:600;margin:0 0 6px}textarea{width:100%;min-height:110px;border:1px solid var(--ln);border-radius:8px;padding:8px;font:inherit}
select{width:100%;padding:8px;border-radius:8px;border:1px solid var(--ln);font:inherit;background:#fff}
.linha{display:flex;align-items:center;gap:8px;margin:6px 0}.mu{color:var(--mu);font-size:13px}
.aviso{background:#fff3e0;border:1px solid #f5c38b;border-radius:8px;padding:8px 10px;font-size:13px;margin-top:8px}
button.post{background:var(--c);color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:16px;font-weight:600;cursor:pointer;width:100%}
button.post:disabled{opacity:.45;cursor:not-allowed}.erro{color:#b00020}.ok{color:#1b7f3b}
</style></head><body>
<header><b>Agendador de Shorts</b><span class="mu">· Post to TikTok</span></header>
<main>
<div id="topo" class="mu">Loading…</div>
<div class="lista" id="lista"></div>
<div id="tela"></div>
</main>
<script>
const NOMES={PUBLIC_TO_EVERYONE:"Everyone",MUTUAL_FOLLOW_FRIENDS:"Friends",FOLLOWER_OF_CREATOR:"Followers",SELF_ONLY:"Only me"};
let D=null, atual=null;
const el=(t,a={},...f)=>{const e=document.createElement(t);for(const[k,v]of Object.entries(a)){if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else if(k==="checked"||k==="disabled")e[k]=v;else e.setAttribute(k,v)}f.flat().forEach(x=>e.append(x instanceof Node?x:document.createTextNode(x??"")));return e};
async function carregar(){
  const r=await fetch("/api/dados"),j=await r.json();
  if(j.erro){document.getElementById("topo").textContent="Error: "+j.erro;return}
  D=j;const c=j.creator;
  document.getElementById("topo").replaceChildren(el("span",{},"Posting to TikTok account: "),el("b",{},c.creator_nickname+" (@"+c.creator_username+")"));
  if(!j.pendentes.length){document.getElementById("tela").replaceChildren(el("div",{class:"card"},"No videos waiting for approval. Each video appears here at its scheduled time."));return}
  const l=document.getElementById("lista");l.replaceChildren(...j.pendentes.map((p,i)=>el("button",{onclick:()=>abrir(i),id:"b"+i},new Date(p.publish_at).toLocaleString([], {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}))));
  abrir(0);
}
function abrir(i){
  atual=D.pendentes[i];const c=D.creator;
  document.querySelectorAll(".lista button").forEach((b,k)=>b.classList.toggle("on",k===i));
  const longo=c.max_video_post_duration_sec&&atual.duracao>c.max_video_post_duration_sec;
  const priv=el("select",{id:"priv",onchange:validar},el("option",{value:""},"Select who can view this video"),...(c.privacy_level_options||[]).map(o=>el("option",{value:o},NOMES[o]||o)));
  const cx=(id,txt,dis)=>el("div",{class:"linha"},el("input",{type:"checkbox",id,disabled:!!dis,onchange:validar}),el("label",{for:id},txt+(dis?" (turned off in your TikTok settings)":"")));
  document.getElementById("tela").replaceChildren(el("div",{class:"grade"},
    el("div",{},el("video",{src:"/video/"+encodeURIComponent(atual.arquivo),controls:"",playsinline:""}),el("p",{class:"mu"},"Preview of the video that will be posted")),
    el("div",{},
      el("div",{class:"card"},el("label",{class:"l",for:"tit"},"Caption"),el("textarea",{id:"tit",maxlength:"2200",oninput:validar},atual.legenda),el("div",{class:"mu"},"You can edit the caption and hashtags.")),
      el("div",{class:"card"},el("label",{class:"l",for:"priv"},"Who can view this video"),priv),
      el("div",{class:"card"},el("label",{class:"l"},"Allow users to"),cx("com","Comment",c.comment_disabled),cx("duet","Duet",c.duet_disabled),cx("sti","Stitch",c.stitch_disabled)),
      el("div",{class:"card"},el("div",{class:"linha"},el("input",{type:"checkbox",id:"dis",onchange:validar}),el("label",{for:"dis",class:"l",style:"margin:0"},"Disclose video content")),
        el("div",{class:"mu"},"Turn on to disclose that this video promotes goods or services in exchange for something of value."),
        el("div",{id:"opc",style:"display:none;margin-top:8px"},cx("sua","Your brand — you are promoting yourself or your own business"),cx("marca","Branded content — you are promoting another brand or a third party"),el("div",{id:"rot",class:"aviso",style:"display:none"}))),
      el("div",{class:"card"},el("div",{class:"linha"},el("input",{type:"checkbox",id:"ia",onchange:validar}),el("label",{for:"ia"},"AI-generated content"))),
      longo?el("div",{class:"aviso erro"},"This video is longer than the maximum allowed for your account ("+c.max_video_post_duration_sec+" s)."):"",
      el("p",{id:"cons",class:"mu"}),
      el("button",{class:"post",id:"post",onclick:postar,disabled:true},"Post to TikTok"),
      el("p",{id:"msg"}))));
  validar();
}
function validar(){
  const dis=document.getElementById("dis").checked,sua=document.getElementById("sua").checked,marca=document.getElementById("marca").checked;
  document.getElementById("opc").style.display=dis?"block":"none";
  const priv=document.getElementById("priv");
  [...priv.options].forEach(o=>{if(o.value==="SELF_ONLY")o.disabled=dis&&marca});
  if(dis&&marca&&priv.value==="SELF_ONLY")priv.value="";
  const rot=document.getElementById("rot");
  rot.style.display=dis&&(sua||marca)?"block":"none";
  rot.textContent=marca?'Your video will be labeled as "Paid partnership".':'Your video will be labeled as "Promotional content".';
  const link=(t,u)=>el("a",{href:u,target:"_blank"},t);
  document.getElementById("cons").replaceChildren("By posting, you agree to TikTok's ",
    ...(dis&&marca?[link("Branded Content Policy","https://www.tiktok.com/legal/page/global/bc-policy/en")," and "]:[]),
    link("Music Usage Confirmation","https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"),".");
  const c=D.creator,longo=c.max_video_post_duration_sec&&atual.duracao>c.max_video_post_duration_sec;
  const ok=priv.value&&document.getElementById("tit").value.trim()&&!(dis&&!sua&&!marca)&&!longo;
  document.getElementById("post").disabled=!ok;
  document.getElementById("post").title=dis&&!sua&&!marca?"You need to indicate if your content promotes yourself, a third party, or both.":"";
}
async function postar(){
  const b=document.getElementById("post"),m=document.getElementById("msg");b.disabled=true;m.className="";m.textContent="Uploading…";
  const dis=document.getElementById("dis").checked;
  const r=await fetch("/api/publicar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
    arquivo:atual.arquivo,titulo:document.getElementById("tit").value,privacidade:document.getElementById("priv").value,
    comentar:document.getElementById("com").checked,duet:document.getElementById("duet").checked,stitch:document.getElementById("sti").checked,
    ia:document.getElementById("ia").checked,comercial:{ativo:dis,sua_marca:document.getElementById("sua").checked,conteudo_marca:document.getElementById("marca").checked}})});
  const j=await r.json();
  if(j.erro){m.className="erro";m.textContent=j.erro;b.disabled=false;return}
  m.className="ok";m.textContent="Sent. It may take a few minutes for the content to process and be visible on your profile.";
  const id=j.publish_id;
  for(let k=0;k<60;k++){await new Promise(s=>setTimeout(s,5000));const s=await (await fetch("/api/status/"+id)).json();
    if(s.status==="PUBLISH_COMPLETE"){m.textContent="Posted to TikTok ✓";break}
    if(s.status==="FAILED"){m.className="erro";m.textContent="TikTok could not post: "+(s.fail_reason||"unknown reason");break}
    m.textContent="Processing on TikTok ("+(s.status||"…")+"). It may take a few minutes to be visible on your profile.";}
}
carregar();
</script></body></html>"""
