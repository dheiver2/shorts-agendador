import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agente  # noqa: E402
import instagram  # noqa: E402

AGORA = datetime(2026, 11, 9, 15, 5, tzinfo=timezone.utc)  # 12:05 em Brasília


def reg(horas_atras, **extra):
    return {"publish_at": agente.iso(AGORA - timedelta(hours=horas_atras)), "arquivo": f"{horas_atras}.mp4",
            "titulo": "t", **extra}


def test_pendentes_separa_no_horario_atrasado_e_futuro():
    estado = {"agendados": [reg(0.1), reg(1.5), reg(5), reg(-1), reg(0.5, instagram={"id": "1"})]}
    desde = AGORA - timedelta(days=1)
    pub, perd = instagram.pendentes(estado, AGORA, desde, 2, agente.de_iso)
    assert [a["arquivo"] for a in pub] == ["1.5.mp4", "0.1.mp4"]  # o mais antigo primeiro
    assert [a["arquivo"] for a in perd] == ["5.mp4"]


def test_pendentes_ignora_o_que_saiu_antes_do_inicio():
    estado = {"agendados": [reg(0.5), reg(1.5)]}
    pub, perd = instagram.pendentes(estado, AGORA, AGORA - timedelta(hours=1), 2, agente.de_iso)
    assert [a["arquivo"] for a in pub] == ["0.5.mp4"] and perd == []


def test_legenda_tira_shorts_e_limita():
    assert instagram.legenda("Título", "linha\n#ia #Shorts") == "Título\n\nlinha\n#ia"
    assert len(instagram.legenda("x" * 3000, "")) == instagram.LEGENDA_MAX


def test_publicar_reel_em_modo_teste(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"abc")
    chamadas, status = [], iter(["IN_PROGRESS", "FINISHED"])

    def chamar(url, dados=None, cabecalhos=None, metodo=None, corpo=None, timeout=60):
        chamadas.append((url, dados, cabecalhos, corpo))
        if url.endswith("/media"):
            return {"id": "C1"}
        if "rupload" in url:
            return {"success": True}
        if "fields=status_code" in url:
            return {"status_code": next(status)}
        return {"id": "POST9"}

    assert instagram.publicar_reel("TK", "IG", video, "leg", chamar=chamar, dormir=lambda s: None) == "POST9"
    criar = chamadas[0][1]
    assert criar["media_type"] == "REELS" and criar["upload_type"] == "resumable"
    assert json.loads(criar["trial_params"]) == {"graduation_strategy": "MANUAL"}
    assert chamadas[1][0].endswith("/C1") and chamadas[1][2]["file_size"] == "3" and chamadas[1][3] == b"abc"
    assert chamadas[-1][1]["creation_id"] == "C1"


def test_publicar_reel_normal_sem_trial(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"a")
    vistos = []

    def chamar(url, dados=None, **kw):
        vistos.append(dados)
        if url.endswith("/media"):
            return {"id": "C"}
        if "fields=" in url:
            return {"status_code": "FINISHED"}
        return {"id": "P", "success": True}

    instagram.publicar_reel("TK", "IG", video, "l", teste=False, chamar=chamar, dormir=lambda s: None)
    assert "trial_params" not in vistos[0]


def test_publicar_reel_erro_de_processamento(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"a")

    def chamar(url, dados=None, **kw):
        if url.endswith("/media"):
            return {"id": "C"}
        if "fields=" in url:
            return {"status_code": "ERROR", "status": "formato"}
        return {"success": True}

    try:
        instagram.publicar_reel("TK", "IG", video, "l", chamar=chamar, dormir=lambda s: None)
    except RuntimeError as e:
        assert "formato" in str(e)
    else:
        raise AssertionError("devia falhar")


# ---------- TikTok ----------
import tiktok  # noqa: E402


def test_tiktok_pedacos():
    assert tiktok.pedacos(30 * tiktok.MB) == (30 * tiktok.MB, 1, [(0, 30 * tiktok.MB - 1)])
    tam, total, faixas = tiktok.pedacos(75 * tiktok.MB)
    assert tam == 10 * tiktok.MB and total == 7
    assert faixas[-1] == (60 * tiktok.MB, 75 * tiktok.MB - 1)  # o último absorve o resto
    assert sum(b - a + 1 for a, b in faixas) == 75 * tiktok.MB


def fake_tiktok(opcoes, chamadas, status=("PROCESSING_UPLOAD", "PUBLISH_COMPLETE")):
    st = iter(status)

    def chamar(url, json_corpo=None, form=None, token=None, metodo=None, corpo=None, cabecalhos=None, timeout=60):
        chamadas.append((url, json_corpo, corpo, cabecalhos))
        ok = {"error": {"code": "ok"}}
        if url.endswith("creator_info/query/"):
            return {**ok, "data": {"privacy_level_options": opcoes}}
        if url.endswith("video/init/"):
            return {**ok, "data": {"publish_id": "P1", "upload_url": "https://up/x"}}
        if url.endswith("status/fetch/"):
            s = next(st)
            return {**ok, "data": {"status": s, "publicaly_available_post_id": [123] if s == "PUBLISH_COMPLETE" else []}}
        return {}
    return chamar


def test_tiktok_publica_publico_quando_permitido(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"12345")
    cham = []
    r = tiktok.publicar("TK", v, "leg", chamar=fake_tiktok(["PUBLIC_TO_EVERYONE", "SELF_ONLY"], cham),
                        dormir=lambda s: None)
    assert r == {"publish_id": "P1", "privacidade": "PUBLIC_TO_EVERYONE", "post_id": "123"}
    init = cham[1][1]
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 5, "chunk_size": 5, "total_chunk_count": 1}
    assert cham[2][0] == "https://up/x" and cham[2][2] == b"12345" and cham[2][3]["Content-Range"] == "bytes 0-4/5"


def test_tiktok_sem_auditoria_cai_para_privado(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"1")
    cham = []
    r = tiktok.publicar("TK", v, "leg", chamar=fake_tiktok(["SELF_ONLY"], cham), dormir=lambda s: None)
    assert r["privacidade"] == "SELF_ONLY" and cham[1][1]["post_info"]["privacy_level"] == "SELF_ONLY"


def test_tiktok_falha_mostra_motivo(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"1")
    try:
        tiktok.publicar("TK", v, "l", chamar=fake_tiktok(["SELF_ONLY"], [], status=("FAILED",)), dormir=lambda s: None)
    except RuntimeError as e:
        assert "não publicou" in str(e)
    else:
        raise AssertionError("devia falhar")


def test_tiktok_renova_token_perto_de_vencer():
    tok = {"access_token": "A", "refresh_token": "R", "expira_em": 1000 + 600, "refresh_expira_em": 10**9}
    vistos = []

    def chamar(url, form=None, **kw):
        vistos.append(form)
        return {"access_token": "B", "refresh_token": "R2", "expires_in": 86400, "refresh_expires_in": 100}
    novo, mudou = tiktok.token_valido(tok, "K", "S", chamar=chamar, agora=1000)
    assert mudou and novo["access_token"] == "B" and vistos[0]["grant_type"] == "refresh_token"
    assert tiktok.token_valido(novo, "K", "S", chamar=chamar, agora=1000) == (novo, False)


def test_tiktok_login_vencido_pede_auth():
    tok = {"access_token": "A", "refresh_token": "R", "expira_em": 0, "refresh_expira_em": 5}
    try:
        tiktok.token_valido(tok, "K", "S", agora=10)
    except RuntimeError as e:
        assert "tiktok-auth" in str(e)
    else:
        raise AssertionError("devia falhar")
