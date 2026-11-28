"""Peças comuns aos provedores: acompanhamento de progresso, erros definitivos e interface LLMProvider."""
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Protocol


def _start_ai_heartbeat(
    tag: str,
    model_name: str,
    t0: float,
    state: Dict[str, Any],
    stop_event: threading.Event,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    interval_sec: float = 15.0
) -> threading.Thread:
    """
    Thread leve de acompanhamento: enquanto a API está processando o prompt (thinking / fila)
    ou se ficar mais de 15s entre pacotes, registra o tempo decorrido nos logs e na barra de progresso
    para que o usuário sempre saiba que o processo está vivo e aguardando resposta.
    """
    def _loop():
        while not stop_event.wait(interval_sec):
            elapsed = time.time() - t0
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"
            json_chars = state.get("json_chars", 0)
            think_chars = state.get("think_chars", 0)
            last_chunk_t = state.get("last_chunk_t", 0.0)

            if json_chars == 0 and think_chars == 0:
                print(
                    f"[{tag}] ⏳ Aguardando primeiro pacote de '{model_name}'... "
                    f"({elapsed_str} decorridos | API processando transcrição/raciocínio)",
                    flush=True
                )
                if progress_callback:
                    pct = min(0.95, 0.92 + (elapsed / 600.0) * 0.03)
                    progress_callback(f"Aguardando resposta da IA ({model_name})... {elapsed_str}", pct)
            elif (time.time() - last_chunk_t) >= interval_sec:
                extra = f" | {think_chars:,} chars raciocínio" if think_chars > 0 else ""
                print(
                    f"[{tag}] ⏳ Stream ativo em '{model_name}'... "
                    f"({elapsed_str} | {json_chars:,} chars JSON{extra})",
                    flush=True
                )
                if progress_callback:
                    pct = min(0.98, 0.94 + min(0.04, json_chars / 20000.0))
                    progress_callback(
                        f"Recebendo resposta da IA ({model_name}): {json_chars:,} chars ({elapsed_str})",
                        pct
                    )

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return th




def _is_non_retryable_ai_error(err: Exception) -> bool:
    """Retorna True para erros definitivos (chave inválida, permissão negada, conta bloqueada)."""
    msg = str(err or "").lower()
    fatal_tokens = (
        "api_key_invalid", "invalid api key", "invalid_api_key",
        "401", "403", "unauthorized", "permission_denied", "permission denied",
        "authentication", "invalid x-api-key", "account suspended", "billing"
    )
    return any(tok in msg for tok in fatal_tokens)


class LLMProvider(Protocol):
    """Contrato comum dos provedores de IA que analisam a transcrição automaticamente."""

    name: str

    def analyze(
        self,
        transcript_text: str,
        mosaic_jpg_path: Optional[Path] = None,
        duration_sec: Optional[float] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        extra_mosaic_paths: Optional[list] = None,
    ) -> Dict[str, Any]:
        ...
