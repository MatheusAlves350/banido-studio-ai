"""IA local opcional via Ollama (B11). Roda no PC, sem chave e sem enviar a transcrição para a nuvem.

Analogia: cozinhar em casa é mais privado e não tem custo por refeição, mas o chef do restaurante
(Gemini/NVIDIA) costuma cozinhar melhor. A nuvem continua sendo o padrão; esta é uma opção.

Pontos de cuidado (GPU de 8 GB):
- Whisper e SpeechBrain já foram liberados da VRAM antes desta etapa; ainda assim chamamos uma limpeza extra.
- O Ollama descarrega o modelo ao terminar (`keep_alive: 0`), para a VRAM voltar ao próximo vídeo.
- O contexto padrão do Ollama (2.048 tokens) cortaria a transcrição; calculamos `num_ctx` pelo tamanho dela.
"""
import gc
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.ai.base import _start_ai_heartbeat
from app.ai.parsing import call_with_schema_retry, parse_ai_json_response
from app.ai.prompt import SYSTEM_PROMPT
from app.ai.schema import get_structure_temperature
from app.ai.tokens import estimate_tokens

DEFAULT_URL = "http://127.0.0.1:11434"
OUTPUT_RESERVE = 4096
MIN_CTX = 8192
DEFAULT_CTX_MAX = 32768
CHAT_TIMEOUT_SEC = 3600.0   # modelo local em GPU de 8 GB pode levar muitos minutos


def get_base_url() -> str:
    url = os.getenv("BANIDO_OLLAMA_URL", "").strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else DEFAULT_URL


def get_ctx_max() -> int:
    try:
        value = int(os.getenv("BANIDO_OLLAMA_CTX_MAX", "").strip())
        if 4096 <= value <= 262144:
            return value
    except ValueError:
        pass
    return DEFAULT_CTX_MAX


def free_gpu_memory() -> None:
    """Limpeza extra antes de pedir VRAM ao Ollama. Sem PyTorch ou GPU, não faz nada."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def ollama_status(timeout: float = 2.0) -> Dict[str, Any]:
    """Ollama está aberto? Quais modelos já foram baixados? Nunca levanta erro."""
    base = get_base_url()
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = sorted(str(m.get("name")) for m in data.get("models", []) if m.get("name"))
        return {"running": True, "url": base, "models": [{"value": n, "label": n} for n in names]}
    except Exception as e:
        return {"running": False, "url": base, "models": [], "error": str(e)}


def compute_num_ctx(transcript_text: str) -> int:
    """Tamanho de contexto pedido ao Ollama: transcrição + instruções + espaço da resposta, em múltiplos de 1024."""
    needed = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(transcript_text) + OUTPUT_RESERVE
    ctx = max(MIN_CTX, -(-needed // 1024) * 1024)
    limit = get_ctx_max()
    if ctx > limit:
        raise RuntimeError(
            f"A transcrição (cerca de {needed:,} tokens) é grande demais para a IA local (limite configurado: {limit:,}). "
            "Use Gemini ou NVIDIA para esta sessão."
        )
    return ctx


def _chat(base: str, payload: Dict[str, Any]) -> str:
    req = urllib.request.Request(
        f"{base}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 404:
            raise RuntimeError(f"O modelo '{payload['model']}' não está baixado no Ollama. Baixe com: ollama pull {payload['model']}")
        raise RuntimeError(f"Ollama respondeu erro {e.code}: {body[:300]}")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise RuntimeError(f"Não consegui falar com o Ollama em {base}. Confira se ele está instalado e aberto. ({e})")
    return str((data.get("message") or {}).get("content") or "").strip()


def analyze_with_ollama(
    transcript_text: str,
    model_name: str,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    if not model_name:
        raise RuntimeError("Escolha um modelo do Ollama nas opções avançadas.")
    free_gpu_memory()
    base = get_base_url()
    num_ctx = compute_num_ctx(transcript_text)
    base_messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": transcript_text},
    ]
    print(f"[IA OLLAMA] Enviando ≈ {estimate_tokens(transcript_text):,} tokens para '{model_name}' (contexto {num_ctx:,}) em {base}...", flush=True)
    if progress_callback:
        progress_callback(f"Enviando transcrição para a IA local ({model_name})...", 0.92)

    def _single(correction_note: Optional[str]) -> Dict[str, Any]:
        messages = list(base_messages)
        if correction_note:
            messages.append({"role": "user", "content": correction_note})
        payload = {
            "model": model_name, "messages": messages, "stream": False, "format": "json",
            "keep_alive": 0,  # descarrega o modelo ao terminar, devolvendo a VRAM
            "options": {"temperature": get_structure_temperature(), "num_ctx": num_ctx},
        }
        t0 = time.time()
        state: Dict[str, Any] = {"json_chars": 0, "think_chars": 0, "last_chunk_t": 0.0}
        stop = threading.Event()
        hb = _start_ai_heartbeat("IA OLLAMA", model_name, t0, state, stop, progress_callback=progress_callback, interval_sec=15.0)
        try:
            text = _chat(base, payload)
        finally:
            stop.set()
            hb.join(timeout=1.0)
        print(f"[IA OLLAMA] Resposta de '{model_name}' em {time.time() - t0:.1f}s ({len(text):,} chars). Validando JSON...", flush=True)
        return parse_ai_json_response(text, duration_sec=duration_sec, strict=True)

    return call_with_schema_retry(_single, tag="IA OLLAMA")


class OllamaProvider:
    name = "ollama"

    def __init__(self, model_name: str):
        self.model_name = model_name

    def analyze(self, transcript_text, mosaic_jpg_path: Optional[Path] = None, duration_sec=None,
                progress_callback=None, extra_mosaic_paths=None):
        # Modelo local recebe só texto: o mosaico é ignorado, como na NVIDIA.
        return analyze_with_ollama(
            transcript_text, self.model_name, duration_sec=duration_sec, progress_callback=progress_callback,
        )
