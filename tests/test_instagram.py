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
