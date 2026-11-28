"""Provedor NVIDIA NIM."""
import time
import threading
from typing import Dict, Any, List, Optional, Callable

from app.model_catalog import DEFAULT_NVIDIA_MODEL, get_fallback_models
from app.ai.base import _start_ai_heartbeat, _is_non_retryable_ai_error
from app.ai.parsing import parse_ai_json_response, call_with_schema_retry
from app.ai.schema import get_structure_temperature, TEMPERATURE_REASONING
from app.ai.prompt import SYSTEM_PROMPT

def _call_nvidia_single(
    client,
    messages,
    model_name: str,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    correction_note: Optional[str] = None
) -> Dict[str, Any]:
    t0 = time.time()
    if correction_note:
        messages = list(messages) + [{"role": "user", "content": correction_note}]
    low = model_name.lower()
    is_reasoning_model = any(k in low for k in ("reasoning", "deepseek-r1", "kimi", "step-5", "nemotron"))

    state: Dict[str, Any] = {"json_chars": 0, "think_chars": 0, "last_chunk_t": 0.0}
    stop_event = threading.Event()
    hb_thread = _start_ai_heartbeat(
        "IA NVIDIA", model_name, t0, state, stop_event,
        progress_callback=progress_callback, interval_sec=15.0
    )

    def _invoke(use_json_format: bool, use_stream: bool):
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": TEMPERATURE_REASONING if is_reasoning_model else get_structure_temperature(),
            "max_tokens": 8192 if is_reasoning_model else 4096,
            "stream": use_stream
        }
        if use_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    text = ""
    try:
        try:
            if is_reasoning_model:
                stream = _invoke(use_json_format=False, use_stream=True)
            else:
                try:
                    stream = _invoke(use_json_format=True, use_stream=True)
                except Exception as fmt_err:
                    print(f"[IA NVIDIA] Aviso com response_format JSON ({fmt_err}); tentando modo padrão...", flush=True)
                    stream = _invoke(use_json_format=False, use_stream=True)

            text_parts: List[str] = []
            first_packet_logged = False
            last_progress_log = 0.0

            for chunk in stream:
                now_t = time.time()
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if not delta:
                    continue
                r_txt = getattr(delta, "reasoning_content", None) or ""
                c_txt = getattr(delta, "content", None) or ""
                if not r_txt and not c_txt:
                    continue

                if r_txt:
                    state["think_chars"] += len(r_txt)
                if c_txt:
                    text_parts.append(c_txt)
                    state["json_chars"] += len(c_txt)
                state["last_chunk_t"] = now_t

                if not first_packet_logged:
                    first_packet_logged = True
                    phase_lbl = "raciocínio" if (r_txt and not c_txt) else "resposta JSON"
                    print(
                        f"[IA NVIDIA] ⚡ Primeiro pacote ({phase_lbl}) recebido de '{model_name}' em {now_t - t0:.1f}s! "
                        f"Recebendo em tempo real...",
                        flush=True
                    )
                    last_progress_log = now_t
                    if progress_callback:
                        progress_callback(f"IA NVIDIA ({model_name}) respondendo em tempo real...", 0.94)
                elif (now_t - last_progress_log) >= 5.0:
                    last_progress_log = now_t
                    extra_think = f" + {state['think_chars']:,} chars raciocínio" if state["think_chars"] > 0 else ""
                    print(
                        f"[IA NVIDIA] 📥 Recebendo de '{model_name}': "
                        f"{state['json_chars']:,} chars JSON{extra_think} ({now_t - t0:.0f}s)",
                        flush=True
                    )
                    if progress_callback:
                        pct = min(0.98, 0.94 + min(0.04, state["json_chars"] / 20000.0))
                        progress_callback(
                            f"Recebendo JSON da IA NVIDIA ({model_name}): {state['json_chars']:,} chars...",
                            pct
                        )

            text = "".join(text_parts).strip()
        except Exception as stream_err:
            if state["json_chars"] > 0 or state["think_chars"] > 0:
                raise stream_err
            print(f"[IA NVIDIA] Aviso ao abrir streaming ({stream_err}); tentando chamada padrão...", flush=True)
            response = _invoke(use_json_format=False, use_stream=False)
            text = (response.choices[0].message.content or "").strip()
    finally:
        stop_event.set()
        hb_thread.join(timeout=1.0)

    print(
        f"[IA NVIDIA] ✅ Stream finalizado de '{model_name}' em {time.time() - t0:.1f}s ({len(text):,} chars). Validando JSON...",
        flush=True
    )
    data = parse_ai_json_response(text, duration_sec=duration_sec, strict=True)
    print(
        f"[IA NVIDIA] ✅ Análise concluída! "
        f"Jogo: {data.get('main_game')} | Capítulos: {len(data.get('chapters', []))} | "
        f"Assuntos: {len(data.get('topics', []))} | Highlights: {len(data.get('highlights', []))}",
        flush=True
    )
    return data


def analyze_with_nvidia_nim(
    api_key: str,
    transcript_text: str,
    model_name: str = DEFAULT_NVIDIA_MODEL,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    from openai import OpenAI

    est_tokens = max(1, len(transcript_text) // 4)
    print(
        f"[IA NVIDIA] Enviando transcrição ({len(transcript_text):,} chars ≈ {est_tokens:,} tokens) "
        f"para '{model_name}' (streaming + acompanhamento ao vivo)...",
        flush=True
    )
    if progress_callback:
        progress_callback(f"Enviando transcrição ({est_tokens:,} tokens) para {model_name}...", 0.92)

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
        timeout=300.0
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": transcript_text}
    ]

    try:
        return call_with_schema_retry(
            lambda note: _call_nvidia_single(
                client, messages, model_name,
                duration_sec=duration_sec, progress_callback=progress_callback,
                correction_note=note
            ),
            tag="IA NVIDIA"
        )
    except Exception as e:
        print(f"[IA NVIDIA] ❌ Falha no modelo '{model_name}': {e}", flush=True)
        if _is_non_retryable_ai_error(e):
            print("[IA NVIDIA] ⛔ Erro definitivo de autenticação/permissão; fallback cancelado imediatamente.", flush=True)
            raise e
        for fb in get_fallback_models("nvidia"):
            if fb != model_name:
                print(f"[IA NVIDIA] 🔄 Tentando fallback automático para '{fb}'...", flush=True)
                if progress_callback:
                    progress_callback(f"Tentando fallback automático NVIDIA: {fb}...", 0.92)
                try:
                    return call_with_schema_retry(
                        lambda note, fb=fb: _call_nvidia_single(
                            client, messages, fb,
                            duration_sec=duration_sec, progress_callback=progress_callback,
                            correction_note=note
                        ),
                        tag="IA NVIDIA"
                    )
                except Exception as fb_err:
                    print(f"[IA NVIDIA] ❌ Fallback '{fb}' também falhou: {fb_err}", flush=True)
                    if _is_non_retryable_ai_error(fb_err):
                        raise fb_err
        raise e


class NvidiaProvider:
    name = "nvidia"

    def __init__(self, api_key: str, model_name: str = DEFAULT_NVIDIA_MODEL):
        self.api_key = api_key
        self.model_name = model_name

    def analyze(self, transcript_text, mosaic_jpg_path=None, duration_sec=None, progress_callback=None,
                extra_mosaic_paths=None):
        # A NVIDIA NIM recebe só texto: a imagem (mosaico) é ignorada, como antes.
        return analyze_with_nvidia_nim(
            self.api_key, transcript_text, model_name=self.model_name,
            duration_sec=duration_sec, progress_callback=progress_callback,
        )


def generate_text_nvidia(api_key: str, model_name: str, system: str, user: str, temperature: float) -> str:
    """Chamada curta, só texto (usada para refazer um título). Devolve o texto puro."""
    from openai import OpenAI

    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key, timeout=120.0)
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=512,
    )
    return (response.choices[0].message.content or "").strip()
