"""Leitura e validação do JSON devolvido pela IA."""
import json
import re
from typing import Dict, Any, Optional
from app.ai.youtube import normalize_ai_data


def parse_ai_json_response(response_text: str, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    if not response_text or not response_text.strip():
        raise ValueError("Resposta vazia recebida da IA.")

    # Remover blocos de raciocínio <think>...</think>
    cleaned = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE).strip()

    match = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", cleaned, re.DOTALL)
    if match:
        raw_json = match.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw_json = cleaned[start:end + 1]
        else:
            raw_json = cleaned

    parsed = json.loads(raw_json)
    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError("O JSON retornado pela IA precisa ser um objeto { ... }.")

    return normalize_ai_data(parsed, duration_sec=duration_sec)


def parse_claude_web_response(claude_response_text: str, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    return parse_ai_json_response(claude_response_text, duration_sec=duration_sec)
