# Arquitetura — Banido Studio AI

## Visão Geral

O sistema separa completamente a camada de interface/API (`app/main.py`) da camada de processamento pesado (`app/worker.py`) para garantir responsividade contínua no painel web mesmo durante transcrições longas em GPU ou remuxing de arquivos 4K de dezenas de gigabytes.

## Fluxo do Pipeline

1. **Ingestão (`database.sync_obs_videos`)**:
   - Monitora `BANIDO_OBS_DIR` em busca de gravações `.mkv` sem apagar registros se o volume estiver temporariamente desmontado.
2. **Extração Segmentada em Paralelo (`audio_pipeline.SegmentedAudioExtractor`)**:
   - Extrai faixas de áudio em blocos de 5 minutos para `BANIDO_TEMP_DIR` com pré-buffer de segurança.
   - Suporta gravações de 6 faixas (Mix, Host, Discord, Jogo, Desktop, Soundpad), 2 faixas (Mix/Discord + Mic) ou 1 faixa (fallback acústico).
3. **Transcrição e Diarização (`transcriber_v3.py` + `voice_bank.py`)**:
   - Whisper `large-v3` com `word_timestamps=True`, quebra de falas longas e detecção de troca rápida de locutor.
   - Checkpoints persistidos em JSON após cada bloco para retomada segura em vídeos longos.
   - Liberação explícita da VRAM do Whisper antes de carregar o modelo SpeechBrain ECAPA-TDNN para identificação dos participantes no Discord.
4. **Visão Multimodal & Direção Criativa (`mosaic_generator.py` + `ai_director.py`)**:
   - Gera mosaico 4x4 (16 frames) para identificação de jogo.
   - Envia transcrição + telemetria de hype + mosaico para Google Gemini, NVIDIA NIM ou pacote Claude Web.
5. **Operações Lossless (`lossless_media.py`)**:
   - Cortes exportados com `ffmpeg -c:v copy`.
   - Capítulos gravados com `mkvpropedit` (ou fallback FFmpeg lossless).
   - Normalização de áudio YouTube Gold (`-15.5 LUFS`) na Faixa 1 preservando integralmente o vídeo e as demais faixas.

## Configuração Multi-Instância

Toda a resolução de caminhos e identidade está centralizada em `app/config.py`, carregando `.env` da raiz do projeto:

- `BANIDO_BASE_DIR`: raiz dos dados da instância (`database/`, `Cortes/`, `vozes_amigos/`, `models/`).
- `BANIDO_OBS_DIR`: pasta de entrada dos `.mkv`.
- `BANIDO_TEMP_DIR`: pasta de temporários (idealmente em SSD NVMe separado do disco das gravações).
- `BANIDO_HOST_NAME`: rótulo usado para o microfone isolado da Faixa 2.
- `BANIDO_PORT`: porta HTTP da instância (permite rodar várias instâncias em paralelo).
