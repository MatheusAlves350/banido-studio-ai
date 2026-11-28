"""F3: relatório de problema (.zip). Sem GPU, sem modelos e sem rede."""
import io
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

import app.database as db
import app.problem_report as pr

CHAVE_GOOGLE = "AIzaSyChaveSecretaDeTeste1234567890abcdef"
CHAVE_NVIDIA = "nvapi-ChaveNvidiaDeTeste1234567890"


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    base = tmp_path / "MinhaPastaBase"
    obs = tmp_path / "Gravacoes OBS"
    for d in (base, obs):
        d.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", tmp_path / "t.json")
    monkeypatch.setattr(db, "LOGS_DIR", logs)
    monkeypatch.setattr(pr.config, "LOGS_DIR", logs)
    monkeypatch.setattr(pr.config, "BASE_DIR", base)
    monkeypatch.setattr(pr.config, "OBS_RECORDINGS_DIR", obs)
    db.init_db()
    db.save_setting("google_api_key", CHAVE_GOOGLE)
    db.save_setting("nvidia_api_key", CHAVE_NVIDIA)
    db.save_setting("google_model", "gemini-2.5-flash")
    return {"logs": logs, "base": base, "obs": obs}


def _criar_job_com_segredos(env):
    job = db.create_job_record(video_id=1, filename="sessao_do_grupo.mkv", engine="gemini", model="gemini-2.5-flash")
    Path(job["log_file"]).write_text(
        "[FALA #0001] [00:00:01 -> 00:00:03] [Host]: conversa privada da call\n"
        f"Chave usada: {CHAVE_GOOGLE} e {CHAVE_NVIDIA}\n"
        f"Abrindo {env['obs']}\\sessao_do_grupo.mkv e {env['base']}/database\n"
        "Falha em C:\\Users\\Joaozinho\\Videos\\x.mkv no IP 203.0.113.7\n",
        encoding="utf-8",
    )
    db.finish_job_record(job["job_id"], status="error", last_step="Falha", error_detail=f"erro com {CHAVE_GOOGLE}")
    return job


def _conteudo(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def test_zip_nao_contem_chaves_falas_nomes_nem_caminhos(ambiente):
    _criar_job_com_segredos(ambiente)
    dados, nome = pr.build_problem_zip()
    assert nome.startswith("banido-relatorio-") and nome.endswith(".zip")
    arquivos = _conteudo(dados)
    tudo = "\n".join(arquivos.values())
    for proibido in (CHAVE_GOOGLE, CHAVE_NVIDIA, "AIzaSy", "nvapi-", "conversa privada", "Joaozinho",
                     "203.0.113.7", "sessao_do_grupo", str(ambiente["obs"]), str(ambiente["base"]),
                     "MinhaPastaBase", "Gravacoes OBS"):
        assert proibido not in tudo, proibido


def test_zip_traz_o_conteudo_prometido(ambiente):
    _criar_job_com_segredos(ambiente)
    arquivos = _conteudo(pr.build_problem_zip()[0])
    for esperado in ("LEIA-ME.txt", "ambiente.txt", "diagnostico.txt", "historico_jobs.json", "configuracoes.json"):
        assert esperado in arquivos
    assert any(n.startswith("logs/job_") for n in arquivos)
    assert "<OBS_DIR>" in "\n".join(arquivos.values())
    cfg = arquivos["configuracoes.json"]
    assert '"google_api_key_configurada": true' in cfg and "gemini-2.5-flash" in cfg
    assert "Python:" in arquivos["ambiente.txt"]


def test_inclui_relatorio_de_queda_do_f1_com_caminhos_trocados(ambiente):
    (ambiente["logs"] / "job_9_crash_report.txt").write_text(
        f"Código 0xC0000409 em {ambiente['base']}\\models\n", encoding="utf-8")
    arquivos = _conteudo(pr.build_problem_zip()[0])
    queda = arquivos["queda/job_9_crash_report.txt"]
    assert "0xC0000409" in queda and "<BASE_DIR>" in queda and str(ambiente["base"]) not in queda


def test_logs_antigos_ficam_de_fora_e_o_total_respeita_o_limite(ambiente, monkeypatch):
    velho = ambiente["logs"] / "job_1.log"
    velho.write_text("antigo", encoding="utf-8")
    dois_dias = time.time() - 2 * 24 * 3600
    os.utime(velho, (dois_dias, dois_dias))
    (ambiente["logs"] / "job_2.log").write_text("linha\n" * 600_000, encoding="utf-8")  # ~3,6 MB
    arquivos = pr.build_report_files()
    assert "logs/job_1.log" not in arquivos
    assert len(arquivos["logs/job_2.log"].encode()) <= pr.MAX_LOG_BYTES_TOTAL + 200
    assert "omitido pelo limite" in arquivos["logs/job_2.log"]


def test_varredura_final_bloqueia_vazamento(ambiente, monkeypatch):
    _criar_job_com_segredos(ambiente)
    monkeypatch.setattr(pr, "clean", lambda t: t or "")  # simula uma falha na limpeza
    with pytest.raises(pr.ReportBlocked) as e:
        pr.build_problem_zip()
    msg = str(e.value)
    assert "bloqueado" in msg
    assert CHAVE_GOOGLE not in msg  # a mensagem nunca repete o conteúdo vazado


def test_varredura_pega_chave_salva_e_pasta_conhecida(ambiente):
    achados = pr.scan_for_leaks({"x.txt": f"abc {CHAVE_NVIDIA} def", "y.txt": f"em {ambiente['obs']}"})
    assert any("chave de API salva" in a for a in achados)
    assert any("caminho completo" in a for a in achados)
    assert pr.scan_for_leaks({"ok.txt": "tudo limpo aqui"}) == []


def test_endpoint_do_zip_exige_token_e_baixa_o_arquivo(ambiente):
    from fastapi.testclient import TestClient
    import app.main as m

    _criar_job_com_segredos(ambiente)
    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)
    assert client.get("/api/diagnostics/package").status_code == 403
    r = client.get("/api/diagnostics/package", headers={"X-Banido-Token": token})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert "Joaozinho" not in "\n".join(_conteudo(r.content).values())
    assert datetime.now().strftime("%Y%m%d") in r.headers["content-disposition"]


def test_falas_com_carimbo_de_hora_sao_omitidas(ambiente):
    log_txt = (
        "[15:24:41] [WHISPER] Inicio\n"
        "[15:24:49] [FALA #0001] [00:00:04 -> 00:00:10] [Discord]: conversa privada do grupo\n"
        "[15:24:49] [FALA #0002] [00:00:10 -> 00:00:14] [Discord]: outra fala secreta\n"
        "[15:24:50] [WHISPER] Fim\n"
    )
    limpo = pr.clean(log_txt)
    assert "conversa privada" not in limpo
    assert "outra fala secreta" not in limpo
    assert "2 fala(s) transcrita(s) omitida(s)" in limpo
    assert pr.scan_for_leaks({"logs/job_1.log": log_txt}) != []
    assert pr.scan_for_leaks({"logs/job_1.log": limpo}) == []
