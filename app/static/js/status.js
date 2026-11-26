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
                obsLabel.textContent = "Gravações";
                obsLabel.title = studioConfig.obs_dir;
            }
            const gpuBadge = document.getElementById("badge-gpu-label");
            if (gpuBadge && studioConfig.gpu) {
                gpuBadge.textContent = studioConfig.gpu;
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
                btnBatch.innerText = `Cancelar ajuste em lote (${bpct}%)`;
                btnBatch.style.borderColor = "rgba(239, 68, 68, 0.55)";
                btnBatch.style.color = "#f87171";
                batchStatusEl.style.display = "block";
                batchStatusEl.innerText = `${bj.last_step || "Processando lote..."} (${formatDuration(bj.elapsed_sec)})`;
                loadVideos(false);
            } else {
                btnBatch.innerText = "Ajustar volume de todas";
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
            statusText.innerText = `Processando (${formatDuration(j.elapsed_sec)})`;
        } else {
            pulseDot.style.backgroundColor = "var(--success)";
            pulseDot.style.boxShadow = "0 0 8px var(--success)";
            statusText.style.color = "var(--text-secondary)";
            statusText.innerText = "Pronto";
        }
    } catch (e) {
        pulseDot.style.backgroundColor = "var(--danger)";
        pulseDot.style.boxShadow = "0 0 10px var(--danger)";
        statusText.style.color = "var(--danger)";
        statusText.innerText = "Desconectado: abra o Iniciar_Banido_Studio.bat";
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

