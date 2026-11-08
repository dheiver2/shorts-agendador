import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agente  # noqa: E402

CFG = {
    "fuso": "America/Sao_Paulo",
    "posts_por_semana": 30,
    "horarios": ["12:00", "19:00", "21:00", "17:00", "09:00"],
    "horarios_reserva": ["15:00", "10:00"],
    "dias_extra": ["sab", "dom"],
    "dias_a_frente": 1,
    "max_por_dia": 6,
    "duracao_max_seg": 180,
    "descricao_padrao": "",
}


def brt(y, m, d, h, mi=0):
    """Hora de Brasília (UTC-3) em UTC."""
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc) + timedelta(hours=3)


def test_semana_soma_30_com_5_no_fim_de_semana():
    seg = date(2026, 11, 9)
    por_dia = [len(agente.horarios_do_dia(CFG, seg + timedelta(days=i))) for i in range(7)]
    assert por_dia == [4, 4, 4, 4, 4, 5, 5]
    assert sum(por_dia) == 30


@pytest.mark.parametrize("total,esperado", [(28, 28), (35, 35), (14, 14), (31, 31)])
def test_outros_totais_semanais(total, esperado):
    cfg = {**CFG, "posts_por_semana": total, "dias_extra": ["sab", "dom", "sex"]}
    seg = date(2026, 11, 9)
    assert sum(len(agente.horarios_do_dia(cfg, seg + timedelta(days=i))) for i in range(7)) == esperado


def test_proximos_horarios_respeita_margem():
    agora = brt(2026, 11, 7, 20, 55)  # sábado 20:55 → 21:00 está dentro da margem
    slots = list(agente.proximos_horarios(CFG, agora))
    assert brt(2026, 11, 7, 21) not in slots
    assert slots[0] == brt(2026, 11, 8, 9)
    assert len(slots) == 5  # só domingo


def estado_com(publicados, inicio):
    return {"inicio": agente.iso(inicio),
            "agendados": [{"publish_at": agente.iso(s), "video_id": "x", "titulo": "t"} for s in publicados]}


def test_perdido_conta_so_depois_do_inicio():
    inicio = brt(2026, 11, 7, 20, 55)
    agora = brt(2026, 11, 7, 22)
    perdidos = agente.horarios_perdidos(CFG, estado_com([], inicio), agora)
    assert perdidos == [brt(2026, 11, 7, 21)]  # 09/12/17/19h de sábado eram antes do início


def test_reposicao_usa_horario_reserva_no_domingo():
    inicio = brt(2026, 11, 7, 20, 55)
    agora = brt(2026, 11, 7, 22)
    domingo = [brt(2026, 11, 8, h) for h in (9, 12, 17, 19, 21)]
    rep = agente.horarios_reposicao(CFG, estado_com(domingo, inicio), agora)
    assert rep == [brt(2026, 11, 8, 15)]


def test_reposicao_nao_repete_depois_de_reposto():
    inicio = brt(2026, 11, 7, 20, 55)
    agora = brt(2026, 11, 7, 22)
    est = estado_com([brt(2026, 11, 8, h) for h in (9, 12, 17, 19, 21)], inicio)
    est["agendados"].append({"publish_at": agente.iso(brt(2026, 11, 8, 15)), "reposicao": True,
                             "video_id": "y", "titulo": "t"})
    assert agente.horarios_reposicao(CFG, est, agora) == []


def test_reposicao_respeita_max_por_dia():
    cfg = {**CFG, "max_por_dia": 5}  # domingo já tem 5 regulares
    inicio = brt(2026, 11, 7, 20, 55)
    agora = brt(2026, 11, 7, 22)
    rep = agente.horarios_reposicao(cfg, estado_com([], inicio), agora)
    assert rep == []


def test_reposicao_nao_atravessa_semana():
    # domingo 22h: perdeu 21h de domingo; amanhã é outra semana → não repõe
    inicio = brt(2026, 11, 8, 8)
    agora = brt(2026, 11, 8, 22)
    feitos = [brt(2026, 11, 8, h) for h in (9, 12, 17, 19)]
    assert agente.horarios_reposicao(CFG, estado_com(feitos, inicio), agora) == []


def test_reposicao_dia_util_usa_9h_antes_da_reserva():
    inicio = brt(2026, 11, 9, 8)          # segunda
    agora = brt(2026, 11, 10, 23)         # terça 23h; perdeu tudo de seg e ter (8)
    rep = agente.horarios_reposicao(CFG, estado_com([], inicio), agora)
    # quarta (dentro da janela de 1 dia) tem 4 regulares → cabem 2 extras: 09:00 e 15:00
    assert rep == [brt(2026, 11, 11, 9), brt(2026, 11, 11, 15)]


def test_metadados_tira_numeracao_e_poe_shorts(tmp_path):
    v = tmp_path / "001-dica-de-ia_rapida.mp4"
    v.write_bytes(b"")
    titulo, desc, side = agente.metadados(v, CFG)
    assert titulo == "dica de ia rapida #Shorts"
    assert "#Shorts" in desc and side is None


def test_metadados_usa_txt(tmp_path):
    v = tmp_path / "02 - x.mp4"
    v.write_bytes(b"")
    (tmp_path / "02 - x.txt").write_text("Meu título\nLinha 1\nLinha 2")
    titulo, desc, side = agente.metadados(v, CFG)
    assert titulo == "Meu título #Shorts"
    assert desc.startswith("Linha 1\nLinha 2")
    assert side is not None


def test_validar():
    assert agente.validar({"largura": 1080, "altura": 1920, "duracao": 59}, CFG) is None
    assert "horizontal" in agente.validar({"largura": 1920, "altura": 1080, "duracao": 59}, CFG)
    assert "181s" in agente.validar({"largura": 1080, "altura": 1920, "duracao": 181}, CFG)


def test_sha256_igual_para_mesmo_conteudo(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"abc" * 1000)
    b.write_bytes(b"abc" * 1000)
    assert agente.sha256(a) == agente.sha256(b)


AGENDADO = "2026-11-08T15:00:00Z"


@pytest.mark.parametrize("item,agora,esperado", [
    (None, brt(2026, 11, 7, 22), "sumiu"),
    ({"status": {"uploadStatus": "rejected", "rejectionReason": "duplicate"}}, brt(2026, 11, 7, 22), "rejected"),
    ({"status": {"privacyStatus": "private", "publishAt": AGENDADO}}, brt(2026, 11, 7, 22), None),
    ({"status": {"privacyStatus": "private"}}, brt(2026, 11, 7, 22), "agendamento mudou"),
    ({"status": {"privacyStatus": "public"}}, brt(2026, 11, 8, 13), None),
    ({"status": {"privacyStatus": "private"}}, brt(2026, 11, 8, 13), "não ficou público"),
    ({"status": {"privacyStatus": "private"}}, brt(2026, 11, 8, 12, 10), None),  # ainda na tolerância
])
def test_diagnosticar(item, agora, esperado):
    r = agente.diagnosticar(item, AGENDADO, agora)
    assert (r is None) if esperado is None else (esperado in r)


# ---------------- Brier ----------------
import brier  # noqa: E402

CFG_B = {"corte_risco": 0.8, "corte_enganoso": 0.85, "corte_hashtag": 0.6, "max_hashtags": 2}


def resposta(gancho_probs, enganoso=0.1, risco=0.1, tags=None):
    a = {"gancho": {"probabilities": gancho_probs},
         "enganoso": {"noul": enganoso}, "risco": {"noul": risco},
         "publico": {"choice": "gestor", "confidence": 0.4}}
    for t, p in (tags or {}).items():
        a[f"tag_{t}"] = {"noul": p}
    return {"answers": a, "model": "brier-v5"}


def test_interpretar_gancho_esperado_e_hashtags():
    av = brier.interpretar(resposta({"0": 0, "1": 0, "2": 0, "3": 0, "4": 1},
                                    tags={"IA": 0.9, "Telecom": 0.7, "Tecnologia": 0.65, "ChatGPT": 0.2}), CFG_B)
    assert av["gancho"] == 1.0
    assert av["hashtags"] == ["IA", "Telecom"]  # ordem por probabilidade, limitado a 2
    assert av["publico"] == "gestor"


def test_segurar_por_risco_e_enganoso():
    ok = brier.interpretar(resposta({"2": 1}), CFG_B)
    assert brier.segurar(ok, CFG_B) is None
    assert "risco" in brier.segurar(brier.interpretar(resposta({"2": 1}, risco=0.9), CFG_B), CFG_B)
    assert "enganoso" in brier.segurar(brier.interpretar(resposta({"2": 1}, enganoso=0.9), CFG_B), CFG_B)


def test_hashtags_entram_antes_do_shorts_sem_repetir():
    d = brier.descricao_com_hashtags("Texto com #IA\n\n#Shorts", ["IA", "Telecom"])
    assert d.endswith("#Telecom #Shorts")
    assert d.count("#IA") == 1


def test_distribuir_maior_gancho_no_melhor_horario():
    pares = brier.distribuir([("a", 0.5), ("b", 0.9), ("c", 0.7)], [(3, "17h"), (0, "12h"), (1, "19h")])
    assert dict(pares) == {"12h": "b", "19h": "c", "17h": "a"}


def test_cache_muda_com_texto():
    assert brier.chave_cache("h", "t1", "d") != brier.chave_cache("h", "t2", "d")
