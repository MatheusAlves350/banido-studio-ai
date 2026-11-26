async function startProcessing() {
    if (!activeVideoId) return;

    const manualGame = document.getElementById("custom-game-input").value.trim();
    const extraContext = (document.getElementById("session-context-input")?.value || "").trim();
    const engine = document.getElementById("select-engine").value;
    const activeModel = document.getElementById("select-active-model").value;
    const normalizeAudio = Boolean(document.getElementById("chk-normalize-on-process")?.checked);
    const sepEl = document.getElementById("chk-separate-tracks");
    const separateTracks = sepEl ? Boolean(sepEl.checked) : true;

    try {
        lastRawLogText = "";
        const res = await fetch(`/api/process/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                manual_game: manualGame,
                extra_context: extraContext,
                engine: engine,
                custom_model: activeModel,
                normalize_audio: normalizeAudio,
                separate_tracks: separateTracks
            })
        });
        const data = await res.json();
        if (!res.ok) {
            alert("Não foi possível iniciar: " + (data.detail || `HTTP ${res.status}`));
            return;
        }
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

