// =========================================================================
// GLOSSÁRIO DO GRUPO (apelidos, gírias e nomes que o Whisper costuma errar)
// =========================================================================

async function loadGlossaryEditor() {
    const box = document.getElementById("input-glossary");
    const feedback = document.getElementById("glossary-feedback");
    if (!box) return;
    try {
        const res = await fetch("/api/glossary");
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        box.value = (data.terms || []).join("\n");
        if (feedback) feedback.textContent = `${data.count} de ${data.max_terms} termos.`;
    } catch (e) {
        console.error("Erro ao carregar o glossário:", e);
        if (feedback) feedback.textContent = "Erro ao carregar o glossário: " + e.message;
    }
}

async function saveGlossaryEditor() {
    const box = document.getElementById("input-glossary");
    const feedback = document.getElementById("glossary-feedback");
    if (!box) return;
    try {
        const res = await fetch("/api/glossary", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: box.value })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        box.value = data.terms.join("\n");
        if (feedback) feedback.textContent = `Salvo: ${data.count} de ${data.max_terms} termos. Vale a partir da próxima transcrição.`;
    } catch (e) {
        if (feedback) feedback.textContent = "Erro ao salvar o glossário: " + e.message;
    }
}

document.getElementById("btn-open-settings")?.addEventListener("click", loadGlossaryEditor);
document.getElementById("btn-save-glossary")?.addEventListener("click", saveGlossaryEditor);
