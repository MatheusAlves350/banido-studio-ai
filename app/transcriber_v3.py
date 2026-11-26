"""
Ponto de entrada de compatibilidade da transcrição.

O código foi dividido em app/transcription/ (issue #14):
  - hallucination.py: filtro de alucinações do Whisper
  - srt.py: exportação SRT
  - prompt.py: prompt inicial do Whisper
  - runtime.py: CUDA, memória e modelo Whisper
  - audio_tracks.py: mix, detecção de fala e eco do microfone
  - chunking.py: cortes entre blocos e refinamento
  - pipeline.py: pipeline principal e modo segmentado

Este arquivo reexporta os mesmos nomes de antes, para que worker.py, routers e os testes não mudem.
"""
import os
import re
import gc
import difflib
import json
import time
import shutil
import subprocess
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Tuple
from app.config import (
    MODELS_DIR, HOST_SPEAKER_NAME,
    WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, WHISPER_LANGUAGE,
    SEPARATE_TRACK_TRANSCRIPTION
)
from app.audio_pipeline import (
    is_host_speaking, extract_audio_tracks, analyze_audio_dynamics,
    compute_rms_100ms,
    build_balanced_mix_array, SegmentedAudioExtractor, analyze_segmented_hype
)
from app.voice_bank import load_known_voice_profiles, cluster_and_identify_speakers, list_known_voice_names


from app.transcription.hallucination import (  # noqa: F401
    WHISPER_AVG_LOGPROB_LIMIT,
    WHISPER_COMPRESSION_RATIO_LIMIT,
    WHISPER_HALLUCINATION_PHRASES,
    WHISPER_HALLUCINATION_SILENCE_THRESHOLD,
    WHISPER_HALLUCINATION_SUBSTRINGS,
    WHISPER_NO_SPEECH_PROB_LIMIT,
    _is_whisper_hallucination,
    _normalize_for_hallucination,
)
from app.transcription.srt import (  # noqa: F401
    export_srt_file,
    seconds_to_srt_time,
)
from app.transcription.prompt import (  # noqa: F401
    build_whisper_initial_prompt,
)
from app.transcription.runtime import (  # noqa: F401
    _DLL_DIR_HANDLES,
    _log_memory_status,
    _register_cuda_dll_dirs,
    _whisper_model,
    format_hhmmss,
    get_whisper_model,
    release_whisper_model,
)
from app.transcription.audio_tracks import (  # noqa: F401
    _drop_mic_bleed,
    _mic_segment_is_real,
    _rms_slice,
    _time_overlap,
    _track_has_speech,
    create_balanced_mix_audio,
    merge_track_segments,
)
from app.transcription.chunking import (  # noqa: F401
    _find_chunk_boundaries,
    _find_tail_silence_split,
    _refine_whisper_segment,
)
from app.transcription.pipeline import (  # noqa: F401
    _run_parallel_segmented_pipeline,
    _transcribe_block,
    run_transcription_pipeline,
)
