"""
Ponto de entrada de compatibilidade da camada de IA.

O código foi dividido em app/ai/ (issue #14):
  - youtube.py: título, descrição, tags e capítulos
  - prompt.py: prompt do sistema, transcrição formatada e prompt do Claude Web
  - parsing.py: leitura do JSON devolvido pela IA
  - base.py: peças comuns e interface LLMProvider
  - gemini.py / nvidia.py: provedores automáticos

Este arquivo apenas reexporta os mesmos nomes de antes, para que main.py, worker.py
e os testes continuem funcionando sem mudanças.
"""
from app.model_catalog import (  # noqa: F401
    DEFAULT_GOOGLE_MODEL, DEFAULT_NVIDIA_MODEL, get_fallback_models,
)
from app.ai.youtube import (  # noqa: F401
    YT_MAX_DESC_CHARS, YT_MAX_TAGS_CHARS, GENERIC_SPEAKER_LABELS,
    sanitize_video_title, _parse_ts_to_seconds, _seconds_to_hhmmss,
    extract_video_participants, build_youtube_tags_csv, normalize_youtube_chapters,
    normalize_ai_data, build_youtube_description,
)
from app.ai.prompt import (  # noqa: F401
    SYSTEM_PROMPT, format_transcript_for_ai, generate_claude_web_prompt,
)
from app.ai.parsing import parse_ai_json_response, parse_claude_web_response  # noqa: F401
from app.ai.base import (  # noqa: F401
    LLMProvider, _start_ai_heartbeat, _is_non_retryable_ai_error,
)
from app.ai.gemini import (  # noqa: F401
    analyze_with_google_gemini_single, analyze_with_google_gemini, GeminiProvider,
)
from app.ai.nvidia import (  # noqa: F401
    _call_nvidia_single, analyze_with_nvidia_nim, NvidiaProvider,
)
