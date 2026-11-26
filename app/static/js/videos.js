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
            statusEl.textContent = "Salvo";
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
            <div class="video-list-empty">
                <strong>Nenhuma gravação encontrada</strong>
                <p>Coloque os arquivos .mkv do OBS nesta pasta e clique em Atualizar lista:</p>
                <code>${obsDir}</code>
            </div>
        `;
        return;
    }

    videos.forEach(v => {
        const item = document.createElement("div");
        item.className = `video-item ${activeVideoId === v.id ? "active" : ""}`;

        let statusBadge = `<span class="status-badge status-unprocessed">${STATUS_LABELS.unprocessed}</span>`;
        if (v.status === "processing") {
            statusBadge = `<span class="status-badge status-processing">${Math.round((v.progress_pct || 0) * 100)}%</span>`;
        } else if (v.status === "completed") {
            statusBadge = `<span class="status-badge status-completed">${STATUS_LABELS.completed}</span>`;
        } else if (v.status === "error") {
            statusBadge = `<span class="status-badge status-error">${STATUS_LABELS.error}</span>`;
        }

        const rec = describeRecording(v.filename);
        const audioNormTag = v.audio_normalized ? `<span class="meta-flag" title="Volume ajustado para o YouTube">volume ok</span>` : "";

        item.innerHTML = `
            <div class="video-item-title" title="${escapeHtml(v.filename)}">${escapeHtml(rec.title)}</div>
            <div class="video-item-meta">
                <span>${escapeHtml(rec.when || v.filename)}</span>
                ${statusBadge}
            </div>
            <div class="video-item-sub"><span>${v.size_gb} GB</span>${audioNormTag}</div>
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
        trBtn.textContent = "Abrir busca";
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
            running: "Em andamento",
            completed: "Concluído",
            error: "Erro",
            cancelled: "Cancelado",
            interrupted: "Interrompido"
        };
        const stLabel = statusMap[j.status] || j.status.toUpperCase();
        const pidTxt = j.pid ? ` • PID ${j.pid}` : "";
        jobBadge.innerText = `Processamento #${j.id}: ${stLabel}${pidTxt}, ${formatDuration(j.elapsed_sec)}, ${j.model || j.engine}`;

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

        const rec = describeRecording(v.filename);
        const statusCls = { completed: "status-completed", processing: "status-processing", error: "status-error" }[v.status] || "status-unprocessed";
        const facts = [`${v.size_gb} GB`];
        if (v.srt_path) facts.push("legenda pronta");
        facts.push(v.audio_normalized ? "volume ajustado para o YouTube" : "volume original do OBS");
        document.getElementById("active-video-header").innerHTML = `
            <h2 title="${escapeHtml(v.filename)}">${escapeHtml(rec.title)}</h2>
            <div class="active-video-meta">
                <span class="status-badge ${statusCls}">${escapeHtml(STATUS_LABELS[v.status] || v.status)}</span>
                <span>${escapeHtml([rec.when, ...facts].filter(Boolean).join(", "))}</span>
            </div>
        `;

        const normHint = document.getElementById("normalize-on-process-hint");
        if (normHint) {
            normHint.innerHTML = v.audio_normalized
                ? `<span class="text-ok">Este vídeo já está com o volume ajustado. Essa etapa será pulada.</span>`
                : "";
        }

        const progressContainer = document.getElementById("progress-container");
        const resultsContainer = document.getElementById("results-container");
        const errorContainer = document.getElementById("error-container");
        const btnStart = document.getElementById("btn-start-process");
        const btnNormAudio = document.getElementById("btn-normalize-audio");
        const btnRemoveBackup = document.getElementById("btn-remove-audio-backup");
        if (btnRemoveBackup) {
            btnRemoveBackup.style.display = (v.audio_normalized && v.status !== "processing") ? "inline-flex" : "none";
        }
        const btnReanalyze = document.getElementById("btn-reanalyze-ai");
        const btnReset = document.getElementById("btn-reset-process");

        if (v.status === "processing") {
            progressContainer.style.display = "block";
            resultsContainer.style.display = "none";
            errorContainer.style.display = "none";

            btnStart.disabled = true;
            btnStart.innerText = "Processando...";
            if (btnNormAudio) {
                btnNormAudio.disabled = true;
            }
            btnReanalyze.style.display = "none";

            btnReset.style.display = "inline-block";
            btnReset.className = "btn-secondary btn-danger";
            btnReset.innerText = "Cancelar processamento";

            const pMsg = v.progress_msg || "Processando...";
            document.getElementById("progress-msg").innerText = pMsg;
            const btnResumePaused = document.getElementById("btn-resume-paused");
            if (btnResumePaused) {
                btnResumePaused.style.display = pMsg.startsWith("⏸️ Pausado") ? "inline-flex" : "none";
            }
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
            btnStart.innerText = v.status === "completed" ? "Processar de novo" : "Processar vídeo";
            if (btnNormAudio) {
                if (v.audio_normalized) {
                    btnNormAudio.disabled = true;
                    btnNormAudio.innerText = "Volume já ajustado";
                    btnNormAudio.style.opacity = "0.75";
                } else {
                    btnNormAudio.disabled = false;
                    btnNormAudio.innerText = "Ajustar volume deste vídeo";
                    btnNormAudio.style.opacity = "1";
                }
            }
            btnReset.className = "btn-secondary";
            btnReset.innerText = "Reiniciar";

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
                document.getElementById("error-title").innerText = "O processamento falhou:";
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
                    document.getElementById("error-title").innerText = "O processamento anterior foi interrompido:";
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
    transcriptEdits = v.transcript_edits || {};
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

