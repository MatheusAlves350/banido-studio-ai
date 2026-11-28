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
        btnSetStart.textContent = "Início";
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
        btnSetEnd.textContent = "Fim";
        btnSetEnd.title = `Definir ${tsEnd} como fim do Corte Manual`;
        btnSetEnd.addEventListener("click", () => {
            const endInp = document.getElementById("manual-cut-end");
            if (endInp) endInp.value = formatSecondsToHHMMSS((seg.end || seg.start || 0) + 2);
            document.getElementById("manual-cut-end")?.scrollIntoView({ behavior: "smooth", block: "center" });
        });

        const idx = activeSegments.indexOf(seg);
        const edit = getSegmentEdit(seg, idx);
        if (edit) {
            const mark = document.createElement("span");
            mark.className = "tag-badge";
            mark.textContent = "editada";
            mark.title = `Original: ${edit.original}`;
            row.querySelector(".transcript-row-text").appendChild(mark);
        }
        const btnEdit = document.createElement("button");
        btnEdit.type = "button";
        btnEdit.className = "btn-tweak";
        btnEdit.textContent = "Editar";
        btnEdit.title = "Corrigir o texto desta fala";
        btnEdit.addEventListener("click", () => startTranscriptEdit(row, seg, idx));
        actions.appendChild(btnEdit);
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

