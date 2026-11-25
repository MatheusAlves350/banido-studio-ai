# 🎮 Banido Studio AI

Pipeline automatizado de altíssima fidelidade para processar gravações do **OBS Studio** (multi-track até **4K 60fps AV1**), transcrever diálogos com precisão, identificar vozes por biometria acústica, detectar jogos visualmente, gerar títulos/capítulos/descrições prontos para o **YouTube** e exportar cortes **100% lossless**.

---

## ⚡ Principais Recursos

- **Transcrição Ultra-Fiel (`faster-whisper`)**:
  - Usa **Whisper `large-v3`** (configurável para outros modelos/quantizações via `.env`) com `Beam Size 5`, `word_timestamps=True`, filtro anti-alucinação e suporte automático a **CUDA (float16)** com fallback para **CPU (int8)**.
  - **Pipeline Paralelo em Blocos**: extração segmentada de 5 minutos com pré-buffer e divisão em fronteira de silêncio + checkpoints incrementais em JSON (retoma vídeos longos de onde parou sem perder progresso).
- **Diarização Híbrida (Física + Biometria Vocal)**:
  - **Anfitrião (`BANIDO_HOST_NAME`)**: isolado com precisão matemática pelo diferencial de energia sonora entre a **Faixa 2 (Microfone)** e a **Faixa 3 (Discord)** em gravações multi-track, com fallback automático para gravações de 1 ou 2 faixas.
  - **Amigos / Participantes**: agrupamento acústico via **SpeechBrain ECAPA-TDNN** com Banco de Vozes evolutivo (`vozes_amigos/<Nome>/`) e player de preview direto no navegador antes de confirmar nomes e salvar amostras `.wav`.
- **Visão Multimodal de Sessão**:
  - Gera folha de contato **4x4 (16 frames com timestamp)** acelerada via NVDEC CUDA (fallback CPU) para identificar o jogo principal e trocas de jogo na sessão.
- **Diretor Criativo Multi-IA**:
  - **Google Gemini** (`google-genai`) com suporte a streaming, heartbeat em tempo real e fallback automático entre modelos.
  - **NVIDIA NIM** (endpoint compatível com OpenAI em `integrate.api.nvidia.com`) para modelos abertos hospedados na nuvem.
  - **Claude Web (1-Clique)**: copia o prompt estruturado para colar no `claude.ai` e importa a resposta JSON de volta no painel.
- **Operações 100% Lossless (Zero Re-renderização de Vídeo)**:
  - **Normalização YouTube Gold (`-15.5 LUFS`)**: pode ser executada de forma isolada, em lote **ou em Passada Única (`Single-Pass`) em paralelo com o Whisper** ao processar um vídeo (sem precisar reler nem reprocessar o MKV no final!). Reequilibra a Faixa 1 mantendo o vídeo 4K AV1 e todas as faixas auxiliares (2..N) intactas via stream copy (`-c:v copy`), com validação via `ffprobe` antes da substituição atômica.
  - **Cortes Instantâneos**: exportação de highlights ou trechos manuais em segundos via `ffmpeg -c:v copy`, com opção de manter todas as faixas de áudio para edição posterior e proteção contra sobrescrita (`(2)`, `(3)`).
  - **Capítulos no `.mkv`**: gravação instantânea in-place com `mkvpropedit` (MKVToolNix) ou fallback lossless via FFmpeg.
  - **Renomeação Segura**: renomeia simultaneamente `.mkv` e `.srt` no padrão `DATA - JOGO - TÍTULO`, sem sobrescrever arquivos existentes.
- **Arquitetura Desacoplada, Segura & Multi-Instância**:
  - Servidor **FastAPI** leve (`<5ms` de latência) sem PyTorch/SpeechBrain na memória web + subprocessos dedicados (`app/worker.py` e coleta vocal isolada em `app/voice_bank.py`) com histórico de jobs e logs coloridos ao vivo.
  - Proteção contra CSRF local (`X-Banido-Token` gerado por sessão), validação de `Host` (`TrustedHostMiddleware`), mascaramento de chaves de API no `GET /api/settings`, travas de concorrência (`409 Conflict`) e sincronização com remoção suave/reassociação automática de arquivos renomeados fora do app.
  - 100% configurável via `.env` para rodar múltiplas instâncias, discos separados (HDD + SSD) e diferentes criadores.

---

## 📋 Pré-requisitos

1. **Python 3.10+** (testado em Python 3.10 / 3.11)
2. **FFmpeg** e **FFprobe** disponíveis no `PATH` do sistema:
   - **Windows**: `winget install Gyan.FFmpeg`
   - **Ubuntu / Debian**: `sudo apt install ffmpeg`
   - **macOS**: `brew install ffmpeg`
3. **Opcional (Recomendado)**:
   - **GPU NVIDIA com CUDA** (para transcrição rápida com `faster-whisper` + `SpeechBrain` e frames via NVDEC)
   - **MKVToolNix (`mkvpropedit`)** para gravar capítulos diretamente no `.mkv` em `<0.5s`:
     - **Windows**: `winget install MoritzBunkus.MKVToolNix`
     - **Linux**: `sudo apt install mkvtoolnix`
     - **macOS**: `brew install mkvtoolnix`

---

## 🚀 Instalação e Execução Rápida

### 1. Clonar o repositório
```bash
git clone https://github.com/MatheusAlves350/banido-studio-ai.git
cd banido-studio-ai
```

### 2. Criar ambiente virtual e instalar dependências

#### Windows (PowerShell ou CMD)
```cmd
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip

:: Se tiver GPU NVIDIA (CUDA 12.1), instale PyTorch com CUDA antes:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

pip install -r requirements.txt
```

#### Linux / macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# Opcional (Linux com GPU NVIDIA CUDA 12.1):
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

pip install -r requirements.txt
```

### 3. Configurar instância (`.env`)
Copie o modelo [.env.example](.env.example) para `.env`:
```bash
cp .env.example .env
```
Se você **não** criar `.env`, tudo funcionará imediatamente usando a pasta `./data/` dentro do repositório:
- `./data/obs_recordings/` — coloque aqui seus `.mkv` (ou aponte `BANIDO_OBS_DIR` para a pasta de gravações do OBS)
- `./data/Cortes/` — cortes 4K lossless exportados
- `./data/vozes_amigos/` — banco de amostras vocais (`.wav` + `embedding.npy`)
- `./data/models/` — cache local dos modelos Whisper e SpeechBrain
- `./data/database/` — banco SQLite (`WAL`), logs e `settings.json`

### 4. Iniciar o servidor
- **Windows**: dê 2 cliques em `Iniciar_Banido_Studio.bat` (o script detecta `.venv`/`venv`, libera a porta configurada e abre o navegador automaticamente).
- **Linux / macOS**:
  ```bash
  chmod +x start.sh
  ./start.sh
  ```
- **Ou diretamente via Python**:
  ```bash
  python -m app.main
  ```

Acesse o painel em: **`http://localhost:8765`** (ou na porta definida em `BANIDO_PORT`).

---

## ⚙️ Variáveis de Ambiente (`.env`) & Múltiplas Instâncias

Todas as configurações da instância ficam centralizadas em [`app/config.py`](app/config.py) e podem ser ajustadas via `.env` ou variáveis de ambiente do sistema:

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `BANIDO_HOST_NAME` | `Host` | Nome do anfitrião atribuído automaticamente à Faixa 2 (microfone isolado) |
| `BANIDO_GROUP_NAME` | `Banido Studio` | Nome do canal/grupo usado no prompt criativo da IA |
| `BANIDO_BASE_DIR` | `./data` | Pasta raiz dos dados da instância (`database/`, `Cortes/`, `vozes_amigos/`, `models/`) |
| `BANIDO_OBS_DIR` | `<BASE_DIR>/obs_recordings` | Pasta monitorada para arquivos `.mkv` do OBS |
| `BANIDO_CLIPS_DIR` | `<BASE_DIR>/Cortes` | Pasta de saída dos cortes lossless |
| `BANIDO_VOICE_BANK_DIR` | `<BASE_DIR>/vozes_amigos` | Diretório de perfis vocais conhecidos |
| `BANIDO_MODELS_DIR` | `<BASE_DIR>/models` | Diretório de cache dos modelos Whisper / SpeechBrain |
| `BANIDO_TEMP_DIR` | Temp do SO (`/BanidoStudioTemp`) | Pasta para extração temporária de áudio (ideal apontar para um **SSD NVMe**) |
| `BANIDO_PYTHON` | Python atual (`sys.executable`) | Caminho explícito do interpretador Python para os workers |
| `BANIDO_HOST` | `127.0.0.1` | Endereço de bind do servidor FastAPI |
| `BANIDO_ACCESS_PASSWORD` | _(vazio)_ | Senha de acesso. **Obrigatória** se `BANIDO_HOST` não for local (ex.: `0.0.0.0`): sem ela, o servidor recusa todas as requisições. Com ela, quem acessar pela rede vê uma tela de login (cookie de sessão por 7 dias; reiniciar o servidor encerra as sessões). Use só em redes de confiança: a conexão é HTTP, sem criptografia. |
| `BANIDO_PORT` | `8765` | Porta HTTP da instância |
| `BANIDO_WHISPER_MODEL` | `large-v3` | Modelo do `faster-whisper` (`large-v3`, `large-v3-turbo`, `medium`, `small`, etc.) |
| `BANIDO_WHISPER_DEVICE` | `auto` | Dispositivo (`auto`, `cuda` ou `cpu`) |
| `BANIDO_WHISPER_COMPUTE_TYPE` | `auto` | Precisão (`auto`, `float16`, `int8_float16`, `int8`) |
| `BANIDO_WHISPER_LANGUAGE` | `pt` | Idioma da transcrição (`pt`, `en`, `auto`, etc.) |
| `BANIDO_SEPARATE_TRACKS` | `1` | Com microfone isolado (6 ou 2 faixas), transcreve microfone e Discord separadamente e mantém falas sobrepostas. Usa o Whisper duas vezes por bloco (mais lento, mais fiel). `0` volta ao mix único. |
| `GOOGLE_API_KEY` | *(vazio)* | Chave da API Google Gemini (opcional; também pode ser salva pela interface) |
| `NVIDIA_API_KEY` | *(vazio)* | Chave da API NVIDIA NIM (opcional; também pode ser salva pela interface) |

### Exemplo para rodar 2ª instância ou discos separados (HDD + SSD)
```env
BANIDO_HOST_NAME=SeuNome
BANIDO_GROUP_NAME=SeuCanal
BANIDO_BASE_DIR=D:/BanidoStudio
BANIDO_OBS_DIR=D:/Videos/OBS
BANIDO_TEMP_DIR=C:/BanidoStudioTemp
BANIDO_PORT=8766
```
Cada instância mantém seu próprio SQLite, logs, banco de vozes e porta HTTP de forma totalmente isolada.

---

## 🔐 Segurança e Chaves de API

- **Nenhuma chave de API fica armazenada no código-fonte.**
- As chaves podem ser informadas na interface Web (`⚙️ Configurações das IAs` → salvas localmente em `<BASE_DIR>/database/banido_studio.db` e `settings.json`) **ou** via `.env` (`GOOGLE_API_KEY` / `NVIDIA_API_KEY`).
- O endpoint `GET /api/settings` devolve **apenas a versão mascarada** das chaves (ex.: `AIza...1234`), exigindo o token de sessão (`X-Banido-Token`) gerado no boot do servidor, e o `TrustedHostMiddleware` restringe o cabeçalho `Host` a `localhost`/`127.0.0.1` por padrão (configurável via `BANIDO_ALLOWED_HOSTS`).
- O `.gitignore` bloqueia explicitamente `.env`, `.env.local`, `data/`, `database/`, `settings.json`, `obs_backups/`, `*.db*`, `logs/`, `temp/`, `models/`, `Cortes/`, `vozes_amigos/`, `obs_recordings/` e arquivos de mídia (`.mkv`, `.mp4`, `.wav`, `.srt`, `.npy`).

---

## 🎙️ Layout de Áudio do OBS Studio (Recomendado)

Para extrair 100% do potencial da diarização física e da normalização YouTube Gold, configure o OBS para gravar em **`.mkv`** com **6 faixas de áudio**:

1. **Faixa 1**: Mix Geral (todas as fontes)
2. **Faixa 2**: Microfone isolado do anfitrião
3. **Faixa 3**: Áudio isolado do Discord / call
4. **Faixa 4**: Áudio do jogo
5. **Faixa 5**: Áudio do desktop / navegador
6. **Faixa 6**: Soundpad / efeitos (usado na telemetria de hype)

> **Nota**: Gravações com **2 faixas** (Mix/Discord + Mic) ou **1 faixa** (Mix único) também funcionam — o pipeline detecta a contagem de streams automaticamente e ajusta o fluxo.

O utilitário opcional `python -m app.obs_fixer` faz backup da cena ativa do OBS e aplica o roteamento multi-track padrão automaticamente.

---

## 🧭 Fluxo de Uso na Interface

1. Coloque gravações `.mkv` na pasta configurada (`BANIDO_OBS_DIR`) e clique em **🔄 Atualizar**.
2. Opcionalmente informe o **Jogo** e um **Contexto da Sessão** (ajuda o `initial_prompt` do Whisper e a IA).
3. Escolha a engine (**Google Gemini**, **NVIDIA NIM** ou **Claude Web**), marque opcionalmente **🔊 Incluir normalização de áudio (YouTube Gold lossless) ao processar** se quiser normalizar automaticamente ao final do pipeline, e clique em **🚀 PROCESSAR VÍDEO COMPLETO**.
4. Na seção de **Vozes Detectadas**:
   - Ouça os trechos de preview diretamente no navegador;
   - Confirme os nomes dos participantes para salvar amostras no Banco de Vozes (`vozes_amigos/`) e atualizar a transcrição/`.srt` instantaneamente.
5. Utilize os recursos pós-análise:
   - **✂️ Cortes 4K Lossless** (automáticos pelos highlights da IA ou intervalo manual);
   - **📑 Embutir Capítulos no MKV** (via `mkvpropedit` ou FFmpeg);
   - **🔊 Normalizar Áudio YouTube Gold** (individual ou em lote);
   - **🏷️ Renomear Arquivo + Legenda `.srt`** com um clique.

---

## 🛠️ Solução de Problemas

- **GPU com 4 GB ou 6 GB de VRAM (Out of Memory no Whisper)**:
  Defina no `.env`:
  ```env
  BANIDO_WHISPER_MODEL=medium
  BANIDO_WHISPER_COMPUTE_TYPE=int8_float16
  ```
- **Sem GPU NVIDIA**:
  Não é necessário configurar nada — o sistema detecta a ausência de CUDA e roda Whisper em `cpu` (`int8`) e SpeechBrain em `cpu`. Para evitar avisos, você pode fixar `BANIDO_WHISPER_DEVICE=cpu`.
- **`ffmpeg` ou `ffprobe` não encontrado**:
  Verifique no terminal com `ffmpeg -version` e `ffprobe -version` e garanta que estão no `PATH`.
- **Mosaico / Preview não atualiza após trocar `.env`**:
  Reinicie o servidor (`Iniciar_Banido_Studio.bat` ou `./start.sh`) para recarregar as variáveis de ambiente e os pontos de montagem estáticos.

---

## 📂 Estrutura do Projeto

```text
├── app/
│   ├── main.py               # Servidor FastAPI e API REST
│   ├── worker.py             # Subprocesso isolado para GPU/FFmpeg/IA
│   ├── config.py             # Configuração portável via .env / ambiente
│   ├── database.py           # SQLite (WAL) + sincronização e histórico de jobs
│   ├── audio_pipeline.py     # Extração paralela, energia multifaixa e telemetria de hype
│   ├── transcriber_v3.py     # Pipeline Whisper + checkpoints + exportação .SRT
│   ├── voice_bank.py         # Diarização e biometria vocal (SpeechBrain ECAPA-TDNN)
│   ├── mosaic_generator.py   # Folha de contato 4x4 para visão multimodal
│   ├── ai_director.py        # Prompts, Gemini, NVIDIA NIM, Claude Web e metadados YT
│   ├── lossless_media.py     # Normalização YouTube Gold, cortes e capítulos MKV
│   ├── obs_fixer.py          # Utilitário opcional de roteamento multi-track no OBS
│   └── static/               # Interface Web (index.html, app.js, style.css)
├── docs/
│   └── ARCHITECTURE.md       # Detalhes técnicos da arquitetura
├── tests/
│   └── test_core.py          # Testes unitários e de API (pytest)
├── .github/workflows/
│   └── ci.yml                # Pipeline CI (AST, Ruff, pytest, JS check)
├── .env.example              # Modelo completo de configuração por instância
├── .gitattributes            # Normalização LF/CRLF entre Linux/macOS e Windows
├── .gitignore                # Proteção contra commit de chaves, dados e mídia
├── Iniciar_Banido_Studio.bat # Inicializador portável para Windows
├── start.sh                  # Inicializador portável para Linux/macOS
└── requirements.txt
```

---

## 🧪 Testes Automatizados

```bash
pip install pytest httpx
pytest -q
```
