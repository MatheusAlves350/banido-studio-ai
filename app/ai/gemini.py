"""Provedor Google Gemini."""
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from app.model_catalog import DEFAULT_GOOGLE_MODEL, get_fallback_models
from app.ai.base import _start_ai_heartbeat, _is_non_retryable_ai_error
from app.ai.parsing import parse_ai_json_response, call_with_schema_retry
from app.ai.schema import get_structure_temperature
from app.ai.prompt import SYSTEM_PROMPT

def analyze_with_google_gemini_single(
    client,
    contents,
    safety_settings,
    model_name: str,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    correction_note: Optional[str] = None
) -> Dict[str, Any]:
    from google.genai import types

    t0 = time.time()
    if correction_note:
        contents = list(contents) + [correction_note]
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        safety_settings=safety_settings,
        temperature=get_structure_temperature()
    )

    state: Dict[str, Any] = {"json_chars": 0, "think_chars": 0, "last_chunk_t": 0.0}
    stop_event = threading.Event()
    hb_thread = _start_ai_heartbeat(
        "IA GEMINI", model_name, t0, state, stop_event,
        progress_callback=progress_callback, interval_sec=15.0
    )

    text_parts: List[str] = []
    usage_meta = None
    first_packet_logged = False
    last_progress_log = 0.0

    try:
        try:
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=cfg
            )
            for chunk in stream:
                now_t = time.time()
                if getattr(chunk, "usage_metadata", None) is not None:
                    usage_meta = chunk.usage_metadata

                got_any = False
                candidates = getattr(chunk, "candidates", None) or []
                if candidates and getattr(candidates[0], "content", None) and getattr(candidates[0].content, "parts", None):
                    for part in candidates[0].content.parts:
                        p_txt = getattr(part, "text", None) or ""
                        if not p_txt:
                            continue
                        got_any = True
                        if getattr(part, "thought", False):
                            state["think_chars"] += len(p_txt)
                        else:
                            text_parts.append(p_txt)
                            state["json_chars"] += len(p_txt)
                else:
                    c_txt = ""
                    try:
                        c_txt = chunk.text or ""
                    except Exception:
                        c_txt = ""
                    if c_txt:
                        got_any = True
                        text_parts.append(c_txt)
                        state["json_chars"] += len(c_txt)

                if got_any:
                    state["last_chunk_t"] = now_t
                    if not first_packet_logged:
                        first_packet_logged = True
                        phase_lbl = "raciocínio interno" if (state["think_chars"] > 0 and state["json_chars"] == 0) else "resposta JSON"
                        print(
                            f"[IA GEMINI] ⚡ Primeiro pacote ({phase_lbl}) recebido de '{model_name}' em {now_t - t0:.1f}s! "
                            f"Recebendo em tempo real...",
                            flush=True
                        )
                        last_progress_log = now_t
                        if progress_callback:
                            progress_callback(f"IA ({model_name}) respondendo em tempo real...", 0.94)
                    elif (now_t - last_progress_log) >= 5.0:
                        last_progress_log = now_t
                        extra_think = f" + {state['think_chars']:,} chars raciocínio" if state["think_chars"] > 0 else ""
                        print(
                            f"[IA GEMINI] 📥 Recebendo de '{model_name}': "
                            f"{state['json_chars']:,} chars JSON{extra_think} ({now_t - t0:.0f}s)",
                            flush=True
                        )
                        if progress_callback:
                            pct = min(0.98, 0.94 + min(0.04, state["json_chars"] / 20000.0))
                            progress_callback(
                                f"Recebendo JSON da IA ({model_name}): {state['json_chars']:,} chars...",
                                pct
                            )
            raw_text = "".join(text_parts).strip()
        except AttributeError:
            # SDK sem generate_content_stream: fallback síncrono mantendo heartbeat ativo
            print(f"[IA GEMINI] Streaming indisponível nesta versão do SDK; usando chamada padrão em '{model_name}'...", flush=True)
            response = client.models.generate_content(model=model_name, contents=contents, config=cfg)
            usage_meta = getattr(response, "usage_metadata", None)
            raw_text = (response.text or "").strip()
    finally:
        stop_event.set()
        hb_thread.join(timeout=1.0)

    if not raw_text:
        raise RuntimeError(f"Resposta vazia retornada pelo modelo {model_name}.")

    elapsed = time.time() - t0
    token_info = ""
    if usage_meta is not None:
        p_tok = getattr(usage_meta, "prompt_token_count", None)
        c_tok = getattr(usage_meta, "candidates_token_count", None)
        t_tok = getattr(usage_meta, "thoughts_token_count", None)
        parts_tok = []
        if p_tok is not None:
            parts_tok.append(f"in={p_tok:,}")
        if t_tok:
            parts_tok.append(f"think={t_tok:,}")
        if c_tok is not None:
            parts_tok.append(f"out={c_tok:,}")
        if parts_tok:
            token_info = f" | Tokens: {' / '.join(parts_tok)}"

    print(
        f"[IA GEMINI] ✅ Stream finalizado de '{model_name}' em {elapsed:.1f}s ({len(raw_text):,} chars JSON{token_info}). Validando JSON...",
        flush=True
    )
    data = parse_ai_json_response(raw_text, duration_sec=duration_sec, strict=True)
    print(
        f"[IA GEMINI] ✅ Análise concluída! "
        f"Jogo: {data.get('main_game')} | Capítulos: {len(data.get('chapters', []))} | "
        f"Assuntos: {len(data.get('topics', []))} | Highlights: {len(data.get('highlights', []))}",
        flush=True
    )
    return data




def analyze_with_google_gemini(
    api_key: str,
    transcript_text: str,
    mosaic_jpg_path: Optional[Path] = None,
    model_name: str = DEFAULT_GOOGLE_MODEL,
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    extra_mosaic_paths: Optional[List[Path]] = None
) -> Dict[str, Any]:
    from google import genai
    from google.genai import types
    from PIL import Image

    client = genai.Client(api_key=api_key)

    safety_settings = [
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    ]

    contents = []
    mosaic_info = "sem mosaico"
    if mosaic_jpg_path and mosaic_jpg_path.exists():
        try:
            mosaic_kb = mosaic_jpg_path.stat().st_size / 1024.0
            with Image.open(mosaic_jpg_path) as img:
                img.load()
                contents.append(img.copy())
            contents.append("Aqui está a folha de contato (mosaico 4x4 com 16 cenas e timestamps HH:MM:SS) para você identificar o jogo principal e eventuais trocas de jogo (other_games):")
            mosaic_info = f"mosaico {mosaic_jpg_path.name} ({mosaic_kb:.0f} KB)"
            print(f"[IA GEMINI] Mosaico visual 4x4 anexado ({mosaic_jpg_path.name}, {mosaic_kb:.0f} KB).", flush=True)
        except Exception as e:
            print(f"[IA GEMINI] Aviso: não foi possível carregar mosaico: {e}", flush=True)

    for extra in (extra_mosaic_paths or []):
        try:
            with Image.open(extra) as img:
                img.load()
                contents.append(img.copy())
            contents.append(f"Mosaico adicional ({extra.stem.split('_mosaic_')[-1]}): mesma folha de contato, com os timestamps HH:MM:SS de cada cena. Use para conferir trocas de jogo (other_games).")
        except Exception as e:
            print(f"[IA GEMINI] Aviso: mosaico adicional {extra.name} ignorado: {e}", flush=True)

    if extra_mosaic_paths:
        mosaic_info += f" + {len(extra_mosaic_paths)} adicional(is)"

    contents.append(transcript_text)
    est_tokens = max(1, len(transcript_text) // 4)
    print(
        f"[IA GEMINI] Enviando transcrição ({len(transcript_text):,} chars ≈ {est_tokens:,} tokens | {mosaic_info}) "
        f"para o modelo '{model_name}' (streaming + acompanhamento ao vivo)...",
        flush=True
    )
    if progress_callback:
        progress_callback(f"Enviando transcrição ({est_tokens:,} tokens) para {model_name}...", 0.92)

    try:
        return call_with_schema_retry(
            lambda note: analyze_with_google_gemini_single(
                client, contents, safety_settings, model_name,
                duration_sec=duration_sec, progress_callback=progress_callback,
                correction_note=note
            ),
            tag="IA GEMINI"
        )
    except Exception as e:
        print(f"[IA GEMINI] ❌ Falha no modelo '{model_name}': {e}", flush=True)
        if _is_non_retryable_ai_error(e):
            print("[IA GEMINI] ⛔ Erro definitivo de autenticação/permissão; fallback cancelado imediatamente.", flush=True)
            raise e
        for fb in get_fallback_models("google"):
            if fb != model_name:
                print(f"[IA GEMINI] 🔄 Tentando fallback automático para '{fb}'...", flush=True)
                if progress_callback:
                    progress_callback(f"Tentando fallback automático Gemini: {fb}...", 0.92)
                try:
                    return call_with_schema_retry(
                        lambda note, fb=fb: analyze_with_google_gemini_single(
                            client, contents, safety_settings, fb,
                            duration_sec=duration_sec, progress_callback=progress_callback,
                            correction_note=note
                        ),
                        tag="IA GEMINI"
                    )
                except Exception as fb_err:
                    print(f"[IA GEMINI] ❌ Fallback '{fb}' também falhou: {fb_err}", flush=True)
                    if _is_non_retryable_ai_error(fb_err):
                        raise fb_err
        raise e


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model_name: str = DEFAULT_GOOGLE_MODEL):
        self.api_key = api_key
        self.model_name = model_name

    def analyze(self, transcript_text, mosaic_jpg_path=None, duration_sec=None, progress_callback=None,
                extra_mosaic_paths=None):
        return analyze_with_google_gemini(
            self.api_key, transcript_text, mosaic_jpg_path=mosaic_jpg_path,
            model_name=self.model_name, duration_sec=duration_sec,
            progress_callback=progress_callback, extra_mosaic_paths=extra_mosaic_paths,
        )


def generate_text_gemini(api_key: str, model_name: str, system: str, user: str, temperature: float) -> str:
    """Chamada curta, só texto, sem mosaico (usada para refazer um título). Devolve o texto puro."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
    response = client.models.generate_content(model=model_name, contents=user, config=cfg)
    return (response.text or "").strip()
