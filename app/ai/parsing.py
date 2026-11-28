"""Leitura e validação do JSON devolvido pela IA."""
import json
import re
from typing import Dict, Any, Callable, Optional
from app.ai.schema import AISchemaError, build_correction_note, validate_ai_schema
from app.ai.youtube import normalize_ai_data


def parse_ai_json_response(
    response_text: str,
    duration_sec: Optional[float] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Lê o JSON da IA. Com strict=True, estrutura fora do esquema levanta AISchemaError (B7)."""
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

    if strict:
        problems = validate_ai_schema(parsed)
        if problems:
            raise AISchemaError(problems, raw_text=response_text, duration_sec=duration_sec)

    return normalize_ai_data(parsed, duration_sec=duration_sec)


def parse_claude_web_response(claude_response_text: str, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    return parse_ai_json_response(claude_response_text, duration_sec=duration_sec)


def call_with_schema_retry(call: Callable[[Optional[str]], Dict[str, Any]], tag: str = "IA") -> Dict[str, Any]:
    """
    Executa `call(nota_de_correcao)`. Se a resposta vier fora do esquema, pede correção UMA vez
    (a nota descreve os problemas). Se continuar fora, aceita a leitura tolerante de sempre,
    para nunca ficar pior do que antes do B7.
    """
    try:
        return call(None)
    except AISchemaError as first:
        print(f"[{tag}] Resposta fora do formato ({'; '.join(first.problems)}). Pedindo correção uma vez...", flush=True)
        try:
            return call(build_correction_note(first.problems))
        except AISchemaError as second:
            print(f"[{tag}] Ainda fora do formato após a correção; usando a leitura tolerante.", flush=True)
            return parse_ai_json_response(second.raw_text, duration_sec=second.duration_sec)
