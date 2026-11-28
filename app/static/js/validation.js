// Validador de metadados do YouTube (B4): mostra avisos sobre título, descrição, tags e capítulos.
let validationTimer = null;

async function refreshMetadataValidation(videoId) {
    const box = document.getElementById("yt-validation");
    if (!box) return;
    const id = videoId || activeVideoId;
    if (!id) {
        box.hidden = true;
        return;
    }
    const titleInput = document.getElementById("custom-title-input");
    const title = titleInput ? titleInput.value.trim() : "";
    try {
        const res = await fetch(`/api/validate-metadata/${id}?title=${encodeURIComponent(title)}`);
        if (id !== activeVideoId || !res.ok) return;
        const d = await res.json();
        box.innerHTML = "";
        if (!d.issues.length) {
            box.className = "validation-box validation-ok";
            box.textContent = "Título, descrição, tags e capítulos estão dentro das regras do YouTube.";
            box.hidden = false;
            return;
        }
        box.className = "validation-box " + (d.ok ? "validation-warn" : "validation-error");
        const list = document.createElement("ul");
        d.issues.forEach(i => {
            const li = document.createElement("li");
            li.textContent = (i.level === "erro" ? "Corrigir: " : "Atenção: ") + i.message;
            list.appendChild(li);
        });
        box.appendChild(list);
        box.hidden = false;
    } catch (_) {
        box.hidden = true;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const titleInput = document.getElementById("custom-title-input");
    if (titleInput) {
        titleInput.addEventListener("input", () => {
            clearTimeout(validationTimer);
            validationTimer = setTimeout(() => refreshMetadataValidation(), 400);
        });
    }
});
