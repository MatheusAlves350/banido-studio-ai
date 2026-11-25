import re


def test_sanitize_log_text_redacts_secrets_paths_ips_and_transcripts():
    from app.diagnostics import sanitize_log_text

    raw = "\n".join([
        "GOOGLE_API_KEY=AIzaSyDummyKey1234567890abcdefghijk",
        "NVIDIA_API_KEY=nvapi-1234567890abcdef",
        "Authorization: Bearer super_secret_token_99999",
        r"Abrindo C:\Users\Matheus\Videos\2026-07-26 18-32-49.mkv",
        "Log em /home/fulano/banido/data/log.txt no IP 192.168.1.42 (local 127.0.0.1)",
        "[FALA #0001] [00:00:05 -> 00:00:08] [Host]: fala privada da call",
        "[FALA #0002] [00:00:09 -> 00:00:12] [Discord]: outra fala privada",
        "Traceback: RuntimeError: falha simulada",
    ])

    clean = sanitize_log_text(raw, strip_transcript_lines=True, redact_filenames=True)
    assert "AIzaSy" not in clean
    assert "nvapi-" not in clean
    assert "super_secret_token" not in clean
    assert "Matheus" not in clean
    assert "fulano" not in clean
    assert "192.168.1.42" not in clean
    assert "127.0.0.1" in clean
    assert "fala privada" not in clean
    assert "2 fala(s) transcrita(s) omitida(s)" in clean
    assert "2026-07-26 18-32-49.mkv" not in clean
    assert "<ARQUIVO.mkv>" in clean
    assert "RuntimeError: falha simulada" in clean


def test_diagnostics_endpoint_requires_token_and_returns_sanitized_report(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.database as db
    import app.main as m

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "diag.db")
    monkeypatch.setattr(db, "SETTINGS_JSON_PATH", tmp_path / "diag.json")
    monkeypatch.setattr(db, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    db.init_db()

    from pathlib import Path
    job = db.create_job_record(video_id=1, filename="sessao_teste.mkv", engine="gemini", model="gemini-2.5-flash")
    Path(job["log_file"]).write_text(
        "[FALA #0001] [00:00:01 -> 00:00:03] [Host]: segredo da call\n"
        "Erro com chave AIzaSySecretKey1234567890abcdefghij em C:\\Users\\Joao\\test.mkv\n",
        encoding="utf-8",
    )
    db.finish_job_record(
        job["job_id"],
        status="error",
        last_step="Falha na IA",
        error_detail="Falha com nvapi-abcdef1234567890",
    )

    client = TestClient(m.app)
    token = re.search(r'<meta name="banido-token" content="([^"]+)">', client.get("/").text).group(1)

    assert client.get("/api/diagnostics/report").status_code == 403
    r = client.get(
        f"/api/diagnostics/report?job_id={job['job_id']}&strip_transcripts=true&redact_filenames=true",
        headers={"X-Banido-Token": token},
    )
    assert r.status_code == 200
    data = r.json()
    md = data["report_markdown"]
    assert "AIzaSy" not in md
    assert "nvapi-" not in md
    assert "Joao" not in md
    assert "segredo da call" not in md
    assert "sessao_teste.mkv" not in md
    assert data["github_issue_url"].startswith("https://github.com/")
    assert "privacy_notice" in data
