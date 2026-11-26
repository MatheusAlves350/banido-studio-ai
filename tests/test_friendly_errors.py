"""Erros em linguagem simples (item E6): cada erro conhecido gera a mensagem esperada e o original é mantido."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import friendly_errors
from app.friendly_errors import explain, friendly_text, REGRAS
from app.main import app, API_AUTH_TOKEN
from app.database import update_video_progress


EXEMPLOS = {
    "sem_memoria_gpu": "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
    "sem_espaco_disco": "OSError: [Errno 28] No space left on device",
    "ffmpeg_ausente": "FileNotFoundError: ffmpeg não encontrado no PATH",
    "mkvtoolnix_ausente": "mkvpropedit não encontrado nos caminhos padrão",
    "porta_ocupada": "OSError: [WinError 10048] Only one usage of each socket address",
    "arquivo_em_uso": "PermissionError: [WinError 32] O arquivo está sendo usado por outro processo",
    "video_ausente": "Arquivo MKV não encontrado no disco: E:/OBS/a.mkv",
    "video_fora_do_banco": "Vídeo #7 não encontrado no banco de dados.",
    "sem_audio": "O vídeo 'a.mkv' não possui nenhuma faixa de áudio.",
    "sem_transcricao": "Este vídeo ainda não possui transcrição salva. Execute o Processamento Completo primeiro.",
    "chave_ia_invalida": "Falha na etapa da IA Gemini (x): 400 API key not valid. Please pass a valid API key.",
    "chave_ia_ausente": "Chave da API NVIDIA NIM não configurada.",
    "limite_ia": "Falha na etapa da IA Gemini (x): 429 RESOURCE_EXHAUSTED",
    "rede": "httpx.ConnectError: getaddrinfo failed",
    "modelo_nao_baixou": "huggingface_hub.errors.LocalEntryNotFoundError: cached snapshot",
    "resposta_ia_invalida": "Resposta vazia recebida da IA.",
    "falha_ffmpeg": "FFmpeg falhou ao extrair faixas (código 1): Invalid data",
    "worker_caiu": "Processo worker encerrou abruptamente (código 3221226505). Verifique o log.",
    "cancelado": "Cancelado pelo usuário",
}


def test_todo_codigo_da_tabela_tem_exemplo_de_teste():
    # Quem acrescentar uma regra precisa acrescentar o exemplo aqui, senão este teste avisa.
    assert {r[0] for r in REGRAS} == set(EXEMPLOS)


@pytest.mark.parametrize("codigo,texto", EXEMPLOS.items())
def test_cada_erro_conhecido_gera_a_mensagem_esperada(codigo, texto):
    info = explain(texto)
    assert info["known"] is True
    assert info["code"] == codigo
    assert info["what"] and info["todo"]
    assert "{" not in info["what"] + info["todo"]


def test_erro_original_continua_disponivel_sem_o_prefixo_do_programa():
    info = explain("Erro: CUDA out of memory em bloco 3")
    assert info["technical"] == "CUDA out of memory em bloco 3"
    info = explain("Aviso (transcrição e .SRT preservados!): Erro: RuntimeError boom")
    assert info["technical"] == "RuntimeError boom"


def test_erro_desconhecido_cai_na_mensagem_generica_com_relatorio():
    info = explain("falha estranha xyz")
    assert info["known"] is False and info["code"] == "desconhecido"
    assert info["report"] is True
    assert info["technical"] == "falha estranha xyz"
    assert explain("")["known"] is False and explain(None)["technical"] == ""


def test_mensagens_de_disco_e_porta_trazem_os_valores():
    disco = explain("No space left on device")
    assert str(friendly_errors.TEMP_DIR) in disco["todo"]
    assert str(friendly_errors.SERVER_PORT) in explain("address already in use")["what"]


def test_codigos_http_so_valem_perto_de_palavras_de_erro():
    assert explain("Extraídos 4290 quadros")["known"] is False
    assert explain("HTTP 429 Too Many Requests")["code"] == "limite_ia"


def test_friendly_text_junta_o_que_houve_e_o_que_fazer():
    txt = friendly_text("CUDA out of memory")
    assert txt.startswith("A placa de vídeo ficou sem memória.")


def test_detalhes_do_video_com_erro_trazem_a_explicacao(tmp_path):
    from app.database import get_connection
    client = TestClient(app, headers={"X-Banido-Token": API_AUTH_TOKEN})
    nome = f"e6_{uuid.uuid4().hex[:8]}.mkv"  # nome novo a cada execução: o banco de teste persiste
    conn = get_connection()
    conn.execute(
        "INSERT INTO videos (filename, file_path, status) VALUES (?, ?, 'unprocessed')",
        (nome, str(tmp_path / nome)),
    )
    conn.commit()
    vid = conn.execute("SELECT id FROM videos WHERE filename=?", (nome,)).fetchone()[0]
    conn.close()
    update_video_progress(vid, "Erro: CUDA out of memory", 0.0, "error")
    data = client.get(f"/api/video/{vid}?include_data=false").json()
    assert data["error_info"]["code"] == "sem_memoria_gpu"
    assert data["error_info"]["technical"] == "CUDA out of memory"
