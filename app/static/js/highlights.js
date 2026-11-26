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
                    <span class="score-badge" title="Nota da IA para o quanto o trecho é engraçado ou agitado">Nota ${escapeHtml(String(h.score ?? 85))}</span>
                </div>
                <div class="highlight-title">${escapeHtml(clipTitle)}</div>
                <div class="highlight-reason">${escapeHtml(h.reason || "")}</div>
            </div>
        `;

        const rangeEl = card.querySelector(".hl-range-label");
        const updateRangeLabel = () => {
            const durS = Math.max(1, Math.round(endSec - startSec));
            rangeEl.textContent = `${formatSecondsToHHMMSS(startSec)} até ${formatSecondsToHHMMSS(endSec)} (${durS}s)`;
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
        tweakRow.appendChild(mkBtn("Pré-visualizar", () => openClipPreview({
            videoId, title: clipTitle, startSec, endSec, totalDurationSec,
            onApply: (s, e) => { startSec = s; endSec = e; updateRangeLabel(); }
        })));
        card.appendChild(tweakRow);

        // Prévia de áudio antes de exportar o corte 4K
        const previewWrap = document.createElement("div");
        previewWrap.style.marginBottom = "8px";
        const btnPrev = document.createElement("button");
        btnPrev.type = "button";
        btnPrev.className = "btn-secondary btn-sm";
        btnPrev.style.width = "100%";
        btnPrev.style.marginBottom = "5px";
        btnPrev.textContent = "Ouvir prévia";

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
            btnPrev.textContent = "Carregando prévia...";
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
                btnPrev.textContent = "Ouvir de novo";
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
        btn.textContent = "Exportar corte";
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
            btn.textContent = "Exportando...";
        }
        const keepAllTracks = Boolean(document.getElementById("chk-keep-all-tracks")?.checked);
        const exactCut = Boolean(document.getElementById("chk-exact-cut")?.checked);
        const res = await fetch("/api/cut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: activeVideoId,
                start_sec: startSec,
                end_sec: endSec,
                title: title,
                boost_discord: true,
                keep_all_tracks: keepAllTracks,
                exact_cut: exactCut
            })
        });
        const data = await res.json();
        if (data.success) {
            const clipsDir = studioConfig.clips_dir || "Cortes";
            const savedTarget = data.clip_path || `${clipsDir}/${data.filename}`;
            alert(`Corte exportado.\nSalvo em: ${savedTarget}`);
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
        btn.innerText = "Exportando...";
        btn.disabled = true;

        const keepAllTracks = Boolean(document.getElementById("chk-keep-all-tracks")?.checked);
        const exactCut = Boolean(document.getElementById("chk-exact-cut")?.checked);
        const res = await fetch("/api/cut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: videoId,
                start_sec: startSec,
                end_sec: endSec,
                title: title,
                boost_discord: true,
                keep_all_tracks: keepAllTracks,
                exact_cut: exactCut
            })
        });
        const data = await res.json();
        if (data.success) {
            const clipsDir = studioConfig.clips_dir || "Cortes";
            const savedTarget = data.clip_path || `${clipsDir}/${data.filename}`;
            alert(`Corte exportado.\nSalvo em: ${savedTarget}`);
        } else {
            alert("Erro ao gerar corte: " + (data.detail || data.error || "Desconhecido"));
        }
    } catch (e) {
        alert("Erro ao cortar: " + e);
    } finally {
        btn.innerText = "Exportar corte";
        btn.disabled = false;
    }
}


// C1: abre a pré-visualização a partir dos campos do corte manual.
function previewManualCut() {
    if (!activeVideoId) return;
    const startInp = document.getElementById("manual-cut-start");
    const endInp = document.getElementById("manual-cut-end");
    const startSec = parseTimestampToSeconds((startInp?.value || "").trim());
    const endSec = parseTimestampToSeconds((endInp?.value || "").trim());
    if (Number.isNaN(startSec) || Number.isNaN(endSec) || endSec <= startSec) {
        alert("Informe tempos válidos de Início e Fim (ex: 00:12:30 até 00:13:45) com Fim maior que Início.");
        return;
    }
    const video = allVideos.find(v => v.id === activeVideoId);
    openClipPreview({
        videoId: activeVideoId,
        title: (document.getElementById("manual-cut-title")?.value || "").trim() || "Corte manual",
        startSec, endSec,
        totalDurationSec: (video && video.duration_sec) || 0,
        onApply: (s, e) => {
            if (startInp) startInp.value = formatSecondsToHHMMSS(s);
            if (endInp) endInp.value = formatSecondsToHHMMSS(e);
        }
    });
}
