"""Painel de desempenho dos Shorts: um HTML estático no layout do Tabler (template de
dashboard open source), regravado a cada rodada do agente com os dados do estado.json."""
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TABLER = "https://cdn.jsdelivr.net/npm/@tabler/core@1.0.0/dist"
ICONES = "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css"
APEX = "https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js"


def produto(titulo):
    """'Zero custo por chamada | Mangaba Brier #Shorts' -> 'Mangaba Brier'."""
    partes = titulo.replace("#Shorts", "").split("|")
    return partes[-1].strip() if len(partes) > 1 else "Outros"


def dados(cfg, estado, fila_n, fila_dias, agora_utc):
    tz = ZoneInfo(cfg["fuso"])
    videos = []
    for a in estado["agendados"]:
        pub = datetime.strptime(a["publish_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
        m = a.get("metricas") or {}
        local = pub.astimezone(tz)
        videos.append({
            "id": a["video_id"], "titulo": a["titulo"].replace(" #Shorts", "").split("|")[0].strip(), "produto": produto(a["titulo"]),
            "quando": local.strftime("%d/%m %H:%M"), "ordem": a["publish_at"], "hora": local.hour,
            "publicado": pub <= agora_utc, "views": m.get("views"), "likes": m.get("likes"),
            "comentarios": m.get("comentarios"), "gancho": (a.get("brier") or {}).get("gancho"),
            "alerta": a.get("alerta"),
        })
    serie = [{"x": datetime.strptime(p["em"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
              .astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"), "y": p["views"]} for p in estado.get("serie", [])]
    return {
        "atualizado": agora_utc.astimezone(tz).strftime("%d/%m/%Y %H:%M"),
        "regra": f"{cfg['posts_por_semana'] // 7} por dia · {min(cfg['horarios'])}–{max(cfg['horarios'])}",
        "fila": fila_n, "fila_dias": round(fila_dias, 1), "videos": videos, "serie": serie,
    }


def gerar(cfg, estado, fila_n, fila_dias, agora_utc, destino):
    destino = Path(destino)
    destino.parent.mkdir(exist_ok=True)
    js = json.dumps(dados(cfg, estado, fila_n, fila_dias, agora_utc), ensure_ascii=False).replace("</", "<\\/")
    destino.write_text(MODELO.replace("__DADOS__", js).replace("__CANAL__", html.escape(cfg.get("canal", ""))),
                       encoding="utf-8")
    return destino


MODELO = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>Painel de Shorts</title>
<link rel="stylesheet" href="{TABLER}/css/tabler.min.css">
<link rel="stylesheet" href="{ICONES}">
<style>
  :root, [data-bs-theme=light] {{ --tblr-primary: #E94A12; --tblr-primary-rgb: 233,74,18; }}
  .kpi-icone {{ font-size: 1.5rem; }}
  .titulo-video {{ max-width: 340px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .table td {{ vertical-align: middle; }}
</style>
</head>
<body>
<div class="page">
  <header class="navbar navbar-expand-md d-print-none">
    <div class="container-xl">
      <span class="navbar-brand pe-0 pe-md-3">
        <i class="ti ti-brand-youtube-filled text-primary me-2 kpi-icone"></i>Painel de Shorts
      </span>
      <div class="navbar-nav flex-row order-md-last">
        <span class="text-secondary small">Canal __CANAL__ · atualizado <b id="atualizado"></b></span>
      </div>
    </div>
  </header>
  <div class="page-wrapper">
    <div class="page-header d-print-none">
      <div class="container-xl">
        <div class="row g-2 align-items-center">
          <div class="col">
            <div class="page-pretitle">Desempenho</div>
            <h2 class="page-title">Shorts publicados pelo agendador</h2>
          </div>
          <div class="col-auto"><span class="badge bg-primary-lt" id="regra"></span></div>
        </div>
      </div>
    </div>
    <div class="page-body">
      <div class="container-xl">
        <div class="row row-deck row-cards">
          <div class="col-6 col-lg-3"><div class="card card-sm"><div class="card-body"><div class="row align-items-center">
            <div class="col-auto"><span class="bg-primary text-white avatar"><i class="ti ti-eye kpi-icone"></i></span></div>
            <div class="col"><div class="h2 mb-0" id="k-views"></div><div class="text-secondary">views no total</div></div>
          </div></div></div></div>
          <div class="col-6 col-lg-3"><div class="card card-sm"><div class="card-body"><div class="row align-items-center">
            <div class="col-auto"><span class="bg-green text-white avatar"><i class="ti ti-player-play kpi-icone"></i></span></div>
            <div class="col"><div class="h2 mb-0" id="k-pub"></div><div class="text-secondary" id="k-pub-sub"></div></div>
          </div></div></div></div>
          <div class="col-6 col-lg-3"><div class="card card-sm"><div class="card-body"><div class="row align-items-center">
            <div class="col-auto"><span class="bg-azure text-white avatar"><i class="ti ti-heart kpi-icone"></i></span></div>
            <div class="col"><div class="h2 mb-0" id="k-likes"></div><div class="text-secondary" id="k-likes-sub"></div></div>
          </div></div></div></div>
          <div class="col-6 col-lg-3"><div class="card card-sm"><div class="card-body"><div class="row align-items-center">
            <div class="col-auto"><span class="text-white avatar" id="k-fila-cor"><i class="ti ti-stack-2 kpi-icone"></i></span></div>
            <div class="col"><div class="h2 mb-0" id="k-fila"></div><div class="text-secondary" id="k-fila-sub"></div></div>
          </div></div></div></div>

          <div class="col-lg-8"><div class="card">
            <div class="card-header"><h3 class="card-title">Views acumuladas</h3></div>
            <div class="card-body"><div id="g-serie"></div></div>
          </div></div>
          <div class="col-lg-4"><div class="card">
            <div class="card-header"><h3 class="card-title">Views por produto</h3></div>
            <div class="card-body"><div id="g-produto"></div></div>
          </div></div>

          <div class="col-lg-6"><div class="card">
            <div class="card-header"><h3 class="card-title">Média de views por horário</h3></div>
            <div class="card-body"><div id="g-hora"></div></div>
          </div></div>
          <div class="col-lg-6"><div class="card">
            <div class="card-header"><h3 class="card-title">Gancho do Brier × views</h3></div>
            <div class="card-body"><div id="g-brier"></div></div>
          </div></div>

          <div class="col-lg-8"><div class="card">
            <div class="card-header"><h3 class="card-title">Vídeos publicados</h3></div>
            <div class="table-responsive">
              <table class="table card-table table-vcenter">
                <thead><tr><th>Vídeo</th><th>Publicado</th><th class="text-end">Views</th>
                  <th class="text-end">Likes</th><th class="text-end">Coment.</th><th class="text-end">Gancho</th></tr></thead>
                <tbody id="t-pub"></tbody>
              </table>
            </div>
          </div></div>
          <div class="col-lg-4"><div class="card">
            <div class="card-header"><h3 class="card-title">Próximos</h3></div>
            <div class="list-group list-group-flush overflow-auto" style="max-height: 32rem" id="l-prox"></div>
          </div></div>
        </div>
      </div>
    </div>
    <footer class="footer footer-transparent d-print-none">
      <div class="container-xl text-secondary small">
        Dados da YouTube Data API, coletados a cada rodada do agente (a cada 2 h). A página recarrega sozinha a cada 10 min.
      </div>
    </footer>
  </div>
</div>
<script src="{TABLER}/js/tabler.min.js"></script>
<script src="{APEX}"></script>
<script>
const D = __DADOS__;
const fmt = n => n == null ? "–" : n.toLocaleString("pt-BR");
const esc = s => String(s).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
const cor = getComputedStyle(document.documentElement).getPropertyValue("--tblr-primary").trim() || "#E94A12";
const base = {{ chart: {{ fontFamily: "inherit", toolbar: {{ show: false }}, animations: {{ enabled: false }} }},
  dataLabels: {{ enabled: false }}, colors: [cor], grid: {{ strokeDashArray: 4 }} }};

const pub = D.videos.filter(v => v.publicado).sort((a, b) => (b.views ?? -1) - (a.views ?? -1));
const prox = D.videos.filter(v => !v.publicado).sort((a, b) => a.ordem.localeCompare(b.ordem));
const soma = k => pub.reduce((s, v) => s + (v[k] || 0), 0);
const views = soma("views"), likes = soma("likes");

document.getElementById("atualizado").textContent = D.atualizado;
document.getElementById("regra").textContent = D.regra;
document.getElementById("k-views").textContent = fmt(views);
document.getElementById("k-pub").textContent = fmt(pub.length);
document.getElementById("k-pub-sub").textContent = `publicados · ${{prox.length}} agendados`;
document.getElementById("k-likes").textContent = fmt(likes);
document.getElementById("k-likes-sub").textContent = `likes · ${{pub.length ? (views / pub.length).toFixed(1) : 0}} views/vídeo`;
document.getElementById("k-fila").textContent = fmt(D.fila);
document.getElementById("k-fila-sub").textContent = `na fila · ~${{D.fila_dias}} dia(s)`;
document.getElementById("k-fila-cor").classList.add(D.fila_dias < 1 ? "bg-red" : D.fila_dias < 3 ? "bg-yellow" : "bg-green");

const vazio = (id, txt) => document.getElementById(id).innerHTML =
  `<div class="empty py-4"><p class="empty-subtitle text-secondary">${{txt}}</p></div>`;

if (D.serie.length > 1) new ApexCharts(document.getElementById("g-serie"), {{ ...base,
  chart: {{ ...base.chart, type: "area", height: 260 }}, stroke: {{ width: 2, curve: "smooth" }},
  fill: {{ opacity: .16, type: "solid" }}, series: [{{ name: "Views", data: D.serie }}],
  xaxis: {{ type: "datetime", labels: {{ datetimeUTC: false }} }},
  tooltip: {{ x: {{ format: "dd/MM HH:mm" }} }} }}).render();
else vazio("g-serie", "O gráfico aparece a partir da segunda rodada do agente.");

const porProd = {{}};
pub.forEach(v => porProd[v.produto] = (porProd[v.produto] || 0) + (v.views || 0));
const prods = Object.entries(porProd).sort((a, b) => b[1] - a[1]);
if (prods.length) new ApexCharts(document.getElementById("g-produto"), {{ ...base,
  chart: {{ ...base.chart, type: "bar", height: Math.max(180, prods.length * 34) }},
  plotOptions: {{ bar: {{ horizontal: true, borderRadius: 3, barHeight: "60%" }} }},
  series: [{{ name: "Views", data: prods.map(p => p[1]) }}], xaxis: {{ categories: prods.map(p => p[0]) }} }}).render();
else vazio("g-produto", "Sem vídeos publicados ainda.");

const horas = [...new Set(D.videos.map(v => v.hora))].sort((a, b) => a - b);
const media = horas.map(h => {{ const vs = pub.filter(v => v.hora === h && v.views != null);
  return vs.length ? +(vs.reduce((s, v) => s + v.views, 0) / vs.length).toFixed(1) : 0; }});
const qtd = horas.map(h => pub.filter(v => v.hora === h).length);
new ApexCharts(document.getElementById("g-hora"), {{ ...base,
  chart: {{ ...base.chart, type: "bar", height: 260 }}, plotOptions: {{ bar: {{ borderRadius: 3, columnWidth: "55%" }} }},
  series: [{{ name: "Média de views", data: media }}], xaxis: {{ categories: horas.map(h => String(h).padStart(2, "0") + "h") }},
  tooltip: {{ y: {{ formatter: (v, o) => `${{v}} (${{qtd[o.dataPointIndex]}} vídeo(s))` }} }} }}).render();

const pontos = pub.filter(v => v.gancho != null && v.views != null).map(v => ({{ x: +v.gancho.toFixed(2), y: v.views, t: v.titulo }}));
if (pontos.length) new ApexCharts(document.getElementById("g-brier"), {{ ...base,
  chart: {{ ...base.chart, type: "scatter", height: 260, zoom: {{ enabled: false }} }}, markers: {{ size: 7 }},
  series: [{{ name: "Vídeo", data: pontos }}],
  xaxis: {{ type: "numeric", min: 0.4, max: 0.8, tickAmount: 4, title: {{ text: "gancho (Brier)" }},
    labels: {{ formatter: v => (+v).toFixed(2) }} }}, yaxis: {{ title: {{ text: "views" }} }},
  tooltip: {{ custom: ({{ dataPointIndex: i }}) => `<div class="p-2 small">${{esc(pontos[i].t)}}<br>gancho ${{pontos[i].x}} · ${{pontos[i].y}} views</div>` }} }}).render();
else vazio("g-brier", "Aparece quando os vídeos avaliados pelo Brier forem publicados.");

document.getElementById("t-pub").innerHTML = pub.map(v => `<tr>
  <td><div class="titulo-video"><a href="https://youtube.com/shorts/${{v.id}}" target="_blank" rel="noopener">${{esc(v.titulo)}}</a></div>
    <div class="text-secondary small">${{esc(v.produto)}}${{v.alerta ? ` · <span class="text-danger">${{esc(v.alerta)}}</span>` : ""}}</div></td>
  <td class="text-secondary text-nowrap">${{v.quando}}</td><td class="text-end fw-bold">${{fmt(v.views)}}</td>
  <td class="text-end">${{fmt(v.likes)}}</td><td class="text-end">${{fmt(v.comentarios)}}</td>
  <td class="text-end">${{v.gancho == null ? "–" : v.gancho.toFixed(2)}}</td></tr>`).join("")
  || `<tr><td colspan="6" class="text-secondary">Nenhum vídeo publicado ainda.</td></tr>`;

document.getElementById("l-prox").innerHTML = prox.map(v => `<div class="list-group-item">
  <div class="row align-items-center"><div class="col-auto"><span class="badge bg-primary-lt">${{v.quando}}</span></div>
  <div class="col text-truncate"><span class="text-reset">${{esc(v.titulo)}}</span>
  <div class="text-secondary small text-truncate">${{esc(v.produto)}}${{v.alerta ? ` · <span class="text-danger">${{esc(v.alerta)}}</span>` : ""}}</div></div></div></div>`).join("")
  || `<div class="list-group-item text-secondary">Nada agendado. Coloque vídeos na pasta.</div>`;
</script>
</body>
</html>
"""
