// =========================================================================
// EDITOR DE LEGENDA: corrige o texto de uma fala e refaz o .srt (A6)
// =========================================================================

let transcriptEdits = {};

function startTranscriptEdit(row, seg, idx) {
    if (idx < 0) return;
    const textBox = row.querySelector(".transcript-row-text");
    const actions = row.querySelector(".transcript-row-actions");
    const edit = transcriptEdits[String(idx)];
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
            const e = transcriptEdits[String(idx)];
            seg.text = e ? e.text : (edit ? edit.original : area.value);
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
            } catch (err) {
                btn.disabled = false;
                btn.textContent = "Erro ao salvar no glossário";
            }
        });
        el.appendChild(btn);
    });
}
