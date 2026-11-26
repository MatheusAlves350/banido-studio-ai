"""Sugestão de miniatura (B6): 3 a 5 quadros nítidos nos picos de hype, em 1280x720.

Analogia: a capa do livro é o que faz a pessoa parar na prateleira. Aqui o programa folheia os
melhores momentos e separa as capas mais nítidas, sem tela de carregamento, menu ou imagem escura.
"""
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageFilter, ImageStat

from app.ai.youtube import _seconds_to_hhmmss
from app.mosaic_generator import extract_frames

THUMB_W, THUMB_H = 1280, 720
CANDIDATES = 10
MIN_PICK, MAX_PICK = 3, 5
MIN_GAP_SEC = 90.0
HYPE_PEAK = 75
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 28.0, 232.0   # abaixo/acima disso: escuro demais ou estourado
MIN_CONTRAST = 22.0                              # desvio padrão da luminosidade; tela lisa (carregamento/menu)
MIN_SHARPNESS = 8.0                              # variância das bordas; abaixo disso o quadro está borrado

MAX_WORDS_TEXT = 4


def candidate_times(ai_data: Dict[str, Any], duration: float, limit: int = CANDIDATES) -> List[float]:
    """Instantes dos maiores picos de hype (afastados entre si); sem telemetria, usa os melhores momentos."""
    picks: List[float] = []
    gap = min(MIN_GAP_SEC, max(5.0, duration / 12.0))  # vídeos curtos aceitam candidatos mais próximos

    def _add(t: float) -> None:
        if 0 < t < duration and all(abs(t - p) >= gap for p in picks):
            picks.append(t)

    peaks = sorted(
        (h for h in (ai_data.get("hype_scores") or []) if h.get("score", 0) >= HYPE_PEAK),
        key=lambda h: h["score"], reverse=True,
    )
    for h in peaks:
        if len(picks) >= limit:
            break
        _add((float(h["start"]) + float(h["end"])) / 2)

    if len(picks) < MIN_PICK + 2:
        for hl in sorted(ai_data.get("highlights") or [], key=lambda x: x.get("score", 0), reverse=True):
            if len(picks) >= limit:
                break
            _add((float(hl["start"]) + float(hl["end"])) / 2)

    if len(picks) < MIN_PICK + 2 and duration > 0:
        for frac in (0.2, 0.35, 0.5, 0.65, 0.8):
            _add(duration * frac)
    return sorted(picks)


def frame_quality(img: Image.Image) -> Dict[str, float]:
    """Luminosidade média, contraste e nitidez simples (variância das bordas) de um quadro."""
    gray = img.convert("L").resize((320, 180))
    stat = ImageStat.Stat(gray)
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    return {"brightness": stat.mean[0], "contrast": stat.stddev[0], "sharpness": edges.var[0]}


def is_usable(q: Dict[str, float]) -> bool:
    return (
        MIN_BRIGHTNESS <= q["brightness"] <= MAX_BRIGHTNESS
        and q["contrast"] >= MIN_CONTRAST
        and q["sharpness"] >= MIN_SHARPNESS
    )


def pick_best(scored: List[Tuple[float, Dict[str, float]]], n: int = MAX_PICK) -> List[int]:
    """Índices dos até `n` melhores quadros utilizáveis (mais nítidos primeiro). Se poucos servirem, completa
    com os mais nítidos entre os demais, até no mínimo MIN_PICK, para nunca devolver lista vazia."""
    order = sorted(range(len(scored)), key=lambda i: scored[i][1]["sharpness"], reverse=True)
    good = [i for i in order if is_usable(scored[i][1])]
    chosen = good[:n]
    if len(chosen) < MIN_PICK:
        chosen += [i for i in order if i not in chosen][: MIN_PICK - len(chosen)]
    return chosen


def clean_thumbnail_text(raw: str) -> str:
    """Até 4 palavras, sem colchetes, aspas ou ponto final."""
    txt = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    txt = txt.replace("[", "").replace("]", "").strip().strip("\"'“”-•*0123456789. ").strip()
    words = txt.split()
    return " ".join(words[:MAX_WORDS_TEXT]).rstrip(".!,;:")


def suggest_thumbnail_texts(ai_data: Dict[str, Any], generate) -> List[str]:
    """Dois textos curtos de chamada. `generate(system, user, temperatura)` faz a chamada à IA."""
    from app.ai.schema import TEMPERATURE_CREATIVE
    game = str(ai_data.get("main_game") or "").strip()
    summary = str(ai_data.get("session_summary") or "").strip()
    system = (
        "Você cria textos curtos para miniaturas de YouTube de um canal de gameplay entre amigos, em português do Brasil. "
        f"Cada texto tem NO MÁXIMO {MAX_WORDS_TEXT} palavras, sem colchetes e sem emojis."
    )
    user = (
        f"Jogo: {game}\nResumo: {summary}\n"
        "Escreva 2 textos de chamada diferentes, um por linha, sem numeração e sem explicação."
    )
    lines = [ln for ln in (generate(system, user, TEMPERATURE_CREATIVE) or "").splitlines() if ln.strip()]
    texts: List[str] = []
    for ln in lines:
        t = clean_thumbnail_text(ln)
        if t and t.lower() not in [x.lower() for x in texts]:
            texts.append(t)
    return texts[:2]


def generate_thumbnails(
    video_path: Path, out_dir: Path, video_id: int, ai_data: Dict[str, Any], duration: float
) -> List[Dict[str, Any]]:
    """Extrai candidatos em 1280x720, descarta os ruins e salva os melhores como {id}_thumb_N.jpg."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{video_id}_thumb_*.jpg"):
        old.unlink(missing_ok=True)

    times = candidate_times(ai_data, duration)
    if not times:
        return []
    work = out_dir / f"temp_thumb_{video_id}"
    try:
        work.mkdir(parents=True, exist_ok=True)
        frames, _ = extract_frames(video_path, times, work, THUMB_W, THUMB_H)
        valid: List[Tuple[float, Path, Dict[str, float]]] = []
        for t, f in zip(times, frames or []):
            if f is not None and Path(f).exists():
                with Image.open(f) as im:
                    valid.append((t, Path(f), frame_quality(im)))
        if not valid:
            return []
        chosen = pick_best([(t, q) for t, _, q in valid])
        results = []
        for n, idx in enumerate(sorted(chosen, key=lambda i: valid[i][0]), start=1):
            t, path, q = valid[idx]
            dest = out_dir / f"{video_id}_thumb_{n}.jpg"
            with Image.open(path) as im:
                im.convert("RGB").save(dest, quality=92)
            results.append({
                "name": dest.name, "url": f"/mosaics/{dest.name}", "time": round(t, 1),
                "time_label": _seconds_to_hhmmss(t), "sharpness": round(q["sharpness"], 1),
            })
        return results
    finally:
        shutil.rmtree(work, ignore_errors=True)
