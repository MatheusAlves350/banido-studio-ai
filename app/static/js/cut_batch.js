// C3: exportar vários cortes de uma vez, em fila (um por vez, em ordem cronológica).
let batchCards = [];
let batchJobId = null;
let batchPoll = null;

function resetBatchCards() {
    batchCards = [];
    updateBatchButton();
}

// Chamado por renderHighlights para cada card: `getRange` devolve o início e o fim atuais do card.
function registerBatchCard(checkbox, getRange, title) {
    batchCards.push({ checkbox, getRange, title });
    checkbox.addEventListener("change", updateBatchButton);
}

function selectedBatchCards() {
    return batchCards.filter(c => c.checkbox.checked);
}

function updateBatchButton() {
    const btn = document.getElementById("btn-batch-export");
    if (!btn) return;
    const n = selectedBatchCards().length;
    btn.textContent = `Exportar selecionados (${n})`;
    btn.disabled = n === 0 || Boolean(batchPoll);
}

function setupBatchCutBar() {
    document.getElementById("btn-batch-select-all")?.addEventListener("click", () => {
        const all = batchCards.length > 0 && batchCards.every(c => c.checkbox.checked);
        batchCards.forEach(c => { c.checkbox.checked = !all; });
        updateBatchButton();
    });
    document.getElementById("btn-batch-export")?.addEventListener("click", startBatchCut);
    document.getElementById("btn-batch-cancel")?.addEventListener("click", cancelBatchCut);
}

async function startBatchCut() {
    if (!activeVideoId) return;
    const picked = selectedBatchCards();
    if (picked.length === 0) return;
    const items = picked.map(c => {
        const r = c.getRange();
        return { start_sec: r.startSec, end_sec: r.endSec, title: c.title };
    });
    try {
        const res = await fetch("/api/cut/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                video_id: activeVideoId,
                items,
                boost_discord: true,
                keep_all_tracks: Boolean(document.getElementById("chk-keep-all-tracks")?.checked),
                exact_cut: Boolean(document.getElementById("chk-exact-cut")?.checked)
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        batchJobId = data.job_id;
        renderBatchStatus(data);
        batchPoll = setInterval(pollBatchCut, 2000);
        updateBatchButton();
    } catch (e) {
        alert("Não foi possível iniciar a fila de cortes: " + e.message);
    }
}

async function pollBatchCut() {
    if (!batchJobId) return;
    try {
        const res = await fetch(`/api/cut/batch/${batchJobId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderBatchStatus(data);
        if (data.state !== "running") {
            clearInterval(batchPoll);
            batchPoll = null;
            updateBatchButton();
        }
    } catch (_) {
        clearInterval(batchPoll);
        batchPoll = null;
        updateBatchButton();
    }
}

async function cancelBatchCut() {
    if (!batchJobId) return;
    try {
        await fetch(`/api/cut/batch/${batchJobId}/cancel`, { method: "POST" });
    } catch (_) {}
}

const BATCH_STATUS_LABEL = { pending: "Aguardando", running: "Exportando...", done: "Pronto", error: "Falhou", skipped: "Cancelado" };

function renderBatchStatus(data) {
    const box = document.getElementById("batch-cut-status");
    const list = document.getElementById("batch-cut-list");
    const cancel = document.getElementById("btn-batch-cancel");
    if (!box || !list) return;
    const running = data.state === "running";
    if (cancel) cancel.hidden = !running;
    const end = { running: "", done: " Fila concluída.", cancelled: " Fila cancelada." }[data.state] || "";
    box.textContent = `${data.finished} de ${data.total} cortes processados (${data.ok} com sucesso, ${data.failed} com erro).${end}`;
    list.innerHTML = "";
    data.items.forEach(it => {
        const li = document.createElement("li");
        let text = `${formatSecondsToHHMMSS(it.start_sec)} até ${formatSecondsToHHMMSS(it.end_sec)} - ${it.title}: ${BATCH_STATUS_LABEL[it.status] || it.status}`;
        if (it.status === "done" && it.result) {
            text += ` (${it.result.filename})${describeCutVerification(it.result.verification).replace(/\n+/g, " ")}`;
        }
        if (it.error) text += ` - ${it.error}`;
        li.textContent = text;
        list.appendChild(li);
    });
}
