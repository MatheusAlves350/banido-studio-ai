// Estilos de título e botões "Mais curto" / "Outro tom" (B9). Os cinco títulos vêm nesta ordem.
const TITLE_STYLE_LABELS = ["Engraçado", "Curioso", "Direto", "Livre", "Livre"];

async function regenerateTitle(index, mode, button) {
    const status = document.getElementById("title-regen-status");
    if (!activeVideoId) return;
    const engine = document.getElementById("select-engine").value || "gemini";
    const modelSel = document.getElementById("select-active-model");
    const model = modelSel ? modelSel.value : "";
    if (status) status.textContent = "Pedindo um novo título à IA...";
    if (button) button.disabled = true;
    try {
        const res = await fetch(`/api/titles/regenerate/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index, mode, engine, model })
        });
        const d = await res.json();
        if (!res.ok) {
            if (status) status.textContent = d.detail || "Não foi possível refazer o título.";
            return;
        }
        if (status) status.textContent = "Título atualizado.";
        await fetchVideoDetails(activeVideoId, true);
    } catch (e) {
        if (status) status.textContent = "Não foi possível falar com a IA agora.";
    } finally {
        if (button) button.disabled = false;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("title-options-list");
    if (!list) return;
    list.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-title-action]");
        if (!btn) return;
        ev.preventDefault();
        ev.stopPropagation();
        regenerateTitle(parseInt(btn.dataset.titleIndex, 10), btn.dataset.titleAction, btn);
    });
});
