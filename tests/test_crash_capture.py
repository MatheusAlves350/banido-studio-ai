"""F1: captura da causa de queda do worker. Sem GPU, sem modelos e sem rede."""
import time
from pathlib import Path

import pytest

import app.crash_capture as cc


@pytest.fixture(autouse=True)
def _sem_faulthandler_real(monkeypatch):
    # O pytest já usa o faulthandler; não mexer nele durante os testes.
    monkeypatch.setattr(cc.faulthandler, "enable", lambda **k: None)
    monkeypatch.setattr(cc.faulthandler, "disable", lambda: None)
    monkeypatch.setattr(cc, "SAMPLE_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(cc, "gpu_memory_mb", lambda: {"used": 6100, "total": 8188})


def test_descreve_codigos_de_saida_do_windows():
    assert cc.describe_exit_code(3221226505).startswith("0xC0000409")
    assert "biblioteca nativa" in cc.describe_exit_code(3221226505)
    assert cc.describe_exit_code(3221225477).startswith("0xC0000005")
    assert cc.describe_exit_code(-9) == "sinal 9 (encerrado pelo sistema)"
    assert cc.describe_exit_code(7) == "7"


def test_caixa_preta_grava_cabecalho_etapa_e_vram(tmp_path):
    cc.start(11, tmp_path)
    cc.set_stage("Whisper bloco 3/10")
    time.sleep(0.3)
    files = cc.crash_files(11, tmp_path)
    assert "Python:" in files["crash"].read_text(encoding="utf-8")
    amostras = files["resources"].read_text(encoding="utf-8")
    assert "VRAM 6100/8188 MB" in amostras
    assert "etapa: Whisper bloco 3/10" in amostras
    assert cc.last_known_stage(11, tmp_path) == "Whisper bloco 3/10"
    cc.stop(success=False)
    assert files["crash"].exists() and files["resources"].exists()  # falha: mantém


def test_so_guarda_os_ultimos_24_registros(tmp_path):
    cc.start(12, tmp_path)
    time.sleep(0.3)
    cc._state["samples"].extend(["x"] * 100)
    assert len(cc._state["samples"]) == cc.KEEP_SAMPLES
    cc.stop(success=True)


def test_sucesso_apaga_os_arquivos(tmp_path):
    cc.start(13, tmp_path)
    time.sleep(0.2)
    cc.stop(success=True)
    assert not list(tmp_path.glob("job_13_*"))


def test_relatorio_de_queda_junta_tudo_e_limpa_os_brutos(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "windows_fault_module", lambda pid: {"module": "cudnn_ops64_9.dll", "exception": "0xc0000409", "offset": "0x1234", "app": "python.exe"})
    cc.start(14, tmp_path)
    cc.set_stage("SpeechBrain")
    time.sleep(0.2)
    cc.stop(success=False)
    stage = cc.last_known_stage(14, tmp_path)
    info = cc.collect_crash_info(14, 3221226505, tmp_path, pid=999, stage=stage)
    texto = Path(info["report_path"]).read_text(encoding="utf-8")
    assert "0xC0000409" in texto and "cudnn_ops64_9.dll" in texto
    assert "Cabeçalho do job" in texto and "VRAM 6100/8188 MB" in texto
    assert "módulo que falhou: cudnn_ops64_9.dll" in info["summary"] and "etapa: SpeechBrain" in info["summary"]
    assert not cc.crash_files(14, tmp_path)["crash"].exists()
    assert not cc.crash_files(14, tmp_path)["resources"].exists()


def test_relatorio_funciona_mesmo_sem_arquivos(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "windows_fault_module", lambda pid: None)
    info = cc.collect_crash_info(15, 3221226505, tmp_path)
    assert "sem registro" in info["report_text"]


XML_EVENTO = """<Events>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error'/></System>
<EventData><Data>python.exe</Data><Data>3.11.9</Data><Data>66</Data><Data>OUTRO.dll</Data><Data>1</Data><Data>1</Data>
<Data>c0000409</Data><Data>0000000000001111</Data><Data>0x1f4</Data><Data>01da</Data></EventData></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Application Error'/></System>
<EventData><Data>python.exe</Data><Data>3.11.9</Data><Data>66</Data><Data>ucrtbase.dll</Data><Data>10.0</Data><Data>1</Data>
<Data>c0000409</Data><Data>000000000007ecce</Data><Data>0x2a3c</Data><Data>01da</Data></EventData></Event>
</Events>"""


def test_le_modulo_que_falhou_do_registro_de_eventos_pelo_pid():
    r = cc.parse_application_error_xml(XML_EVENTO, 0x2A3C)
    assert r["module"] == "ucrtbase.dll" and r["exception"] == "c0000409"
    assert cc.parse_application_error_xml(XML_EVENTO, 1) is None
    assert cc.parse_application_error_xml("", 1) is None


def test_reconcile_registra_a_queda_no_log_do_job(tmp_path, monkeypatch):
    import app.worker_control as wc

    log = tmp_path / "job_20.log"
    log.write_text("inicio\n", encoding="utf-8")
    monkeypatch.setattr(wc, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(wc, "get_job_by_id", lambda jid: {"id": jid, "log_file": str(log), "status": "running"})
    monkeypatch.setattr(cc, "windows_fault_module", lambda pid: None)

    class Proc:
        pid = 1234

        def poll(self):
            return 3221226505

    resumo = wc._record_worker_crash({"job_id": 20, "proc": Proc()}, 3221226505)
    assert "0xC0000409" in resumo
    assert "[QUEDA DO PROCESSO]" in log.read_text(encoding="utf-8")
    assert (tmp_path / "job_20_crash_report.txt").exists()
