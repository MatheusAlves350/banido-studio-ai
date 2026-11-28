// C1: pré-visualização do corte antes de exportar.
// A prévia é uma cópia leve (360p) gerada sob demanda pelo servidor; o arquivo original nunca é alterado.
const CLIP_PREVIEW_FRAME_SEC = 1 / 30;   // aproximação de um quadro (gravações de 30 fps ou mais)
const CLIP_PREVIEW_EDGE_SEC = 5;         // folga para testar as bordas
const CLIP_PREVIEW_MAX_SEC = 120;        // limite do servidor por prévia

function openClipPreview(opts) {
    const { videoId, title, totalDurationSec = 0, onApply, suggestion } = opts;
    let startSec = opts.startSec;
    let endSec = opts.endSec;
    let windowStart = 0;
    let objUrl = null;

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Pré-visualizar o corte");

    const card = document.createElement("div");
    card.className = "modal-card clip-preview-card";
    overlay.appendChild(card);

    const header = document.createElement("div");
    header.className = "modal-header";
    const h = document.createElement("h3");
    h.textContent = `Pré-visualizar: ${title || "Corte"}`;
    const btnClose = document.createElement("button");
    btnClose.type = "button";
    btnClose.className = "btn-close";
    btnClose.textContent = "Fechar";
    header.append(h, btnClose);

    const body = document.createElement("div");
    body.className = "modal-body";

    const video = document.createElement("video");
    video.controls = true;
    video.className = "clip-preview-video";
    video.preload = "none";

    const status = document.createElement("p");
    status.className = "field-hint";
    status.textContent = "Escolha uma das prévias abaixo. A prévia é uma cópia leve e fica só no disco temporário.";

    const rangeLabel = document.createElement("p");
    rangeLabel.className = "clip-preview-range";

    const timeline = document.createElement("div");
    timeline.className = "clip-preview-timeline";
    timeline.setAttribute("aria-label", "Linha do tempo das falas no trecho da prévia");

    const mkBtn = (label, onClick, extra = "btn-tweak") => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = extra;
        b.textContent = label;
        b.addEventListener("click", onClick);
        return b;
    };

    const cap = () => (totalDurationSec > 0 ? totalDurationSec : Infinity);
    const moveStart = (d) => {
        startSec = Math.min(endSec - 1, Math.max(0, startSec + d));
        refresh();
    };
    const moveEnd = (d) => {
        endSec = Math.max(startSec + 1, Math.min(cap(), endSec + d));
        refresh();
    };

    const startRow = document.createElement("div");
    startRow.className = "highlight-tweak-row";
    startRow.append("Início:",
        mkBtn("-1s", () => moveStart(-1)), mkBtn("-1 quadro", () => moveStart(-CLIP_PREVIEW_FRAME_SEC)),
        mkBtn("+1 quadro", () => moveStart(CLIP_PREVIEW_FRAME_SEC)), mkBtn("+1s", () => moveStart(1)),
        mkBtn("Início = ponto atual", () => {
            if (video.src) { startSec = Math.min(endSec - 1, windowStart + video.currentTime); refresh(); }
        }));
    const endRow = document.createElement("div");
    endRow.className = "highlight-tweak-row";
    endRow.append("Fim:",
        mkBtn("-1s", () => moveEnd(-1)), mkBtn("-1 quadro", () => moveEnd(-CLIP_PREVIEW_FRAME_SEC)),
        mkBtn("+1 quadro", () => moveEnd(CLIP_PREVIEW_FRAME_SEC)), mkBtn("+1s", () => moveEnd(1)),
        mkBtn("Fim = ponto atual", () => {
            if (video.src) { endSec = Math.max(startSec + 1, windowStart + video.currentTime); refresh(); }
        }));

    const loadRow = document.createElement("div");
    loadRow.className = "highlight-tweak-row";
    const loadBtns = [];
    const addLoad = (label, getRange) => {
        const b = mkBtn(label, () => loadPreview(getRange()), "btn-secondary btn-sm");
        loadBtns.push(b);
        loadRow.appendChild(b);
    };
    addLoad("Testar o início (5 s antes e depois)", () => [startSec - CLIP_PREVIEW_EDGE_SEC, startSec + CLIP_PREVIEW_EDGE_SEC]);
    addLoad("Testar o fim (5 s antes e depois)", () => [endSec - CLIP_PREVIEW_EDGE_SEC, endSec + CLIP_PREVIEW_EDGE_SEC]);
    addLoad("Ver o trecho todo", () => [startSec - CLIP_PREVIEW_EDGE_SEC, endSec + CLIP_PREVIEW_EDGE_SEC]);

    const notice = document.createElement("p");
    notice.className = "field-hint";
    notice.textContent = "Aviso: sem a opção de corte exato, o corte real começa no quadro-chave anterior e pode ter 1 ou 2 segundos a mais no início. A prévia mostra o ponto pedido.";

    const footer = document.createElement("div");
    footer.className = "modal-footer clip-preview-footer";

    function close() {
        if (objUrl) { try { URL.revokeObjectURL(objUrl); } catch (_) {} }
        document.removeEventListener("keydown", onKey);
        overlay.remove();
    }
    function onKey(ev) { if (ev.key === "Escape") close(); }

    function drawTimeline(w0, w1) {
        timeline.innerHTML = "";
        const span = Math.max(1, w1 - w0);
        const place = (el, a, b) => {
            el.style.left = `${Math.max(0, (a - w0) / span * 100)}%`;
            el.style.width = `${Math.max(0.4, (Math.min(b, w1) - Math.max(a, w0)) / span * 100)}%`;
            timeline.appendChild(el);
        };
        (typeof activeSegments !== "undefined" ? activeSegments : []).forEach(seg => {
            if (seg.end < w0 || seg.start > w1) return;
            const el = document.createElement("span");
            el.className = "clip-preview-speech";
            el.title = `${formatSecondsToHHMMSS(seg.start)} [${seg.speaker || "Discord"}] ${seg.text || ""}`;
            place(el, seg.start, seg.end);
        });
        [[startSec, "início"], [endSec, "fim"]].forEach(([t, name]) => {
            if (t < w0 || t > w1) return;
            const el = document.createElement("span");
            el.className = "clip-preview-marker";
            el.title = `Marca de ${name}`;
            place(el, t, t);
        });
    }

    function refresh() {
        rangeLabel.textContent = `${formatSecondsToHHMMSS(startSec)} até ${formatSecondsToHHMMSS(endSec)} (${Math.max(1, Math.round(endSec - startSec))} s)`;
        drawTimeline(windowStart, windowStart + (video.duration && isFinite(video.duration) ? video.duration : CLIP_PREVIEW_EDGE_SEC * 2));
    }

    async function loadPreview([a, b]) {
        a = Math.max(0, a);
        b = Math.min(cap(), Math.max(a + 2, b));
        if (b - a > CLIP_PREVIEW_MAX_SEC) {
            b = a + CLIP_PREVIEW_MAX_SEC;
            status.textContent = "O trecho é longo: a prévia mostra os primeiros 2 minutos.";
        } else {
            status.textContent = "Gerando a prévia, isso leva alguns segundos...";
        }
        loadBtns.forEach(x => { x.disabled = true; });
        try {
            const res = await fetch(`/api/video/preview/${videoId}?start=${a.toFixed(2)}&end=${b.toFixed(2)}`);
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const blob = await res.blob();
            if (objUrl) { try { URL.revokeObjectURL(objUrl); } catch (_) {} }
            objUrl = URL.createObjectURL(blob);
            windowStart = a;
            video.src = objUrl;
            video.onloadedmetadata = refresh;
            status.textContent = `Prévia de ${formatSecondsToHHMMSS(a)} a ${formatSecondsToHHMMSS(b)}.`;
            video.play().catch(() => {});
        } catch (e) {
            status.textContent = "Não foi possível gerar a prévia: " + e.message;
        } finally {
            loadBtns.forEach(x => { x.disabled = false; });
        }
    }

    const btnApply = mkBtn("Usar estes tempos", () => {
        if (typeof onApply === "function") onApply(startSec, endSec);
        close();
    }, "btn-primary");
    footer.appendChild(btnApply);
    if (suggestion && Number.isFinite(suggestion.start) && Number.isFinite(suggestion.end)) {
        footer.prepend(mkBtn("Usar a sugestão", () => {
            startSec = suggestion.start;
            endSec = suggestion.end;
            refresh();
        }, "btn-secondary"));
    }

    btnClose.addEventListener("click", close);
    overlay.addEventListener("click", (ev) => { if (ev.target === overlay) close(); });
    document.addEventListener("keydown", onKey);

    body.append(video, status, rangeLabel, timeline, startRow, endRow, loadRow, notice);
    card.append(header, body, footer);
    document.body.appendChild(overlay);
    refresh();
    loadPreview([startSec - CLIP_PREVIEW_EDGE_SEC, endSec + CLIP_PREVIEW_EDGE_SEC]);
}
