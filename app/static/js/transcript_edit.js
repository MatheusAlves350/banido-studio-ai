// =========================================================================
// EDITOR DE LEGENDA: corrige o texto de uma fala e refaz o .srt (A6)
// =========================================================================

let transcriptEdits = {};

function getSegmentEdit(seg, idx) {
    if (idx < 0) return null;
    const edit = transcriptEdits[String(idx)];
    if (!edit) return null;
    const segStart = Number(seg?.start);
    const editStart = Number(edit.start);
    if (!Number.isFinite(segStart) || !Number.isFinite(editStart)) return null;
    return Math.abs(segStart - editStart) <= 0.5 ? edit : null;
}

function startTranscriptEdit(row, seg, idx) {
    if (idx < 0) return;
    const textBox = row.querySelector(".transcript-row-text");
    const actions = row.querySelector(".transcript-row-actions");
    const edit = getSegmentEdit(seg, idx);
    const spk = escapeHtml(seg.speaker || "Discord");
    const ts = escapeHtml(formatSecondsToHHMMSS(seg.start));

    const box = document.createElement("div");
    box.className = "transcript-edit-box";
    box.innerHTML = `
        <div><span class="chapter-ts">${ts}</span> <span class="transcript-row-speaker">[${spk}]</span></div>
        <textarea aria-label="Texto da fala"></textarea>
        <div class="transcript-edit-actions">
            <button type="button" class="btn-primary btn-sm" data-act="save">Salvar</button>
            <button type="button" class="btn-secondary btn-sm" data-act="cancel">Cancelar</button>
            ${edit ? '<button type="button" class="btn-secondary btn-sm" data-act="undo">Voltar ao original</button>' : ""}
        </div>
        <small class="field-hint" data-role="msg"></small>`;
    const area = box.querySelector("textarea");
    area.value = seg.text || "";
    const msg = box.querySelector("[data-role=msg]");

    textBox.style.display = "none";
    actions.style.display = "none";
    row.insertBefore(box, textBox);
    area.focus();

    box.addEventListener("click", async (ev) => {
        const act = ev.target?.dataset?.act;
        if (!act) return;
        if (act === "cancel") { renderTranscriptResults(); return; }
        const url = `/api/video/${activeVideoId}/transcript/${act === "save" ? "edit" : "undo"}`;
        const payload = { index: idx, start: seg.start };
        if (act === "save") payload.text = area.value;
        msg.textContent = "Salvando...";
        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            transcriptEdits = data.edits || {};
            const e = getSegmentEdit(seg, idx);
            seg.text = typeof data.text === "string" ? data.text : (e ? e.text : (edit ? edit.original : area.value));
            if (typeof data.youtube_description === "string") {
                const ytBox = document.getElementById("yt-description-box");
                if (ytBox) ytBox.value = data.youtube_description;
            }
            renderTranscriptResults();
            showGlossarySuggestions(data.glossary_suggestions || []);
        } catch (err) {
            msg.textContent = "Erro: " + err.message;
        }
    });
}

function showGlossarySuggestions(terms) {
    const el = document.getElementById("glossary-suggestions");
    if (!el) return;
    el.innerHTML = "";
    el.style.display = terms.length ? "block" : "none";
    terms.slice(0, 5).forEach(term => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-tweak";
        btn.textContent = `Adicionar "${term}" ao glossário`;
        btn.title = "Você corrigiu esta palavra mais de uma vez. Ela ajuda o Whisper nas próximas transcrições.";
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            try {
                const cur = await (await fetch("/api/glossary")).json();
                const res = await fetch("/api/glossary", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text: [...(cur.terms || []), term].join("\n") })
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                btn.remove();
                if (!el.children.length) el.style.display = "none";
            } catch (err) {
                btn.disabled = false;
                btn.textContent = "Erro ao salvar no glossário";
            }
        });
        el.appendChild(btn);
    });
}
