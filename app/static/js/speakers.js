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
    btnPlay.textContent = `Ouvir trecho${tsLabel}`;

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
        btnPlay.textContent = "Carregando trecho...";
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
            btnPlay.textContent = `Ouvir de novo${tsLabel}`;
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
    const hostCard = document.createElement("div");
    hostCard.className = "speaker-card";
    hostCard.innerHTML = `
        <div class="speaker-header">
            <span class="speaker-tag">${escapeHtml(hostName)}</span>
            <span class="status-badge status-completed">Você (microfone)</span>
        </div>
        <p class="speaker-note">Reconhecido pela faixa do seu microfone no OBS. Salvar amostras ajuda quando a gravação tem uma faixa só.</p>
    `;
    hostCard.appendChild(createVoicePreviewControl(videoId, hostName, null));
    const btnHost = document.createElement("button");
    btnHost.className = "btn-secondary";
    btnHost.style.width = "100%";
    btnHost.textContent = `Salvar amostras da voz de ${hostName}`;
    btnHost.addEventListener("click", () => harvestHostSamples(videoId, btnHost));
    hostCard.appendChild(btnHost);
    grid.appendChild(hostCard);

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

        let badgeHtml = `<span class="speaker-suggestion">Ainda sem nome</span>`;
        if (confirmedManually && confirmedName) {
            badgeHtml = `<span class="status-badge status-completed">Confirmado: ${escapeHtml(confirmedName)}</span>`;
        } else if (isKnown) {
            badgeHtml = `<span class="status-badge status-completed">Reconhecido (confiança ${Math.round((info.avg_confidence || 0.7) * 100)}%)</span>`;
        } else if (deduced) {
            badgeHtml = `<span class="speaker-suggestion">A IA acha que é: ${escapeHtml(deduced)}</span>`;
        }

        const segInfo = info.segment_count ? ` (${info.segment_count} falas)` : "";
        const displayTag = (confirmedManually && confirmedName && confirmedName.toLowerCase() !== tag.toLowerCase())
            ? `${confirmedName} ← ${tag}`
            : tag;

        const card = document.createElement("div");
        card.className = "speaker-card";
        card.innerHTML = `
            <div class="speaker-header">
                <span class="speaker-tag">${escapeHtml(displayTag.replace(/^Voz_(\d+)/, "Voz $1"))}<span class="speaker-count">${escapeHtml(segInfo)}</span></span>
                ${badgeHtml}
            </div>
            <p class="speaker-quote">“${escapeHtml((info.text || "").slice(0, 90))}${(info.text || "").length > 90 ? "..." : ""}”</p>
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
            ? "Salvar mais amostras desta voz"
            : "Confirmar nome";
        btn.addEventListener("click", () => confirmSpeakerName(videoId, tag, input, btn));

        card.appendChild(input);
        card.appendChild(btn);
        grid.appendChild(card);
    });
}

async function harvestHostSamples(videoId, btnEl) {
    const hostName = studioConfig.host_name || "Host";
    try {
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.textContent = `Salvando amostras de ${hostName}...`;
        }
        const res = await fetch(`/api/speakers/harvest-host/${videoId}`, { method: "POST" });
        const d = await res.json();
        if (d.success) {
            const countSaved = (d.harvest && d.harvest.samples_saved) ? d.harvest.samples_saved.length : 0;
            alert(`${countSaved} amostra(s) da voz de ${hostName} salvas.`);
        } else {
            alert(`Erro ao salvar amostras de ${hostName}: ` + (d.detail || "Falha desconhecida"));
        }
    } catch (e) {
        alert(`Erro ao salvar amostras de ${hostName}: ` + e);
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = `Salvar amostras da voz de ${hostName}`;
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
            btn.textContent = "Gravando capítulos...";
        }
        const res = await fetch(`/api/embed-chapters/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: mode })
        });
        const d = await res.json();
        if (res.ok && d.success) {
            alert(`${d.chapters_count} capítulo(s) gravados no arquivo.\n\nArquivo: ${d.filename}\nMétodo: ${d.method}`);
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
            btnEl.textContent = "Salvando...";
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
            alert(`Pronto: ${d.updated_segments || 0} falas agora aparecem como ${d.friend_name || name} na legenda, e ${countSaved} amostra(s) da voz foram salvas para reconhecer nas próximas gravações.`);
            fetchVideoDetails(videoId, true);
        } else {
            alert("Erro ao confirmar voz: " + (d.detail || "Falha desconhecida"));
        }
    } catch (e) {
        alert("Erro ao confirmar voz: " + e);
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = "Confirmar nome";
        }
    }
}

