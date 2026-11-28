// Estimativa de tokens antes de enviar à IA (B10): texto curto com cor (verde, amarelo, vermelho).
const TOKEN_LEVEL_TEXT = {
    ok: "dentro do limite",
    atencao: "perto do limite",
    estoura: "pode passar do limite"
};

function formatTokenCount(n) {
    return Number(n || 0).toLocaleString("pt-BR");
}

async function refreshTokenEstimate() {
    const box = document.getElementById("ai-token-estimate");
    if (!box) return;
    if (!activeVideoId) {
        box.textContent = "";
        return;
    }
    const engine = document.getElementById("select-engine").value || "gemini";
    const modelSel = document.getElementById("select-active-model");
    const model = modelSel ? modelSel.value : "";
    const videoId = activeVideoId;
    try {
        const res = await fetch(`/api/ai/estimate/${videoId}?engine=${encodeURIComponent(engine)}&model=${encodeURIComponent(model)}`);
        if (videoId !== activeVideoId) return;
        if (!res.ok) {
            box.textContent = "";
            return;
        }
        const d = await res.json();
        const pct = Math.round((d.total_tokens / d.limit_tokens) * 100);
        box.className = `field-hint token-estimate token-${d.level}`;
        box.textContent =
            `Envio estimado: cerca de ${formatTokenCount(d.total_tokens)} tokens (${pct}% do limite de ${formatTokenCount(d.limit_tokens)}), ${TOKEN_LEVEL_TEXT[d.level] || ""}. Valor aproximado.` +
            (d.suggestion ? ` ${d.suggestion}` : "");
    } catch (_) {
        box.textContent = "";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const engineSel = document.getElementById("select-engine");
    const modelSel = document.getElementById("select-active-model");
    if (engineSel) engineSel.addEventListener("change", () => setTimeout(refreshTokenEstimate, 300));
    if (modelSel) modelSel.addEventListener("change", refreshTokenEstimate);
});
