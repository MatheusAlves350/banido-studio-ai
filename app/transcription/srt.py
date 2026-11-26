"""Exportação de legendas SRT."""
from pathlib import Path
from typing import List, Dict


def seconds_to_srt_time(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msecs = int(round((seconds - int(seconds)) * 1000))
    if msecs >= 1000:
        msecs = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"


def export_srt_file(segments: List[Dict], srt_path: Path):
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, 1):
            start_str = seconds_to_srt_time(seg["start"])
            end_str = seconds_to_srt_time(seg["end"])
            speaker = seg.get("speaker", "Amigo")
            text = seg["text"].strip()

            f.write(f"{idx}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"[{speaker}]: {text}\n\n")
    print(f"[SRT] Legenda exportada com sucesso ({len(segments)} falas): {srt_path}", flush=True)
