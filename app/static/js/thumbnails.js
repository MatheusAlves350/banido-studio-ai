// Sugestão de miniatura (B6): quadros 1280x720 nos picos de hype e dois textos curtos para escolher.
function renderThumbnails(t) {
    const grid = document.getElementById("thumbnail-grid");
    const texts = document.getElementById("thumbnail-texts");
    if (!grid || !texts) return;
    grid.innerHTML = "";
    texts.innerHTML = "";
    (t.frames || []).forEach(f => {
        const fig = document.createElement("figure");
        fig.className = "thumbnail-item";
        const img = document.createElement("img");
        img.src = `${f.url}?t=${Date.now()}`;
        img.alt = `Quadro de ${f.time_label}`;
        img.loading = "lazy";
        const cap = document.createElement("figcaption");
        const a = document.createElement("a");
        a.href = f.url;
        a.download = f.name;
        a.textContent = "Baixar";
        cap.append(`${f.time_label} · `, a);
        fig.append(img, cap);
        grid.appendChild(fig);
    });
    (t.texts || []).forEach((txt, i) => {
        const label = document.createElement("label");
        label.className = "thumbnail-text-option";
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "thumbnail-text";
        radio.checked = i === 0;
        const span = document.createElement("span");
        span.textContent = txt;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-secondary btn-sm";
        btn.textContent = "Copiar";
        btn.addEventListener("click", async (ev) => {
            ev.preventDefault();
            try { await navigator.clipboard.writeText(txt); btn.textContent = "Copiado"; } catch (_) { btn.textContent = "Selecione e copie"; }
            setTimeout(() => { btn.textContent = "Copiar"; }, 1500);
        });
        label.append(radio, span, btn);
        texts.appendChild(label);
    });
}

async function suggestThumbnails() {
    if (!activeVideoId) return;
    const status = document.getElementById("thumbnail-status");
    const btn = document.getElementById("btn-suggest-thumbnails");
    const engine = document.getElementById("select-engine").value;
    const modelSel = document.getElementById("select-active-model");
    status.textContent = "Escolhendo quadros... pode levar alguns segundos.";
    btn.disabled = true;
    try {
        const res = await fetch(`/api/thumbnails/${activeVideoId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ engine: engine === "manual_claude" ? "" : engine, model: modelSel ? modelSel.value : "" })
        });
        const d = await res.json();
        if (!res.ok) {
            status.textContent = d.detail || "Não foi possível gerar as miniaturas.";
            return;
        }
        renderThumbnails(d);
        status.textContent = d.note || "Pronto.";
    } catch (_) {
        status.textContent = "Não foi possível gerar as miniaturas agora.";
    } finally {
        btn.disabled = false;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("btn-suggest-thumbnails");
    if (btn) btn.addEventListener("click", suggestThumbnails);
});
