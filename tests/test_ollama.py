"""B11: IA local opcional (Ollama). Usa um servidor falso na própria máquina; sem rede e sem Ollama real."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai import ollama as ol

JSON_OK = {
    "main_game": "GTA V", "session_summary": "Noite boa.", "title_options": ["GTA V - A"],
    "chapters": [{"timestamp": "00:00:00", "title": "Início"}], "topics": [], "highlights": [], "speaker_mapping": {},
}


class _Fake:
    """Servidor falso: guarda os pedidos e devolve respostas programadas."""
    def __init__(self, replies, tags=None, chat_status=200):
        self.requests = []
        self.replies = list(replies)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"models": [{"name": n} for n in (tags or [])]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                fake.requests.append(json.loads(self.rfile.read(n)))
                self.send_response(chat_status)
                self.end_headers()
                content = fake.replies.pop(0) if fake.replies else "{}"
                self.wfile.write(json.dumps({"message": {"content": content}}).encode())

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake(monkeypatch):
    made = []

    def make(*a, **k):
        f = _Fake(*a, **k)
        made.append(f)
        monkeypatch.setenv("BANIDO_OLLAMA_URL", f.url)
        return f

    yield make
    for f in made:
        f.close()


def test_status_com_modelos_e_sem_ollama(fake, monkeypatch):
    fake(["{}"], tags=["qwen2.5:7b", "llama3.1:8b"])
    st = ol.ollama_status()
    assert st["running"] and [m["value"] for m in st["models"]] == ["llama3.1:8b", "qwen2.5:7b"]
    monkeypatch.setenv("BANIDO_OLLAMA_URL", "http://127.0.0.1:1")
    off = ol.ollama_status(timeout=0.5)
    assert off["running"] is False and off["models"] == []


def test_analise_envia_contexto_temperatura_e_descarrega_modelo(fake):
    f = fake([json.dumps(JSON_OK)])
    out = ol.OllamaProvider("qwen2.5:7b").analyze("[00:00:01] [Host]: oi", duration_sec=100.0)
    assert out["main_game"] == "GTA V"
    req = f.requests[0]
    assert req["model"] == "qwen2.5:7b" and req["stream"] is False and req["format"] == "json"
    assert req["keep_alive"] == 0 and req["options"]["temperature"] == 0.2
    assert req["options"]["num_ctx"] >= 8192 and req["options"]["num_ctx"] % 1024 == 0
    assert req["messages"][1]["content"].startswith("[00:00:01]")


def test_resposta_fora_do_esquema_pede_correcao_uma_vez(fake):
    f = fake([json.dumps({"main_game": "X"}), json.dumps(JSON_OK)])
    out = ol.analyze_with_ollama("texto", "m", duration_sec=100.0)
    assert out["main_game"] == "GTA V" and len(f.requests) == 2
    assert "formato exigido" in f.requests[1]["messages"][-1]["content"]


def test_transcricao_grande_demais_para_o_limite(monkeypatch):
    monkeypatch.setenv("BANIDO_OLLAMA_CTX_MAX", "8192")
    with pytest.raises(RuntimeError, match="grande demais"):
        ol.compute_num_ctx("a" * 4 * 20000)
    monkeypatch.delenv("BANIDO_OLLAMA_CTX_MAX")
    assert ol.compute_num_ctx("a" * 400) == 8192


def test_modelo_nao_baixado_da_mensagem_clara(fake):
    fake(["{}"], chat_status=404)
    with pytest.raises(RuntimeError, match="ollama pull"):
        ol.analyze_with_ollama("texto", "modelo-que-nao-existe")


def test_sem_modelo_e_ollama_fechado(monkeypatch):
    with pytest.raises(RuntimeError, match="Escolha um modelo"):
        ol.analyze_with_ollama("texto", "")
    monkeypatch.setenv("BANIDO_OLLAMA_URL", "http://127.0.0.1:1")
    with pytest.raises(RuntimeError, match="Ollama"):
        ol.analyze_with_ollama("texto", "m")


def test_url_invalida_cai_no_padrao(monkeypatch):
    monkeypatch.setenv("BANIDO_OLLAMA_URL", "ftp://x")
    assert ol.get_base_url() == ol.DEFAULT_URL
