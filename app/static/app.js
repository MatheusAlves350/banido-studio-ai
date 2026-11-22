let allVideos = [];
let activeVideoId = null;
let pollInterval = null;
let statusHeartbeat = null;
let activeHistoryJobId = null;
let lastRawLogText = "";
let lastYoutubeTagsCsv = "";
let isBatchAudioRunning = false;
let wasBatchAudioRunning = false;
let gameHistory = [];
let activeSegments = [];
let activePreviewUrls = [];
let studioConfig = {
    host_name: "Host",
    group_name: "Banido Studio",
    base_dir: "data",
    obs_dir: "obs_recordings",
    clips_dir: "Cortes",
    voice_bank_dir: "vozes_amigos",
    temp_dir: "temp",
    gpu: ""
};

function revokeAllPreviewUrls() {
    while (activePreviewUrls.length > 0) {
        const u = activePreviewUrls.pop();
        try {
            URL.revokeObjectURL(u);
        } catch (_) {}
    }
}

const GOOGLE_MODELS = [
    { value: "gemini-2.5-flash-lite", label: "gemini-2.5-flash-lite (Recomendado - Rápido e Cota Alta)" },
    { value: "gemini-2.5-flash", label: "gemini-2.5-flash (Gemini 2.5 Flash)" },
    { value: "gemini-3-flash-preview", label: "gemini-3-flash-preview (Gemini 3 Flash)" },
    { value: "gemini-3.1-flash-lite", label: "gemini-3.1-flash-lite (Gemini 3.1 Flash Lite)" },
    { value: "gemini-3.5-flash-lite", label: "gemini-3.5-flash-lite (Gemini 3.5 Flash Lite)" },
    { value: "gemini-3.5-flash", label: "gemini-3.5-flash (Gemini 3.5 Flash)" },
    { value: "gemini-3.6-flash", label: "gemini-3.6-flash (Gemini 3.6 Flash)" },
    { value: "gemini-3.7-flash", label: "gemini-3.7-flash (Gemini 3.7 Flash)" },
    { value: "gemini-3.8-flash", label: "gemini-3.8-flash (Gemini 3.8 Flash)" },
    { value: "gemini-4-hydrogen", label: "gemini-4-hydrogen (Gemini 4 Hydrogen)" },
    { value: "gemini-flash-latest", label: "gemini-flash-latest (Gemini Flash Latest)" },
    { value: "gemini-3.1-pro-preview", label: "gemini-3.1-pro-preview (Plano Pago API)" },
    { value: "gemini-pro-latest", label: "gemini-pro-latest (Plano Pago API)" }
];

const NVIDIA_MODELS = [
    { value: "mistralai/mistral-large-4-1t-a52b", label: "mistralai/mistral-large-4-1t-a52b (Mistral Large 4 - Recomendado)" },
    { value: "moonshotai/kimi-k3.1-pro", label: "moonshotai/kimi-k3.1-pro (Kimi K3.1 Pro - Textos Longos)" },
    { value: "nvidia/nemotron-3-ultra-550b-a55b", label: "nvidia/nemotron-3-ultra-550b-a55b (Nemotron 3 Ultra 550B)" },
    { value: "nvidia/nemotron-3.5-super-vl-120b-a12b", label: "nvidia/nemotron-3.5-super-vl-120b-a12b (Nemotron 3.5 Super VL 120B)" },
    { value: "nvidia/nemotron-3.5-lightning-30b-a3b", label: "nvidia/nemotron-3.5-lightning-30b-a3b (Nemotron 3.5 Lightning 30B)" },
    { value: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", label: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning (Nemotron Raciocínio)" },
    { value: "z-ai/glm-5.3-flash", label: "z-ai/glm-5.3-flash (GLM 5.3 Flash - Ultra Rápido)" },
    { value: "minimaxai/minimax-m3.1-flash", label: "minimaxai/minimax-m3.1-flash (MiniMax M3.1 Flash)" },
    { value: "google/gemma-4-31b-it", label: "google/gemma-4-31b-it (Gemma 4 31B)" },
    { value: "meta/muse-glimmer-30b", label: "meta/muse-glimmer-30b (Meta Muse Glimmer 30B)" },
    { value: "stepfun-ai/step-5-preview", label: "stepfun-ai/step-5-preview (StepFun Step 5 Preview)" },
    { value: "google/gemma-3-12b-it", label: "google/gemma-3-12b-it (Gemma 3 12B)" }
];

document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
    loadSettings(true);
    loadGameHistory();
    loadVideos(true);
    pollServerStatus();
    statusHeartbeat = setInterval(pollServerStatus, 3000);
});

function formatDuration(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const rem = s % 60;
    if (hrs > 0) return `${hrs}h ${String(mins).padStart(2, "0")}m ${String(rem).padStart(2, "0")}s`;
    if (mins > 0) return `${mins}m ${String(rem).padStart(2, "0")}s`;
    return `${rem}s`;
}

function parseTimestampToSeconds(val) {
    if (typeof val === "number" && !Number.isNaN(val)) return val;
    if (typeof val !== "string") return NaN;
    const trimmed = val.trim();
    if (!trimmed) return NaN;
    const num = Number(trimmed);
    if (!Number.isNaN(num)) return num;
    const parts = trimmed.split(":").map(Number);
    if (parts.some(Number.isNaN)) return NaN;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return NaN;
}

function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function colorizeLogHtml(rawText) {
    if (!rawText) return `<span style="color:#64748b;">Nenhum log registrado ainda para este vídeo.</span>`;
    return rawText.split("\n").map(line => {
        const safe = escapeHtml(line);
        if (line.includes("[SUCESSO]") || line.includes("✅")) {
            return `<span class="log-line-ok">${safe}</span>`;
        }
        if (line.includes("[ERRO") || line.includes("❌") || line.includes("Traceback") || line.includes("RuntimeError")) {
            return `<span class="log-line-err">${safe}</span>`;
        }
        if (line.includes("[AVISO") || line.includes("🛑") || line.includes("⚠️")) {
            return `<span class="log-line-warn">${safe}</span>`;
        }
        if (line.includes("[IA ")) {
            return `<span class="log-line-ai">${safe}</span>`;
        }
        if (line.includes("[WHISPER]") || line.includes("[SRT]") || line.includes("[VOZES]")) {
            return `<span class="log-line-whisper">${safe}</span>`;
        }
        if (line.includes("[FALA #")) {
            return `<span class="log-line-speech">${safe}</span>`;
        }
        if (line.includes("[FFMPEG]") || line.includes("[MOSAICO]") || line.includes("[TELEMETRIA]") || line.includes("[AUDIO NORM]") || line.includes("[AUDIO LOTE]")) {
            return `<span class="log-line-ffmpeg">${safe}</span>`;
        }
        return safe;
    }).join("\n");
}

function setupEventListeners() {
    document.getElementById("btn-refresh").addEventListener("click", () => loadVideos(true));
    document.getElementById("video-search").addEventListener("input", filterVideos);

    const gameInput = document.getElementById("custom-game-input");
    const ctxInput = document.getElementById("session-context-input");

    gameInput.addEventListener("input", () => {
        highlightMatchingGameChip(gameInput.value);
    });
    gameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            saveVideoGameAndContext(true);
        }
    });
    gameInput.addEventListener("change", () => {
        // Se escolheu um jogo do histórico que tem contexto padrão e o campo de contexto está vazio, sugere automaticamente
        const matchedGame = gameHistory.find(g => g.name.toLowerCase() === gameInput.value.trim().toLowerCase());
        if (matchedGame && matchedGame.default_context && !ctxInput.value.trim()) {
            ctxInput.value = matchedGame.default_context;
        }
        saveVideoGameAndContext(false);
    });

    ctxInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            saveVideoGameAndContext(true);
        }
    });
    ctxInput.addEventListener("blur", () => {
        if (activeVideoId && (gameInput.value.trim() || ctxInput.value.trim())) {
            saveVideoGameAndContext(false);
        }
    });

    const btnSaveGame = document.getElementById("btn-save-game-context");
    if (btnSaveGame) {
        btnSaveGame.addEventListener("click", () => saveVideoGameAndContext(true));
    }

    // Manual Cut & Transcript Explorer
    const btnManualCut = document.getElementById("btn-manual-cut-export");
    if (btnManualCut) {
        btnManualCut.addEventListener("click", exportManualCut);
    }

    const btnToggleTranscript = document.getElementById("btn-toggle-transcript");
    if (btnToggleTranscript) {
        btnToggleTranscript.addEventListener("click", () => {
            const body = document.getElementById("transcript-explorer-body");
            const opening = body.style.display === "none";
            body.style.display = opening ? "block" : "none";
            btnToggleTranscript.textContent = opening ? "🔼 Recolher Transcrição" : "🔽 Abrir Busca na Transcrição";
            if (opening) {
                renderTranscriptResults();
            }
        });
    }

    const trSearch = document.getElementById("transcript-search-input");
    if (trSearch) {
        trSearch.addEventListener("input", renderTranscriptResults);
    }
    const trSpeaker = document.getElementById("transcript-speaker-filter");
    if (trSpeaker) {
        trSpeaker.addEventListener("change", renderTranscriptResults);
    }

    document.getElementById("select-engine").addEventListener("change", () => {
        updateActiveModelDropdown();
    });

    // Process, Normalize Audio & Cancel/Reset buttons
    document.getElementById("btn-start-process").addEventListener("click", startProcessing);
    document.getElementById("btn-normalize-audio").addEventListener("click", normalizeVideoAudio);
    document.getElementById("btn-normalize-batch").addEventListener("click", toggleBatchAudioNormalize);
    document.getElementById("btn-reanalyze-ai").addEventListener("click", reanalyzeAiOnly);
    document.getElementById("btn-retry-ai-only").addEventListener("click", reanalyzeAiOnly);
    document.getElementById("btn-reset-process").addEventListener("click", resetProcessing);
    document.getElementById("btn-retry-process").addEventListener("click", () => resetProcessing(true));
    document.getElementById("btn-show-log-from-err").addEventListener("click", () => {
        const cc = document.getElementById("console-card");
        const co = document.getElementById("console-output");
        cc.style.display = "block";
        co.style.display = "block";
        cc.scrollIntoView({ behavior: "smooth" });
    });

    // Console controls
    document.getElementById("btn-toggle-console-body").addEventListener("click", () => {
        const out = document.getElementById("console-output");
        out.style.display = out.style.display === "none" ? "block" : "none";
    });
    document.getElementById("btn-copy-log").addEventListener("click", () => {
        navigator.clipboard.writeText(lastRawLogText || "").then(() => {
            alert("Log copiado para a área de transferência!");
        });
    });

    // Rename, Embed Chapters & YouTube
    document.getElementById("btn-apply-rename").addEventListener("click", applyRename);
    document.getElementById("btn-embed-chapters").addEventListener("click", embedChaptersIntoMkv);
    document.getElementById("btn-copy-yt-tags").addEventListener("click", () => {
        if (!lastYoutubeTagsCsv) {
            alert("Nenhuma tag gerada para este vídeo ainda.");
            return;
        }
        navigator.clipboard.writeText(lastYoutubeTagsCsv).then(() => {
            alert("Tags (separadas por vírgula) copiadas! Cole direto no campo Tags do YouTube Studio.");
        });
    });
    document.getElementById("btn-copy-yt-desc").addEventListener("click", () => {
        const text = document.getElementById("yt-description-box").value;
        navigator.clipboard.writeText(text).then(() => {
            alert("Descrição do YouTube copiada com sucesso!");
        });
    });

    // Claude Web
    document.getElementById("btn-copy-claude-prompt").addEventListener("click", copyClaudePrompt);
    document.getElementById("btn-import-claude-response").addEventListener("click", importClaudeResponse);

    // History Modal
    const historyModal = document.getElementById("history-modal");
    document.getElementById("btn-open-history").addEventListener("click", openHistoryModal);
    document.getElementById("btn-topbar-history").addEventListener("click", openHistoryModal);
    document.getElementById("btn-refresh-history").addEventListener("click", () => loadHistoryList(true));
    document.getElementById("btn-close-history").addEventListener("click", () => {
        historyModal.style.display = "none";
    });
    document.getElementById("btn-copy-history-log").addEventListener("click", () => {
        const txt = document.getElementById("history-log-viewer").innerText;
        navigator.clipboard.writeText(txt).then(() => alert("Log do histórico copiado!"));
    });

    // Settings Modal
    const settingsModal = document.getElementById("settings-modal");
    document.getElementById("btn-open-settings").addEventListener("click", () => {
        loadSettings(false);
        settingsModal.style.display = "flex";
    });
    document.getElementById("btn-close-settings").addEventListener("click", () => {
        settingsModal.style.display = "none";
    });
    document.getElementById("btn-save-settings").addEventListener("click", saveSettings);

    document.getElementById("btn-toggle-google-key").addEventListener("click", () => {
        const inp = document.getElementById("input-google-key");
        inp.type = inp.type === "password" ? "text" : "password";
    });
    document.getElementById("btn-toggle-nvidia-key").addEventListener("click", () => {
        const inp = document.getElementById("input-nvidia-key");
        inp.type = inp.type === "password" ? "text" : "password";
    });

    document.getElementById("btn-test-google-key").addEventListener("click", () => testGoogleKey(true));
    document.getElementById("btn-test-nvidia-key").addEventListener("click", () => testNvidiaKey(true));
}

async function pollServerStatus() {
    const statusText = document.getElementById("connection-status");
    const pulseDot = document.getElementById("server-pulse-dot");
    try {
        const stRes = await fetch("/api/status");
        if (!stRes.ok) throw new Error("HTTP " + stRes.status);
        const st = await stRes.json();

        if (st.config) {
            studioConfig = { ...studioConfig, ...st.config };
            const obsLabel = document.getElementById("sidebar-obs-label");
            if (obsLabel && studioConfig.obs_dir) {
                obsLabel.textContent = `GRAVAÇÕES OBS`;
                obsLabel.title = studioConfig.obs_dir;
            }
            const gpuBadge = document.getElementById("badge-gpu-label");
            if (gpuBadge && studioConfig.gpu) {
                gpuBadge.textContent = `${studioConfig.gpu} • AV1 4K`;
            }
            const pathsBox = document.getElementById("instance-paths-info");
            if (pathsBox) {
                pathsBox.innerHTML =
                    `<div><strong>Anfitrião:</strong> ${escapeHtml(studioConfig.host_name || "Host")} (${escapeHtml(studioConfig.group_name || "Banido Studio")})</div>` +
                    `<div><strong>Gravações (.mkv):</strong> ${escapeHtml(studioConfig.obs_dir || "")}</div>` +
                    `<div><strong>Cortes:</strong> ${escapeHtml(studioConfig.clips_dir || "")}</div>` +
                    `<div><strong>Banco de Vozes:</strong> ${escapeHtml(studioConfig.voice_bank_dir || "")}</div>` +
                    `<div><strong>Temporários:</strong> ${escapeHtml(studioConfig.temp_dir || "")}</div>`;
            }
        }

        document.getElementById("history-count-badge").innerText = st.total_jobs ?? 0;

        isBatchAudioRunning = Boolean(st.batch_audio_active);
        const btnBatch = document.getElementById("btn-normalize-batch");
        const batchStatusEl = document.getElementById("batch-audio-status");
        if (btnBatch && batchStatusEl) {
            if (isBatchAudioRunning && st.batch_job) {
                const bj = st.batch_job;
                const bpct = Math.round((bj.progress_pct || 0) * 100);
                btnBatch.innerText = `🛑 Cancelar Lote de Áudio (${bpct}%)`;
                btnBatch.style.borderColor = "rgba(239, 68, 68, 0.55)";
                btnBatch.style.color = "#f87171";
                batchStatusEl.style.display = "block";
                batchStatusEl.innerText = `⚡ ${bj.last_step || "Processando lote..."} (${formatDuration(bj.elapsed_sec)})`;
                loadVideos(false);
            } else {
                btnBatch.innerText = "🔊 Normalizar Áudio (Todos)";
                btnBatch.style.borderColor = "rgba(16, 185, 129, 0.45)";
                btnBatch.style.color = "#34d399";
                batchStatusEl.style.display = "none";
                if (wasBatchAudioRunning) {
                    loadVideos(true);
                    if (activeVideoId) fetchVideoDetails(activeVideoId, true);
                }
            }
        }
        wasBatchAudioRunning = isBatchAudioRunning;

        if (st.active_count > 0) {
            const j = st.active_jobs[0];
            pulseDot.style.backgroundColor = "var(--accent-gold)";
            pulseDot.style.boxShadow = "0 0 10px var(--accent-gold)";
            statusText.style.color = "var(--accent-gold)";
            statusText.innerText = `⚡ Executando Job #${j.job_id} (PID ${j.pid} • ${formatDuration(j.elapsed_sec)})`;
        } else {
            pulseDot.style.backgroundColor = "var(--success)";
            pulseDot.style.boxShadow = "0 0 8px var(--success)";
            statusText.style.color = "var(--text-secondary)";
            statusText.innerText = "Servidor Online • Pronto";
        }
    } catch (e) {
        pulseDot.style.backgroundColor = "var(--danger)";
        pulseDot.style.boxShadow = "0 0 10px var(--danger)";
        statusText.style.color = "var(--danger)";
        statusText.innerText = "Servidor Desconectado (Abra Iniciar_Banido_Studio.bat)";
    }
}

function updateActiveModelDropdown(preferredModel = null) {
    const engine = document.getElementById("select-engine").value;
    const modelSelect = document.getElementById("select-active-model");
    modelSelect.innerHTML = "";

    if (engine === "gemini") {
        modelSelect.style.display = "inline-block";
        GOOGLE_MODELS.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.value;
            opt.textContent = m.label;
            modelSelect.appendChild(opt);
        });
        const defaultGoogle = preferredModel || document.getElementById("select-google-default-model")?.value || GOOGLE_MODELS[0].value;
        const validGoogle = GOOGLE_MODELS.some(m => m.value === defaultGoogle) ? defaultGoogle : GOOGLE_MODELS[0].value;
        modelSelect.value = validGoogle;
    } else if (engine === "nvidia") {
        modelSelect.style.display = "inline-block";
        NVIDIA_MODELS.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.value;
            opt.textContent = m.label;
            modelSelect.appendChild(opt);
        });
        const defaultNvidia = preferredModel || document.getElementById("select-nvidia-default-model")?.value || NVIDIA_MODELS[0].value;
        const validNvidia = NVIDIA_MODELS.some(m => m.value === defaultNvidia) ? defaultNvidia : NVIDIA_MODELS[0].value;
        modelSelect.value = validNvidia;
    } else {
        modelSelect.style.display = "none";
    }
}

async function loadVideos(sync = true) {
    try {
        const res = await fetch(`/api/videos?sync=${sync ? "1" : "0"}`);
        const data = await res.json();
        allVideos = data.videos || [];
        filterVideos();
    } catch (e) {
        console.error("Erro ao carregar vídeos:", e);
    }
}

async function loadGameHistory() {
    try {
        const res = await fetch("/api/games");
        if (!res.ok) return;
        const data = await res.json();
        gameHistory = data.games || [];
        renderGameHistoryChips();
    } catch (e) {
        console.error("Erro ao carregar histórico de jogos:", e);
    }
}

function highlightMatchingGameChip(gameName) {
    const clean = (gameName || "").trim().toLowerCase();
    document.querySelectorAll(".btn-chip-wrap").forEach(wrap => {
        const g = (wrap.dataset.game || "").toLowerCase();
        wrap.classList.toggle("selected", clean !== "" && g === clean);
    });
}

function renderGameHistoryChips() {
    const container = document.getElementById("quick-games-chips");
    const datalist = document.getElementById("games-history-datalist");
    const curVal = (document.getElementById("custom-game-input")?.value || "").trim();

    if (datalist) {
        datalist.innerHTML = "";
        gameHistory.forEach(g => {
            const opt = document.createElement("option");
            opt.value = g.name;
            datalist.appendChild(opt);
        });
    }

    if (!container) return;
    container.innerHTML = "";

    // Mostrar os 8 jogos mais recentes/frequentes como chips de 1 clique (todos ficam no autocomplete)
    gameHistory.slice(0, 8).forEach(g => {
        const wrap = document.createElement("span");
        wrap.className = "btn-chip-wrap";
        wrap.dataset.game = g.name;

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-chip";
        btn.dataset.game = g.name;
        btn.textContent = g.name;
        btn.title = g.default_context
            ? `Selecionar ${g.name} (Contexto salvo: ${g.default_context})`
            : `Selecionar ${g.name}`;

        btn.addEventListener("click", () => {
            const gameInp = document.getElementById("custom-game-input");
            const ctxInp = document.getElementById("session-context-input");
            gameInp.value = g.name;
            if (g.default_context && !ctxInp.value.trim()) {
                ctxInp.value = g.default_context;
            }
            highlightMatchingGameChip(g.name);
            saveVideoGameAndContext(false);
        });

        const delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.className = "chip-del";
        delBtn.textContent = "×";
        delBtn.title = `Remover "${g.name}" do histórico de recomendações`;
        delBtn.addEventListener("click", async (e) => {
            e.stopPropagation();
            if (!confirm(`Remover "${g.name}" do histórico de jogos recomendados?`)) return;
            await removeGameFromHistory(g.name);
        });

        wrap.appendChild(btn);
        wrap.appendChild(delBtn);
        container.appendChild(wrap);
    });

    highlightMatchingGameChip(curVal);
}

async function removeGameFromHistory(gameName) {
    try {
        const res = await fetch(`/api/games/${encodeURIComponent(gameName)}`, { method: "DELETE" });
        if (res.ok) {
            const d = await res.json();
            gameHistory = d.games || [];
            renderGameHistoryChips();
        }
    } catch (e) {
        console.error("Erro ao remover jogo do histórico:", e);
    }
}

async function saveVideoGameAndContext(saveDefaultContext = false) {
    const gameInp = document.getElementById("custom-game-input");
    const ctxInp = document.getElementById("session-context-input");
    const statusEl = document.getElementById("game-save-status");
    const gameVal = (gameInp?.value || "").trim();
    const ctxVal = (ctxInp?.value || "").trim();

    if (!activeVideoId) {
        if (gameVal) {
            try {
                const res = await fetch("/api/games", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: gameVal, default_context: saveDefaultContext ? ctxVal : null })
                });
                if (res.ok) {
                    const d = await res.json();
                    gameHistory = d.games || [];
                    renderGameHistoryChips();
                }
            } catch (_) {}
        }
        return;
    }

    try {
        const res = await fetch(`/api/video/${activeVideoId}/game`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                game_name: gameVal,
                session_context: ctxVal,
                save_default_context: Boolean(saveDefaultContext && gameVal && ctxVal)
            })
        });
        if (!res.ok) return;
        const d = await res.json();
        const idx = allVideos.findIndex(v => v.id === activeVideoId);
        if (idx !== -1) {
            allVideos[idx].game_name = d.game_name;
            allVideos[idx].session_context = d.session_context;
        }
        if (Array.isArray(d.games)) {
            gameHistory = d.games;
            renderGameHistoryChips();
        }
        if (statusEl) {
            statusEl.textContent = "✅ Salvo!";
            clearTimeout(statusEl._timer);
            statusEl._timer = setTimeout(() => { statusEl.textContent = ""; }, 2200);
        }
    } catch (e) {
        console.error("Erro ao salvar jogo/contexto:", e);
    }
}

function filterVideos() {
    const q = document.getElementById("video-search").value.toLowerCase();
    const filtered = allVideos.filter(v => v.filename.toLowerCase().includes(q));
    renderVideoList(filtered);
}

function renderVideoList(videos) {
    const list = document.getElementById("video-list");
    const prevScroll = list.scrollTop;
    list.innerHTML = "";

    if (!videos || videos.length === 0) {
        const obsDir = escapeHtml(studioConfig.obs_dir || "./data/obs_recordings");
        list.innerHTML = `
            <div style="padding: 16px; margin: 8px; border: 1px dashed var(--border-color); border-radius: 10px; font-size: 12px; color: var(--text-secondary); line-height: 1.5;">
                <div style="font-weight: 700; color: var(--text-primary); margin-bottom: 6px;">📭 Nenhum arquivo .mkv encontrado</div>
                Coloque suas gravações do OBS em:<br>
                <code style="word-break: break-all; color: var(--accent);">${obsDir}</code><br><br>
                Ou configure <code>BANIDO_OBS_DIR</code> no arquivo <code>.env</code> e reinicie o servidor.
            </div>
        `;
        return;
    }

    videos.forEach(v => {
        const item = document.createElement("div");
        item.className = `video-item ${activeVideoId === v.id ? "active" : ""}`;

        let statusBadge = `<span class="status-badge status-unprocessed">Não Processado</span>`;
        if (v.status === "processing") {
            statusBadge = `<span class="status-badge status-processing">⚡ ${Math.round((v.progress_pct || 0) * 100)}%</span>`;
        } else if (v.status === "completed") {
            statusBadge = `<span class="status-badge status-completed">✅ Concluído</span>`;
        } else if (v.status === "error") {
            statusBadge = `<span class="status-badge" style="background: rgba(239, 68, 68, 0.2); color: #ef4444;">❌ Erro</span>`;
        }

        const audioNormTag = v.audio_normalized
            ? ` <span title="Áudio normalizado no padrão YouTube Gold" style="color:#34d399; font-weight:700;">🔊</span>`
            : "";

        item.innerHTML = `
            <div class="video-item-title" title="${escapeHtml(v.filename)}">${escapeHtml(v.filename)}</div>
            <div class="video-item-meta">
                <span>${v.size_gb} GB${audioNormTag}</span>
                ${statusBadge}
            </div>
        `;

        item.addEventListener("click", () => selectVideo(v.id));
        list.appendChild(item);
    });
    list.scrollTop = prevScroll;
}

function syncGameSelectorInput(gameName, sessionContext = "") {
    const inp = document.getElementById("custom-game-input");
    const ctxInp = document.getElementById("session-context-input");
    const clean = (gameName || "").trim();
    if (inp) inp.value = clean;
    if (ctxInp) ctxInp.value = (sessionContext || "").trim();
    highlightMatchingGameChip(clean);
}

async function selectVideo(videoId) {
    activeVideoId = videoId;
    revokeAllPreviewUrls();
    filterVideos();

    const matched = allVideos.find(v => v.id === videoId);
    syncGameSelectorInput(matched ? matched.game_name : "", matched ? matched.session_context : "");

    // Recolher busca da transcrição ao trocar de vídeo
    const trBody = document.getElementById("transcript-explorer-body");
    const trBtn = document.getElementById("btn-toggle-transcript");
    if (trBody && trBtn) {
        trBody.style.display = "none";
        trBtn.textContent = "🔽 Abrir Busca na Transcrição";
    }

    document.getElementById("empty-state").style.display = "none";
    document.getElementById("video-workspace").style.display = "block";

    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }

    lastRawLogText = "";
    await fetchVideoDetails(videoId, true);
    await fetchVideoLogs(videoId);
}

async function fetchVideoLogs(videoId) {
    if (videoId !== activeVideoId) return;
    const consoleCard = document.getElementById("console-card");
    const consoleOut = document.getElementById("console-output");
    const jobBadge = document.getElementById("console-job-badge");
    const autoScroll = document.getElementById("chk-autoscroll").checked;

    try {
        const res = await fetch(`/api/logs/video/${videoId}?tail=250`);
        const d = await res.json();
        if (!d.has_job) {
            consoleCard.style.display = "none";
            return;
        }

        consoleCard.style.display = "block";
        const j = d.job;
        const statusMap = {
            running: "⚡ EXECUTANDO",
            completed: "✅ CONCLUÍDO",
            error: "❌ ERRO",
            cancelled: "🛑 CANCELADO",
            interrupted: "⚠️ INTERROMPIDO"
        };
        const stLabel = statusMap[j.status] || j.status.toUpperCase();
        const pidTxt = j.pid ? ` • PID ${j.pid}` : "";
        jobBadge.innerText = `Job #${j.id} • ${stLabel}${pidTxt} • ⏱️ ${formatDuration(j.elapsed_sec)} • ${j.model || j.engine}`;

        if (d.log_text !== lastRawLogText) {
            lastRawLogText = d.log_text || "";
            consoleOut.innerHTML = colorizeLogHtml(lastRawLogText);
            if (autoScroll) {
                consoleOut.scrollTop = consoleOut.scrollHeight;
            }
        }
    } catch (e) {
        console.error("Erro ao ler logs:", e);
    }
}

async function fetchVideoDetails(videoId, includeData = true) {
    if (videoId !== activeVideoId) return;
    try {
        const res = await fetch(`/api/video/${videoId}?include_data=${includeData ? "1" : "0"}`);
        if (!res.ok) return;
        const v = await res.json();

        // Se estávamos em polling leve (includeData=false) e o vídeo acabou de concluir, busca os dados completos uma vez
        if (!includeData && v.status === "completed") {
            return fetchVideoDetails(videoId, true);
        }

        // Sincronizar item na barra lateral sem recarregar tudo
        const idx = allVideos.findIndex(item => item.id === v.id);
        if (idx !== -1) {
            allVideos[idx] = { ...allVideos[idx], ...v };
            filterVideos();
        }

        if (includeData) {
            syncGameSelectorInput(v.game_name || "", v.session_context || "");
        }

        let statusColor = "var(--text-secondary)";
        if (v.status === "completed") statusColor = "var(--success)";
        if (v.status === "processing") statusColor = "var(--accent-gold)";
        if (v.status === "error") statusColor = "var(--danger)";

        const srtBadge = v.srt_path ? ` • <span style="color:var(--accent);">📝 .SRT Gerado</span>` : "";
        const audioBadge = v.audio_normalized
            ? ` • <span style="color:#34d399; font-weight:600;">🔊 Áudio YouTube OK</span>`
            : ` • <span style="color:var(--accent-gold);">🔈 Áudio Original (OBS)</span>`;
        document.getElementById("active-video-header").innerHTML = `
            <h2>${escapeHtml(v.filename)}</h2>
            <div style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">
                Tamanho: ${v.size_gb} GB • Status: <strong style="color: ${statusColor};">${v.status.toUpperCase()}</strong>${srtBadge}${audioBadge}
            </div>
        `;

        const progressContainer = document.getElementById("progress-container");
        const resultsContainer = document.getElementById("results-container");
        const errorContainer = document.getElementById("error-container");
        const btnStart = document.getElementById("btn-start-process");
        const btnNormAudio = document.getElementById("btn-normalize-audio");
        const btnReanalyze = document.getElementById("btn-reanalyze-ai");
        const btnReset = document.getElementById("btn-reset-process");

        if (v.status === "processing") {
            progressContainer.style.display = "block";
            resultsContainer.style.display = "none";
            errorContainer.style.display = "none";

            btnStart.disabled = true;
            btnStart.innerText = "⏳ PROCESSANDO EM SEGUNDO PLANO...";
            if (btnNormAudio) {
                btnNormAudio.disabled = true;
            }
            btnReanalyze.style.display = "none";

            btnReset.style.display = "inline-block";
            btnReset.className = "btn-secondary btn-danger";
            btnReset.innerText = "🛑 Cancelar Processamento";

            document.getElementById("progress-msg").innerText = v.progress_msg || "Processando...";
            const pct = Math.round((v.progress_pct || 0) * 100);
            document.getElementById("progress-pct").innerText = `${pct}%`;
            document.getElementById("progress-bar-fill").style.width = `${pct}%`;

            if (v.latest_job) {
                document.getElementById("progress-meta").innerText =
                    `(Job #${v.latest_job.id} • PID ${v.latest_job.pid} • Tempo: ${formatDuration(v.latest_job.elapsed_sec)})`;
            }

            // Expandir console automaticamente durante execução
            document.getElementById("console-output").style.display = "block";

            if (!pollInterval) {
                pollInterval = setInterval(async () => {
                    await fetchVideoDetails(videoId, false);
                    await fetchVideoLogs(videoId);
                }, 1500);
            }
        } else {
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
                await fetchVideoLogs(videoId);
                pollServerStatus();
                loadGameHistory();
            }

            btnStart.disabled = false;
            btnStart.innerText = v.status === "completed" ? "🚀 REPROCESSAR TUDO" : "🚀 PROCESSAR VÍDEO COMPLETO";
            if (btnNormAudio) {
                if (v.audio_normalized) {
                    btnNormAudio.disabled = true;
                    btnNormAudio.innerText = "✅ Áudio YouTube OK";
                    btnNormAudio.style.opacity = "0.75";
                } else {
                    btnNormAudio.disabled = false;
                    btnNormAudio.innerText = "🔊 Normalizar Áudio (YouTube)";
                    btnNormAudio.style.opacity = "1";
                }
            }
            btnReset.className = "btn-secondary";
            btnReset.innerText = "🔄 Reiniciar Status";

            if (v.status === "completed") {
                progressContainer.style.display = "none";
                errorContainer.style.display = "none";
                resultsContainer.style.display = "block";
                btnReanalyze.style.display = "inline-block";
                btnReset.style.display = "inline-block";
                renderCompletedVideo(v);
            } else if (v.status === "error") {
                progressContainer.style.display = "none";
                resultsContainer.style.display = "none";
                errorContainer.style.display = "block";
                btnReanalyze.style.display = "none";
                btnReset.style.display = "inline-block";
                document.getElementById("error-title").innerText = "❌ Falha na Execução:";
                document.getElementById("error-msg").innerText = v.progress_msg || "Ocorreu um erro durante o processamento.";
                document.getElementById("console-output").style.display = "block";
            } else {
                // unprocessed
                progressContainer.style.display = "none";
                resultsContainer.style.display = "none";
                btnReanalyze.style.display = "none";
                btnReset.style.display = "none";

                if (v.progress_msg && v.progress_msg.includes("interrompido")) {
                    errorContainer.style.display = "block";
                    document.getElementById("error-title").innerText = "⚠️ Sessão Anterior Interrompida:";
                    document.getElementById("error-msg").innerText = v.progress_msg;
                } else {
                    errorContainer.style.display = "none";
                }
            }
        }
    } catch (e) {
        console.error("Erro ao obter detalhes:", e);
    }
}

function renderCompletedVideo(v) {
    const data = v.data || {};
    revokeAllPreviewUrls();

    // Aviso de IA caso o SRT tenha dado certo mas a nuvem tenha falhado
    const warnBox = document.getElementById("ai-warning-box");
    if (data.ai_warning) {
        warnBox.style.display = "block";
        document.getElementById("ai-warning-text").innerText = data.ai_warning;
    } else {
        warnBox.style.display = "none";
    }

    // Jogos detectados (Jogo Principal + Outros Jogos)
    const gamesCard = document.getElementById("games-summary-card");
    const mainGameEl = document.getElementById("detected-main-game");
    const otherWrap = document.getElementById("detected-other-games-wrap");
    const otherList = document.getElementById("detected-other-games-list");
    const mainGame = (data.main_game || v.game_name || "").trim();
    const otherGames = Array.isArray(data.other_games) ? data.other_games.filter(g => g && g.trim()) : [];
    if (gamesCard && mainGameEl) {
        if (mainGame || otherGames.length > 0) {
            gamesCard.style.display = "block";
            mainGameEl.textContent = mainGame || "Gameplay";
            if (otherWrap && otherList) {
                if (otherGames.length > 0) {
                    otherWrap.style.display = "inline";
                    otherList.innerHTML = otherGames
                        .map(g => `<span class="topic-tag">${escapeHtml(g)}</span>`)
                        .join("");
                } else {
                    otherWrap.style.display = "none";
                    otherList.innerHTML = "";
                }
            }
        } else {
            gamesCard.style.display = "none";
        }
    }

    // Mosaico
    const mosaicImg = document.getElementById("mosaic-img");
    if (v.mosaic_path) {
        mosaicImg.src = `${v.mosaic_path}?t=${Math.floor(v.mtime || Date.now())}`;
        mosaicImg.style.display = "block";
    } else {
        mosaicImg.style.display = "none";
    }

    // Títulos
    const titlesList = document.getElementById("title-options-list");
    titlesList.innerHTML = "";
    (data.title_options || []).forEach((t, i) => {
        const opt = document.createElement("label");
        opt.className = "title-option-item";
        opt.innerHTML = `
            <input type="radio" name="title-option" value="${escapeHtml(t)}" ${i === 0 ? "checked" : ""}>
            <span>${escapeHtml(t)}</span>
        `;
        opt.querySelector("input").addEventListener("change", () => {
            document.getElementById("custom-title-input").value = t;
        });
        titlesList.appendChild(opt);
    });
    if ((data.title_options || []).length > 0) {
        document.getElementById("custom-title-input").value = data.title_options[0];
    }

    renderSpeakers(v.id, data, v.known_friends || []);
    renderHighlights(v.id, data.highlights || [], v.duration_sec || 0);
    renderChapters(data.chapters || []);
    renderTopics(data.topics || []);
    setupTranscriptExplorer(data.segments || []);

    const ytDesc = data.youtube_description || "";
    lastYoutubeTagsCsv = data.youtube_tags_csv || "";
    document.getElementById("yt-description-box").value = ytDesc;
    const charBadge = document.getElementById("yt-desc-char-count");
    if (charBadge) {
        const len = ytDesc.length;
        charBadge.textContent = `${len.toLocaleString("pt-BR")} / 5.000 caracteres`;
        if (len > 5000) {
            charBadge.style.color = "#f87171";
            charBadge.style.background = "rgba(239, 68, 68, 0.18)";
        } else if (len >= 4500) {
            charBadge.style.color = "var(--accent-gold)";
            charBadge.style.background = "rgba(255, 183, 3, 0.18)";
        } else {
            charBadge.style.color = "var(--accent)";
            charBadge.style.background = "rgba(0, 229, 255, 0.1)";
        }
    }
}

function setupTranscriptExplorer(segments) {
    activeSegments = Array.isArray(segments) ? segments : [];
    const badge = document.getElementById("transcript-count-badge");
    if (badge) {
        badge.textContent = `${activeSegments.length.toLocaleString("pt-BR")} falas`;
    }
    const sel = document.getElementById("transcript-speaker-filter");
    if (sel) {
        const prev = sel.value;
        const speakers = Array.from(new Set(activeSegments.map(s => s.speaker || "Discord"))).sort();
        sel.innerHTML = `<option value="">Todos os Participantes (${speakers.length})</option>`;
        speakers.forEach(spk => {
            const opt = document.createElement("option");
            opt.value = spk;
            opt.textContent = spk;
            sel.appendChild(opt);
        });
        if (speakers.includes(prev)) sel.value = prev;
    }
    const body = document.getElementById("transcript-explorer-body");
    if (body && body.style.display !== "none") {
        renderTranscriptResults();
    }
}

function renderTranscriptResults() {
    const list = document.getElementById("transcript-results-list");
    const countEl = document.getElementById("transcript-match-count");
    if (!list) return;

    const q = (document.getElementById("transcript-search-input")?.value || "").trim().toLowerCase();
    const spkFilter = document.getElementById("transcript-speaker-filter")?.value || "";

    const matched = activeSegments.filter(seg => {
        if (spkFilter && (seg.speaker || "Discord") !== spkFilter) return false;
        if (q) {
            const txt = String(seg.text || "").toLowerCase();
            const spk = String(seg.speaker || "").toLowerCase();
            return txt.includes(q) || spk.includes(q);
        }
        return true;
    });

    const maxRender = 150;
    const shown = matched.slice(0, maxRender);
    if (countEl) {
        countEl.textContent = matched.length > maxRender
            ? `Mostrando ${maxRender} de ${matched.length} falas (refine a busca)`
            : `${matched.length} fala(s) encontrada(s)`;
    }

    list.innerHTML = "";
    if (shown.length === 0) {
        list.innerHTML = `<p style="color: var(--text-secondary); font-size: 12.5px; padding: 8px;">Nenhuma fala encontrada com esse filtro.</p>`;
        return;
    }

    shown.forEach(seg => {
        const tsStart = formatSecondsToHHMMSS(seg.start);
        const tsEnd = formatSecondsToHHMMSS(seg.end);
        const row = document.createElement("div");
        row.className = "transcript-row";
        row.innerHTML = `
            <div class="transcript-row-text">
                <span class="chapter-ts">${escapeHtml(tsStart)}</span>
                <span class="transcript-row-speaker">[${escapeHtml(seg.speaker || "Discord")}]</span>
                <span>${escapeHtml(seg.text || "")}</span>
            </div>
        `;

        const actions = document.createElement("div");
        actions.className = "transcript-row-actions";

        const btnSetStart = document.createElement("button");
        btnSetStart.type = "button";
        btnSetStart.className = "btn-tweak";
        btnSetStart.textContent = "⏱️ Início";
        btnSetStart.title = "Definir este instante como início do Corte Manual";
        btnSetStart.addEventListener("click", () => {
            const startInp = document.getElementById("manual-cut-start");
            const endInp = document.getElementById("manual-cut-end");
            const titleInp = document.getElementById("manual-cut-title");
            if (startInp) startInp.value = formatSecondsToHHMMSS(Math.max(0, (seg.start || 0) - 2));
            if (endInp && !endInp.value.trim()) {
                endInp.value = formatSecondsToHHMMSS((seg.start || 0) + 45);
            }
            if (titleInp && !titleInp.value.trim()) {
                titleInp.value = String(seg.text || "").slice(0, 45);
            }
            document.getElementById("manual-cut-start")?.scrollIntoView({ behavior: "smooth", block: "center" });
        });

        const btnSetEnd = document.createElement("button");
        btnSetEnd.type = "button";
        btnSetEnd.className = "btn-tweak";
        btnSetEnd.textContent = "🏁 Fim";
        btnSetEnd.title = `Definir ${tsEnd} como fim do Corte Manual`;
        btnSetEnd.addEventListener("click", () => {
            const endInp = document.getElementById("manual-cut-end");
            if (endInp) endInp.value = formatSecondsToHHMMSS((seg.end || seg.start || 0) + 2);
            document.getElementById("manual-cut-end")?.scrollIntoView({ behavior: "smooth", block: "center" });
        });

        actions.appendChild(btnSetStart);
        actions.appendChild(btnSetEnd);
        row.appendChild(actions);
        list.appendChild(row);
    });
}

function renderChapters(chapters) {
    const container = document.getElementById("chapters-list");
    if (!container) return;
    container.innerHTML = "";

    if (!chapters || chapters.length === 0) {
        container.innerHTML = `<p style="color: var(--text-secondary); font-size: 13px;">Nenhum capítulo gerado ainda.</p>`;
        return;
    }

    chapters.forEach(c => {
        const item = document.createElement("div");
        item.className = "chapter-item";
        const ts = c.timestamp || c.time || "00:00:00";
        const title = c.title || c.name || "Capítulo";
        item.innerHTML = `<span class="chapter-ts">${escapeHtml(ts)}</span><span>${escapeHtml(title)}</span>`;
        container.appendChild(item);
    });
}

function renderTopics(topics) {
    const container = document.getElementById("topics-list");
    if (!container) return;
    container.innerHTML = "";

    if (!topics || topics.length === 0) {
        container.innerHTML = `<p style="color: var(--text-secondary); font-size: 13px;">Nenhum assunto classificado ainda.</p>`;
        return;
    }

    topics.forEach(t => {
        const item = document.createElement("div");
        item.className = "topic-item";
        const tsHtml = t.timestamp ? `<span class="topic-ts">${escapeHtml(t.timestamp)}</span>` : "";
        const tags = Array.isArray(t.tags) ? t.tags : [];
        const tagsHtml = tags.length
            ? `<div class="topic-tags">${tags.map(tag => `<span class="topic-tag">${escapeHtml(tag)}</span>`).join("")}</div>`
            : "";
        item.innerHTML = `<div>${tsHtml}<span>${escapeHtml(t.topic || "")}</span></div>${tagsHtml}`;
        container.appendChild(item);
    });
}

function formatSecondsToHHMMSS(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    return [h, m, r].map(v => String(v).padStart(2, "0")).join(":");
}

function createVoicePreviewControl(videoId, tag, startSec) {
    const wrap = document.createElement("div");
    wrap.style.marginBottom = "8px";

    const tsLabel = (startSec !== undefined && startSec !== null)
        ? ` [${formatSecondsToHHMMSS(startSec)}]`
        : "";

    const btnPlay = document.createElement("button");
    btnPlay.type = "button";
    btnPlay.className = "btn-secondary btn-sm";
    btnPlay.style.width = "100%";
    btnPlay.style.marginBottom = "6px";
    btnPlay.textContent = `🔊 Ouvir Trecho da Voz${tsLabel}`;

    const audioEl = document.createElement("audio");
    audioEl.controls = true;
    audioEl.preload = "none";
    audioEl.style.width = "100%";
    audioEl.style.height = "32px";
    audioEl.style.display = "none";

    btnPlay.addEventListener("click", async () => {
        if (audioEl.dataset.loaded === "1") {
            audioEl.currentTime = 0;
            audioEl.play().catch(() => {});
            return;
        }
        const origTxt = btnPlay.textContent;
        btnPlay.disabled = true;
        btnPlay.textContent = "⏳ Extraindo trecho do MKV...";
        try {
            const url = `/api/speakers/preview/${videoId}/${encodeURIComponent(tag)}?t=${Date.now()}`;
            const res = await fetch(url);
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const blob = await res.blob();
            const objUrl = URL.createObjectURL(blob);
            activePreviewUrls.push(objUrl);
            audioEl.src = objUrl;
            audioEl.dataset.loaded = "1";
            audioEl.style.display = "block";
            btnPlay.textContent = `🔁 Ouvir Novamente${tsLabel}`;
            await audioEl.play().catch(() => {});
        } catch (e) {
            alert("Não foi possível carregar o áudio deste trecho: " + e.message);
            btnPlay.textContent = origTxt;
        } finally {
            btnPlay.disabled = false;
        }
    });

    wrap.appendChild(btnPlay);
    wrap.appendChild(audioEl);
    return wrap;
}

function renderSpeakers(videoId, data, knownFriends = []) {
    const grid = document.getElementById("speakers-grid");
    grid.innerHTML = "";

    const datalist = document.getElementById("known-friends-datalist");
    if (datalist) {
        datalist.innerHTML = "";
        (knownFriends || []).forEach(name => {
            const opt = document.createElement("option");
            opt.value = name;
            datalist.appendChild(opt);
        });
    }

    const hostName = studioConfig.host_name || "Host";
    const matheusCard = document.createElement("div");
    matheusCard.className = "speaker-card";
    matheusCard.innerHTML = `
        <div class="speaker-header">
            <span class="speaker-tag">[${escapeHtml(hostName)}]</span>
            <span class="status-badge status-completed">Microfone Isolado (100%)</span>
        </div>
        <p style="font-size: 12px; color: var(--text-secondary); margin-bottom: 10px;">Identificado pela energia diferencial da Faixa 2 do OBS. Ouça ou salve amostras .WAV manualmente quando desejar:</p>
    `;
    matheusCard.appendChild(createVoicePreviewControl(videoId, hostName, null));
    const btnMatheus = document.createElement("button");
    btnMatheus.className = "btn-secondary";
    btnMatheus.style.width = "100%";
    btnMatheus.textContent = `💾 Salvar Amostras .WAV de ${hostName}`;
    btnMatheus.addEventListener("click", () => harvestMatheusSamples(videoId, btnMatheus));
    matheusCard.appendChild(btnMatheus);
    grid.appendChild(matheusCard);

    const speakerSamples = data.speaker_samples || {};
    const speakerMapping = data.speaker_mapping || {};

    Object.keys(speakerSamples).forEach(tag => {
        const info = speakerSamples[tag] || {};
        const isKnown = Boolean(info.known);
        const confirmedManually = Boolean(info.confirmed_manually);
        const confirmedName = String(info.confirmed_name || "").trim();
        const rawDeduced = speakerMapping[tag];
        const deduced = typeof rawDeduced === "object" && rawDeduced !== null
            ? (rawDeduced.name || "")
            : String(rawDeduced || "");

        const suggestedName = confirmedName
            ? confirmedName
            : (isKnown
                ? tag
                : (deduced && !deduced.toLowerCase().startsWith("desconhecid")
                    ? deduced.split(/[\(—:]/)[0].trim()
                    : ""));

        let badgeHtml = `<span style="font-size: 11px; color: var(--text-secondary);">Não identificado</span>`;
        if (confirmedManually && confirmedName) {
            badgeHtml = `<span class="status-badge status-completed">✅ Confirmado: ${escapeHtml(confirmedName)}</span>`;
        } else if (isKnown) {
            badgeHtml = `<span class="status-badge status-completed">✅ Reconhecido (${Math.round((info.avg_confidence || 0.7) * 100)}%)</span>`;
        } else if (deduced) {
            badgeHtml = `<span style="font-size: 11px; color: var(--accent-gold);">💡 IA sugeriu: ${escapeHtml(deduced)}</span>`;
        }

        const segInfo = info.segment_count ? ` (${info.segment_count} falas)` : "";
        const displayTag = (confirmedManually && confirmedName && confirmedName.toLowerCase() !== tag.toLowerCase())
            ? `${confirmedName} ← ${tag}`
            : tag;

        const card = document.createElement("div");
        card.className = "speaker-card";
        card.innerHTML = `
            <div class="speaker-header">
                <span class="speaker-tag">[${escapeHtml(displayTag)}]${escapeHtml(segInfo)}</span>
                ${badgeHtml}
            </div>
            <p style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">
                Trecho: "<em>${escapeHtml((info.text || "").slice(0, 75))}...</em>"
            </p>
        `;

        card.appendChild(createVoicePreviewControl(videoId, tag, info.start));

        const input = document.createElement("input");
        input.type = "text";
        input.setAttribute("list", "known-friends-datalist");
        input.placeholder = "Escolha ou digite o nome exato do amigo...";
        input.value = suggestedName;

        const btn = document.createElement("button");
        btn.className = "btn-secondary";
        btn.style.width = "100%";
        btn.textContent = isKnown
            ? "➕ Reforçar / Salvar Mais Amostras .WAV"
            : "💾 Confirmar & Salvar Amostras .WAV";
        btn.addEventListener("click", () => confirmSpeakerName(videoId, tag, input, btn));

        card.appendChild(input);
        card.appendChild(btn);
        grid.appendChild(card);
    });
}

async function harvestMatheusSamples(videoId, btnEl) {
    const hostName = studioConfig.host_name || "Host";
    try {
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.textContent = `⏳ Salvando amostras de ${hostName}...`;
        }
        const res = await fetch(`/api/speakers/harvest-host/${videoId}`, { method: "POST" });
        const d = await res.json();
        if (d.success) {
            const countSaved = (d.harvest && d.harvest.samples_saved) ? d.harvest.samples_saved.length : 0;
            alert(`✅ ${countSaved} amostra(s) .WAV de ${hostName} salvas em vozes_amigos/${hostName}/!`);
        } else {
            alert(`Erro ao salvar amostras de ${hostName}: ` + (d.detail || "Falha desconhecida"));
        }
    } catch (e) {
        alert(`Erro ao salvar amostras de ${hostName}: ` + e);
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = `💾 Salvar Amostras .WAV de ${hostName}`;
        }
    }
}

async function embedChaptersIntoMkv() {
    if (!activeVideoId) return;
    const modeSelect = document.getElementById("select-embed-mode");
    const mode = modeSelect ? modeSelect.value : "both";
    const modeLabel = modeSelect ? modeSelect.options[modeSelect.selectedIndex].text : mode;

    if (!confirm(`Deseja embutir "${modeLabel}" como capítulos diretamente dentro do arquivo MKV original (100% lossless, sem re-renderizar vídeo/áudio)?\n\nObs: Capítulos embutidos funcionam em players locais (VLC, MPV, PotPlayer). No YouTube, os capítulos são lidos da Descrição copiada abaixo.`)) {
        return;
    }

    const btn = document.getElementById("btn-embed-chapters");
    const origText = btn ? btn.textContent : "";
    try {
        if (btn) {
            btn.disabled = true;
            btn.textContent = "⏳ Embutindo capítulos no MKV (Lossless)...";
        }
        const res = await fetch(`/api/embed-chapters/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: mode })
        });
        const d = await res.json();
        if (res.ok && d.success) {
            alert(`✅ ${d.chapters_count} capítulo(s) embutidos com sucesso no MKV original!\n\nArquivo: ${d.filename}\nMétodo: ${d.method}`);
        } else {
            alert("Erro ao embutir capítulos: " + (d.detail || d.error || "Falha desconhecida"));
        }
    } catch (e) {
        alert("Erro ao embutir capítulos: " + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = origText;
        }
    }
}

async function confirmSpeakerName(videoId, tag, inputEl, btnEl) {
    const name = inputEl ? inputEl.value.trim() : "";
    if (!name) {
        alert("Digite o nome do amigo antes de confirmar!");
        return;
    }

    try {
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.textContent = "⏳ Salvando amostras .WAV...";
        }
        const res = await fetch("/api/speakers/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: videoId,
                speaker_tag: tag,
                friend_name: name
            })
        });
        const d = await res.json();
        if (d.success) {
            const countSaved = (d.harvest && d.harvest.samples_saved) ? d.harvest.samples_saved.length : 0;
            alert(`Voz de [${d.friend_name || name}] confirmada! ${d.updated_segments || 0} falas atualizadas no .SRT e ${countSaved} amostra(s) .WAV salvas no Banco de Vozes.`);
            fetchVideoDetails(videoId, true);
        } else {
            alert("Erro ao confirmar voz: " + (d.detail || "Falha desconhecida"));
        }
    } catch (e) {
        alert("Erro ao confirmar voz: " + e);
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = "💾 Confirmar & Salvar Amostras .WAV";
        }
    }
}

function renderHighlights(videoId, highlights, totalDurationSec = 0) {
    const grid = document.getElementById("highlights-grid");
    grid.innerHTML = "";

    if (highlights.length === 0) {
        grid.innerHTML = `<p style="color: var(--text-secondary);">Nenhum melhor momento identificado nesta sessão.</p>`;
        return;
    }

    highlights.forEach(h => {
        const card = document.createElement("div");
        card.className = "highlight-card";

        let startSec = parseTimestampToSeconds(h.start);
        if (Number.isNaN(startSec)) startSec = parseTimestampToSeconds(h.start_time);
        if (Number.isNaN(startSec)) startSec = 0;

        let endSec = parseTimestampToSeconds(h.end);
        if (Number.isNaN(endSec)) endSec = parseTimestampToSeconds(h.end_time);
        if (Number.isNaN(endSec) || endSec <= startSec) endSec = startSec + 30;

        const clipTitle = h.title || "Corte";

        card.innerHTML = `
            <div>
                <div class="highlight-header">
                    <span class="hl-range-label" style="font-size: 12px; font-weight: 700; color: var(--accent);"></span>
                    <span class="score-badge">🔥 Hype: ${escapeHtml(String(h.score ?? 85))}/100</span>
                </div>
                <div class="highlight-title">${escapeHtml(clipTitle)}</div>
                <div class="highlight-reason">${escapeHtml(h.reason || "")}</div>
            </div>
        `;

        const rangeEl = card.querySelector(".hl-range-label");
        const updateRangeLabel = () => {
            const durS = Math.max(1, Math.round(endSec - startSec));
            rangeEl.textContent = `${formatSecondsToHHMMSS(startSec)} ➔ ${formatSecondsToHHMMSS(endSec)} (${durS}s)`;
        };
        updateRangeLabel();

        // Controles de ajuste fino (-5s / +5s no início e no fim)
        const tweakRow = document.createElement("div");
        tweakRow.className = "highlight-tweak-row";

        const mkBtn = (label, onClick) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn-tweak";
            b.textContent = label;
            b.addEventListener("click", onClick);
            return b;
        };

        const startGrp = document.createElement("span");
        startGrp.className = "tweak-group";
        startGrp.append("Início:", mkBtn("-5s", () => {
            startSec = Math.max(0, startSec - 5);
            updateRangeLabel();
        }), mkBtn("+5s", () => {
            startSec = Math.min(endSec - 3, startSec + 5);
            updateRangeLabel();
        }));

        const endGrp = document.createElement("span");
        endGrp.className = "tweak-group";
        endGrp.append("Fim:", mkBtn("-5s", () => {
            endSec = Math.max(startSec + 3, endSec - 5);
            updateRangeLabel();
        }), mkBtn("+5s", () => {
            const cap = totalDurationSec > 0 ? totalDurationSec : (endSec + 60);
            endSec = Math.min(cap, endSec + 5);
            updateRangeLabel();
        }));

        tweakRow.appendChild(startGrp);
        tweakRow.appendChild(endGrp);
        card.appendChild(tweakRow);

        // Prévia de áudio antes de exportar o corte 4K
        const previewWrap = document.createElement("div");
        previewWrap.style.marginBottom = "8px";
        const btnPrev = document.createElement("button");
        btnPrev.type = "button";
        btnPrev.className = "btn-secondary btn-sm";
        btnPrev.style.width = "100%";
        btnPrev.style.marginBottom = "5px";
        btnPrev.textContent = "🔊 Ouvir Prévia do Momento (até 30s)";

        const audioPrev = document.createElement("audio");
        audioPrev.controls = true;
        audioPrev.preload = "none";
        audioPrev.style.width = "100%";
        audioPrev.style.height = "30px";
        audioPrev.style.display = "none";

        btnPrev.addEventListener("click", async () => {
            const key = `${Math.round(startSec)}_${Math.round(endSec)}`;
            if (audioPrev.dataset.loadedKey === key) {
                audioPrev.currentTime = 0;
                audioPrev.play().catch(() => {});
                return;
            }
            const orig = btnPrev.textContent;
            btnPrev.disabled = true;
            btnPrev.textContent = "⏳ Extraindo prévia de áudio...";
            try {
                const url = `/api/audio/preview/${videoId}?start=${startSec.toFixed(1)}&end=${endSec.toFixed(1)}&t=${Date.now()}`;
                const res = await fetch(url);
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }
                const blob = await res.blob();
                const objUrl = URL.createObjectURL(blob);
                activePreviewUrls.push(objUrl);
                audioPrev.src = objUrl;
                audioPrev.dataset.loadedKey = key;
                audioPrev.style.display = "block";
                btnPrev.textContent = "🔁 Ouvir Prévia Novamente";
                await audioPrev.play().catch(() => {});
            } catch (e) {
                alert("Não foi possível carregar prévia do momento: " + e.message);
                btnPrev.textContent = orig;
            } finally {
                btnPrev.disabled = false;
            }
        });
        previewWrap.appendChild(btnPrev);
        previewWrap.appendChild(audioPrev);
        card.appendChild(previewWrap);

        const btn = document.createElement("button");
        btn.className = "btn-cut";
        btn.textContent = "✂️ Exportar Corte 4K 60fps Lossless (2s)";
        btn.addEventListener("click", () => cutHighlight(btn, videoId, startSec, endSec, clipTitle));
        card.appendChild(btn);

        grid.appendChild(card);
    });
}

async function exportManualCut() {
    if (!activeVideoId) return;
    const startRaw = (document.getElementById("manual-cut-start")?.value || "").trim();
    const endRaw = (document.getElementById("manual-cut-end")?.value || "").trim();
    const title = (document.getElementById("manual-cut-title")?.value || "").trim() || "Corte Manual";
    const btn = document.getElementById("btn-manual-cut-export");

    const startSec = parseTimestampToSeconds(startRaw);
    const endSec = parseTimestampToSeconds(endRaw);
    if (Number.isNaN(startSec) || Number.isNaN(endSec) || endSec <= startSec) {
        alert("Informe tempos válidos de Início e Fim (ex: 00:12:30 até 00:13:45) com Fim maior que Início.");
        return;
    }

    const origTxt = btn ? btn.textContent : "";
    try {
        if (btn) {
            btn.disabled = true;
            btn.textContent = "⏳ Cortando 4K...";
        }
        const res = await fetch("/api/cut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: activeVideoId,
                start_sec: startSec,
                end_sec: endSec,
                title: title,
                boost_discord: true
            })
        });
        const data = await res.json();
        if (data.success) {
            const clipsDir = studioConfig.clips_dir || "Cortes";
            const savedTarget = data.clip_path || `${clipsDir}/${data.filename}`;
            alert(`Corte Manual 4K Lossless gerado com sucesso!\nSalvo em: ${savedTarget}`);
        } else {
            alert("Erro ao gerar corte manual: " + (data.detail || data.error || "Desconhecido"));
        }
    } catch (e) {
        alert("Erro ao gerar corte manual: " + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = origTxt;
        }
    }
}

async function cutHighlight(btn, videoId, startSec, endSec, title) {
    try {
        btn.innerText = "⏳ Cortando em 4K...";
        btn.disabled = true;

        const res = await fetch("/api/cut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: videoId,
                start_sec: startSec,
                end_sec: endSec,
                title: title,
                boost_discord: true
            })
        });
        const data = await res.json();
        if (data.success) {
            const clipsDir = studioConfig.clips_dir || "Cortes";
            const savedTarget = data.clip_path || `${clipsDir}/${data.filename}`;
            alert(`Corte 4K Lossless gerado com sucesso!\nSalvo em: ${savedTarget}`);
        } else {
            alert("Erro ao gerar corte: " + (data.detail || data.error || "Desconhecido"));
        }
    } catch (e) {
        alert("Erro ao cortar: " + e);
    } finally {
        btn.innerText = "✂️ Exportar Corte 4K 60fps Lossless (2s)";
        btn.disabled = false;
    }
}

async function startProcessing() {
    if (!activeVideoId) return;

    const manualGame = document.getElementById("custom-game-input").value.trim();
    const extraContext = (document.getElementById("session-context-input")?.value || "").trim();
    const engine = document.getElementById("select-engine").value;
    const activeModel = document.getElementById("select-active-model").value;

    try {
        lastRawLogText = "";
        const res = await fetch(`/api/process/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                manual_game: manualGame,
                extra_context: extraContext,
                engine: engine,
                custom_model: activeModel
            })
        });
        const data = await res.json();
        if (data.already_running) {
            alert(data.message);
        }
        loadGameHistory();
        await fetchVideoDetails(activeVideoId, false);
        await fetchVideoLogs(activeVideoId);
        pollServerStatus();
    } catch (e) {
        alert("Erro ao iniciar processamento: " + e);
    }
}

async function normalizeVideoAudio() {
    if (!activeVideoId) return;
    const current = allVideos.find(v => v.id === activeVideoId);
    if (current && current.audio_normalized) {
        alert("Este vídeo já foi normalizado no padrão YouTube Gold!");
        return;
    }
    const fname = current ? current.filename : `Vídeo #${activeVideoId}`;
    const sizeGb = current ? current.size_gb : "?";
    if (!confirm(
        `Normalizar o áudio de "${fname}" (${sizeGb} GB) no padrão YouTube Gold?\n\n` +
        `• Vídeo 4K 60fps AV1: 100% lossless (sem re-renderizar)\n` +
        `• Faixa 1 (Mix Geral / YouTube): Mic + Discord equilibrados em ~-15,5 LUFS\n` +
        `• Faixas 2–6 originais, capítulos e transcrições: 100% preservados\n` +
        `• Validação automática com ffprobe antes de substituir o arquivo`
    )) {
        return;
    }

    try {
        lastRawLogText = "";
        const res = await fetch(`/api/normalize-audio/${activeVideoId}`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            alert("Não foi possível iniciar: " + (data.detail || `HTTP ${res.status}`));
            return;
        }
        if (data.already_running) {
            alert(data.message);
        }
        await fetchVideoDetails(activeVideoId);
        await fetchVideoLogs(activeVideoId);
        pollServerStatus();
    } catch (e) {
        alert("Erro ao iniciar normalização de áudio: " + e);
    }
}

async function toggleBatchAudioNormalize() {
    if (isBatchAudioRunning) {
        if (!confirm("Deseja cancelar o lote de normalização de áudio? O vídeo atual será interrompido com segurança e o arquivo original será preservado.")) {
            return;
        }
        try {
            const res = await fetch("/api/normalize-audio-batch/cancel", { method: "POST" });
            const d = await res.json();
            alert(d.message || "Lote cancelado.");
            pollServerStatus();
            loadVideos();
        } catch (e) {
            alert("Erro ao cancelar lote: " + e);
        }
        return;
    }

    const pendingCount = allVideos.filter(v => !v.audio_normalized).length;
    const obsDir = studioConfig.obs_dir || "pasta OBS configurada";
    if (!confirm(
        `Iniciar normalização de áudio YouTube Gold em LOTE para todos os vídeos pendentes em ${obsDir} (${pendingCount} vídeo(s))?\n\n` +
        `• Processa 1 vídeo por vez (seguro para o espaço livre em disco)\n` +
        `• Vídeo 4K 60fps AV1: 100% lossless (-c:v copy)\n` +
        `• Pula automaticamente vídeos que já foram normalizados\n` +
        `• Você pode acompanhar o log ao vivo no botão "Histórico & Logs" ou cancelar a qualquer momento`
    )) {
        return;
    }

    try {
        const res = await fetch("/api/normalize-audio-batch", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            alert("Não foi possível iniciar o lote: " + (data.detail || `HTTP ${res.status}`));
            return;
        }
        pollServerStatus();
        if (data.job_id) {
            openHistoryModal();
        }
    } catch (e) {
        alert("Erro ao iniciar normalização em lote: " + e);
    }
}

async function reanalyzeAiOnly() {
    if (!activeVideoId) return;

    const manualGame = document.getElementById("custom-game-input").value.trim();
    const extraContext = (document.getElementById("session-context-input")?.value || "").trim();
    const engine = document.getElementById("select-engine").value;
    const activeModel = document.getElementById("select-active-model").value;

    try {
        lastRawLogText = "";
        const res = await fetch(`/api/reanalyze-ai/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                manual_game: manualGame,
                extra_context: extraContext,
                engine: engine,
                custom_model: activeModel
            })
        });
        const data = await res.json();
        if (data.already_running) {
            alert(data.message);
        }
        loadGameHistory();
        await fetchVideoDetails(activeVideoId, false);
        await fetchVideoLogs(activeVideoId);
        pollServerStatus();
    } catch (e) {
        alert("Erro ao iniciar reanálise de IA: " + e);
    }
}

async function resetProcessing(skipConfirm = false) {
    if (!activeVideoId) return;
    const current = allVideos.find(v => v.id === activeVideoId);
    if (!skipConfirm && current && current.status === "processing") {
        if (!confirm("Deseja cancelar imediatamente o processamento em andamento e liberar a placa de vídeo?")) {
            return;
        }
    }

    try {
        await fetch(`/api/reset/${activeVideoId}`, { method: "POST" });
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
        await fetchVideoDetails(activeVideoId, true);
        await fetchVideoLogs(activeVideoId);
        await loadVideos(true);
        pollServerStatus();
    } catch (e) {
        alert("Erro ao resetar/cancelar: " + e);
    }
}

async function applyRename() {
    if (!activeVideoId) return;
    const title = document.getElementById("custom-title-input").value.trim();
    if (!title) {
        alert("Selecione ou digite um título!");
        return;
    }

    const currentVideo = allVideos.find(v => v.id === activeVideoId);
    const gameName = document.getElementById("custom-game-input").value.trim() || (currentVideo ? currentVideo.game_name : "") || "";

    try {
        const res = await fetch(`/api/rename/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: title, game_name: gameName })
        });
        const d = await res.json();
        if (d.success) {
            const obsDir = studioConfig.obs_dir || "pasta OBS configurada";
            alert(`Arquivo renomeado com sucesso em ${obsDir} para:\n${d.new_name}`);
            await loadGameHistory();
            await loadVideos(true);
            await selectVideo(activeVideoId);
        } else {
            alert("Erro ao renomear: " + (d.detail || d.error || "Desconhecido"));
        }
    } catch (e) {
        alert("Erro ao renomear: " + e);
    }
}

async function copyClaudePrompt() {
    if (!activeVideoId) return;
    try {
        const res = await fetch(`/api/claude-prompt/${activeVideoId}`);
        const d = await res.json();
        navigator.clipboard.writeText(d.prompt).then(() => {
            alert("Prompt completo copiado com sucesso! Cole no claude.ai (Ctrl+V).");
        });
    } catch (e) {
        alert("Erro ao copiar prompt: " + e);
    }
}

async function importClaudeResponse() {
    if (!activeVideoId) return;
    const respText = document.getElementById("claude-response-input").value.trim();
    if (!respText) {
        alert("Cole a resposta do Claude antes de clicar!");
        return;
    }

    try {
        const res = await fetch(`/api/claude-import/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ claude_response: respText })
        });
        const d = await res.json();
        if (d.success) {
            alert("Resposta do Claude aplicada com sucesso no vídeo!");
            fetchVideoDetails(activeVideoId);
        } else {
            alert("Erro ao interpretar JSON do Claude: " + (d.detail || "Formato inválido"));
        }
    } catch (e) {
        alert("Erro ao aplicar resposta do Claude: " + e);
    }
}

// =========================================================================
// HISTÓRICO DE EXECUÇÕES E LOGS
// =========================================================================

async function openHistoryModal() {
    document.getElementById("history-modal").style.display = "flex";
    await loadHistoryList(true);
}

async function loadHistoryList(autoSelectFirst = false) {
    const pane = document.getElementById("history-list-pane");
    try {
        const res = await fetch("/api/history?limit=60");
        const data = await res.json();
        const jobs = data.jobs || [];

        if (jobs.length === 0) {
            pane.innerHTML = `<div style="padding:20px; text-align:center; color:var(--text-secondary); font-size:13px;">Nenhuma execução registrada no histórico ainda.</div>`;
            document.getElementById("history-log-viewer").innerHTML = "Nenhum job registrado.";
            return;
        }

        pane.innerHTML = "";
        const badgeStyle = {
            running: `<span class="status-badge status-processing">⚡ Em Execução</span>`,
            completed: `<span class="status-badge status-completed">✅ Concluído</span>`,
            error: `<span class="status-badge" style="background:rgba(239,68,68,0.2);color:#ef4444;">❌ Erro</span>`,
            cancelled: `<span class="status-badge" style="background:rgba(251,191,36,0.18);color:#fbbf24;">🛑 Cancelado</span>`,
            interrupted: `<span class="status-badge" style="background:rgba(148,163,184,0.18);color:#cbd5e1;">⚠️ Interrompido</span>`
        };

        jobs.forEach(j => {
            const card = document.createElement("div");
            card.className = `history-item ${activeHistoryJobId === j.id ? "active" : ""}`;
            card.dataset.jobId = String(j.id);
            card.innerHTML = `
                <div class="history-item-top">
                    <span style="color:var(--accent);">Job #${j.id}</span>
                    ${badgeStyle[j.status] || j.status}
                </div>
                <div class="history-item-file" title="${escapeHtml(j.filename)}">${escapeHtml(j.filename)}</div>
                <div class="history-item-sub">
                    📅 ${escapeHtml(j.started_at || "")} • ⏱️ ${formatDuration(j.elapsed_sec)}<br>
                    🤖 ${escapeHtml(j.engine)} (${escapeHtml(j.model || "padrão")})<br>
                    <span style="color:${j.status === 'error' ? '#f87171' : '#94a3b8'};">
                        ${escapeHtml((j.error_detail || j.last_step || "").slice(0, 75))}
                    </span>
                </div>
            `;
            card.addEventListener("click", () => selectHistoryJob(j.id));
            pane.appendChild(card);
        });

        if (autoSelectFirst && (!activeHistoryJobId || !jobs.some(j => j.id === activeHistoryJobId))) {
            selectHistoryJob(jobs[0].id);
        } else if (activeHistoryJobId) {
            selectHistoryJob(activeHistoryJobId);
        }
    } catch (e) {
        pane.innerHTML = `<div style="padding:16px;color:var(--danger);">Erro ao carregar histórico: ${e}</div>`;
    }
}

async function selectHistoryJob(jobId) {
    activeHistoryJobId = jobId;
    document.querySelectorAll(".history-item").forEach(el => {
        el.classList.toggle("active", el.dataset.jobId === String(jobId));
    });
    const viewer = document.getElementById("history-log-viewer");
    const headerTitle = document.getElementById("history-selected-title");
    headerTitle.innerText = `Carregando log do Job #${jobId}...`;

    try {
        const res = await fetch(`/api/logs/job/${jobId}?tail=1000`);
        const d = await res.json();
        const j = d.job;
        headerTitle.innerText = `Job #${j.id} — ${j.filename} (${j.status.toUpperCase()} • ${formatDuration(j.elapsed_sec)})`;
        viewer.innerHTML = colorizeLogHtml(d.log_text || "(Arquivo de log vazio)");
        viewer.scrollTop = viewer.scrollHeight;
    } catch (e) {
        viewer.innerText = "Erro ao abrir log: " + e;
    }
}

// =========================================================================
// CONFIGURAÇÕES E CHAVES DE API
// =========================================================================

async function loadSettings(applyToActionBar = false) {
    try {
        const res = await fetch("/api/settings");
        const data = await res.json();
        const googleInput = document.getElementById("input-google-key");
        const nvidiaInput = document.getElementById("input-nvidia-key");
        const feedback = document.getElementById("google-key-feedback");
        const nvidiaFeedback = document.getElementById("nvidia-key-feedback");
        const googleModelSelect = document.getElementById("select-google-default-model");
        const nvidiaModelSelect = document.getElementById("select-nvidia-default-model");
        const engineSelect = document.getElementById("select-engine");

        if (data.google_api_key) {
            googleInput.value = data.google_api_key;
            feedback.innerHTML = `<span style="color: #10b981; font-weight: 600;">✅ Chave salva e ativa no sistema!</span> (Obtenha grátis em <a href="https://aistudio.google.com" target="_blank">aistudio.google.com</a>)`;
        } else {
            googleInput.value = "";
            feedback.innerHTML = `Obtenha grátis em <a href="https://aistudio.google.com" target="_blank">aistudio.google.com</a> (Lê até 10 horas de sessão de uma vez).`;
        }

        nvidiaInput.value = data.nvidia_api_key || "";
        if (nvidiaFeedback) {
            nvidiaFeedback.innerHTML = data.nvidia_api_key
                ? `<span style="color: #10b981; font-weight: 600;">✅ Chave NVIDIA salva e ativa no sistema!</span>`
                : `Para rodar modelos abertos hospedados na nuvem da NVIDIA.`;
        }

        if (data.google_model && googleModelSelect) {
            const validG = GOOGLE_MODELS.some(m => m.value === data.google_model) ? data.google_model : GOOGLE_MODELS[0].value;
            googleModelSelect.value = validG;
        }
        if (data.nvidia_model && nvidiaModelSelect) {
            const validN = NVIDIA_MODELS.some(m => m.value === data.nvidia_model) ? data.nvidia_model : NVIDIA_MODELS[0].value;
            nvidiaModelSelect.value = validN;
        }

        if (applyToActionBar) {
            if (data.ai_provider && engineSelect) {
                engineSelect.value = data.ai_provider;
            }
            const curEngine = engineSelect ? engineSelect.value : "gemini";
            const preferred = curEngine === "nvidia" ? data.nvidia_model : data.google_model;
            updateActiveModelDropdown(preferred);
        }
    } catch (e) {
        console.error("Erro ao carregar configurações:", e);
    }
}

async function testGoogleKey(showAlert = true) {
    const key = document.getElementById("input-google-key").value.trim();
    const model = document.getElementById("select-google-default-model").value;
    const feedback = document.getElementById("google-key-feedback");
    feedback.innerHTML = `⏳ Testando conexão com a API do Google (${model})...`;

    try {
        const res = await fetch("/api/test-google-key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: key, model: model })
        });
        const data = await res.json();
        if (data.success) {
            feedback.innerHTML = `<span style="color: #10b981; font-weight: 700;">✅ Conexão bem-sucedida com [${data.model}]! Chave válida.</span>`;
            if (showAlert) alert(`✅ Chave do Google Gemini testada com sucesso no modelo [${data.model}]!`);
        } else {
            feedback.innerHTML = `<span style="color: #ef4444; font-weight: 700;">❌ Erro na API (${data.model}): ${escapeHtml(data.error)}</span>`;
            if (showAlert) alert("Erro ao testar a chave: " + data.error);
        }
    } catch (e) {
        feedback.innerHTML = `<span style="color: #ef4444;">Erro ao testar: ${e}</span>`;
    }
}

async function testNvidiaKey(showAlert = true) {
    const key = document.getElementById("input-nvidia-key").value.trim();
    const model = document.getElementById("select-nvidia-default-model").value;
    const feedback = document.getElementById("nvidia-key-feedback");
    feedback.innerHTML = `⏳ Testando conexão com a API da NVIDIA (${model})...`;

    try {
        const res = await fetch("/api/test-nvidia-key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: key, model: model })
        });
        const data = await res.json();
        if (data.success) {
            feedback.innerHTML = `<span style="color: #10b981; font-weight: 700;">✅ Conexão bem-sucedida com [${data.model}]! Chave válida.</span>`;
            if (showAlert) alert(`✅ Chave da NVIDIA NIM testada com sucesso no modelo [${data.model}]!`);
        } else {
            feedback.innerHTML = `<span style="color: #ef4444; font-weight: 700;">❌ Erro na API (${data.model}): ${escapeHtml(data.error)}</span>`;
            if (showAlert) alert("Erro ao testar chave NVIDIA: " + data.error);
        }
    } catch (e) {
        feedback.innerHTML = `<span style="color: #ef4444;">Erro ao testar: ${e}</span>`;
    }
}

async function saveSettings() {
    const googleKey = document.getElementById("input-google-key").value.trim();
    const nvidiaKey = document.getElementById("input-nvidia-key").value.trim();
    const googleModel = document.getElementById("select-google-default-model").value;
    const nvidiaModel = document.getElementById("select-nvidia-default-model").value;
    const aiProvider = document.getElementById("select-engine").value || "gemini";

    try {
        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                google_api_key: googleKey,
                nvidia_api_key: nvidiaKey,
                ai_provider: aiProvider,
                google_model: googleModel,
                nvidia_model: nvidiaModel
            })
        });
        alert("Configurações e modelos salvos com sucesso!");
        await loadSettings(false);
        const preferred = aiProvider === "nvidia" ? nvidiaModel : googleModel;
        updateActiveModelDropdown(preferred);
        document.getElementById("settings-modal").style.display = "none";
    } catch (e) {
        alert("Erro ao salvar configurações: " + e);
    }
}
