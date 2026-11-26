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
            btnToggleTranscript.textContent = opening ? "Fechar busca" : "Abrir busca";
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
    const chkNormProcess = document.getElementById("chk-normalize-on-process");
    if (chkNormProcess) {
        try {
            chkNormProcess.checked = localStorage.getItem("banido_normalize_on_process") === "1";
            chkNormProcess.addEventListener("change", () => {
                localStorage.setItem("banido_normalize_on_process", chkNormProcess.checked ? "1" : "0");
            });
        } catch (_) {}
    }
    const chkSeparateTracks = document.getElementById("chk-separate-tracks");
    if (chkSeparateTracks) {
        try {
            const savedSep = localStorage.getItem("banido_separate_tracks");
            if (savedSep !== null) chkSeparateTracks.checked = savedSep === "1";
            chkSeparateTracks.addEventListener("change", () => {
                localStorage.setItem("banido_separate_tracks", chkSeparateTracks.checked ? "1" : "0");
            });
        } catch (_) {}
    }
    const chkKeepTracks = document.getElementById("chk-keep-all-tracks");
    if (chkKeepTracks) {
        try {
            chkKeepTracks.checked = localStorage.getItem("banido_keep_all_tracks") === "1";
            chkKeepTracks.addEventListener("change", () => {
                localStorage.setItem("banido_keep_all_tracks", chkKeepTracks.checked ? "1" : "0");
            });
        } catch (_) {}
    }
    const chkExactCut = document.getElementById("chk-exact-cut");
    if (chkExactCut) {
        try {
            chkExactCut.checked = localStorage.getItem("banido_exact_cut") === "1";
            chkExactCut.addEventListener("change", () => {
                localStorage.setItem("banido_exact_cut", chkExactCut.checked ? "1" : "0");
            });
        } catch (_) {}
    }

    document.getElementById("btn-start-process").addEventListener("click", startProcessing);
    document.getElementById("btn-normalize-audio").addEventListener("click", normalizeVideoAudio);
    document.getElementById("btn-remove-audio-backup")?.addEventListener("click", removeVideoAudioBackup);
    document.getElementById("btn-resume-paused")?.addEventListener("click", resumePausedProcessing);
    document.getElementById("btn-normalize-batch").addEventListener("click", toggleBatchAudioNormalize);
    document.getElementById("btn-reanalyze-ai").addEventListener("click", reanalyzeAiOnly);
    document.getElementById("btn-retry-ai-only").addEventListener("click", reanalyzeAiOnly);
    document.getElementById("btn-reset-process").addEventListener("click", resetProcessing);
    document.getElementById("btn-retry-process").addEventListener("click", () => resetProcessing(true));
    document.getElementById("btn-clear-google-key")?.addEventListener("click", () => clearSavedApiKey("google"));
    document.getElementById("btn-clear-nvidia-key")?.addEventListener("click", () => clearSavedApiKey("nvidia"));
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

