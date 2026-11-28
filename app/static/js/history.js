// =========================================================================
// HISTÓRICO DE EXECUÇÕES E LOGS
// =========================================================================

async function openHistoryModal() {
    document.getElementById("history-modal").style.display = "flex";
    await loadHistoryList(true);
}

async function loadHistoryList(autoSelectFirst = false) {
    const pane = document.getElementById("history-list-pane");
    try {
        const res = await fetch("/api/history?limit=60");
        const data = await res.json();
        const jobs = data.jobs || [];

        if (jobs.length === 0) {
            pane.innerHTML = `<div style="padding:20px; text-align:center; color:var(--text-secondary); font-size:13px;">Nenhuma execução registrada no histórico ainda.</div>`;
            document.getElementById("history-log-viewer").innerHTML = "Nenhum job registrado.";
            return;
        }

        pane.innerHTML = "";
        const badgeStyle = {
            running: `<span class="status-badge status-processing">Em andamento</span>`,
            completed: `<span class="status-badge status-completed">Concluído</span>`,
            error: `<span class="status-badge status-error">Erro</span>`,
            cancelled: `<span class="status-badge status-warn">Cancelado</span>`,
            interrupted: `<span class="status-badge status-unprocessed">Interrompido</span>`
        };

        jobs.forEach(j => {
            const card = document.createElement("div");
            card.className = `history-item ${activeHistoryJobId === j.id ? "active" : ""}`;
            card.dataset.jobId = String(j.id);
            card.innerHTML = `
                <div class="history-item-top">
                    <span style="color:var(--accent);">Job #${j.id}</span>
                    ${badgeStyle[j.status] || j.status}
                </div>
                <div class="history-item-file" title="${escapeHtml(j.filename)}">${escapeHtml(j.filename)}</div>
                <div class="history-item-sub">
                    ${escapeHtml(j.started_at || "")}, duração ${formatDuration(j.elapsed_sec)}<br>
                    IA: ${escapeHtml(j.engine)} (${escapeHtml(j.model || "padrão")})<br>
                    <span style="color:${j.status === 'error' ? '#f87171' : '#94a3b8'};">
                        ${escapeHtml((j.error_friendly || j.error_detail || j.last_step || "").slice(0, 110))}
                    </span>
                </div>
            `;
            card.addEventListener("click", () => selectHistoryJob(j.id));
            pane.appendChild(card);
        });

        if (autoSelectFirst && (!activeHistoryJobId || !jobs.some(j => j.id === activeHistoryJobId))) {
            selectHistoryJob(jobs[0].id);
        } else if (activeHistoryJobId) {
            selectHistoryJob(activeHistoryJobId);
        }
    } catch (e) {
        pane.innerHTML = `<div style="padding:16px;color:var(--danger);">Erro ao carregar histórico: ${e}</div>`;
    }
}

async function selectHistoryJob(jobId) {
    activeHistoryJobId = jobId;
    document.querySelectorAll(".history-item").forEach(el => {
        el.classList.toggle("active", el.dataset.jobId === String(jobId));
    });
    const viewer = document.getElementById("history-log-viewer");
    const headerTitle = document.getElementById("history-selected-title");
    headerTitle.innerText = `Carregando log do Job #${jobId}...`;

    try {
        const res = await fetch(`/api/logs/job/${jobId}?tail=1000`);
        const d = await res.json();
        const j = d.job;
        headerTitle.innerText = `Job #${j.id} — ${j.filename} (${j.status.toUpperCase()} • ${formatDuration(j.elapsed_sec)})`;
        viewer.innerHTML = colorizeLogHtml(d.log_text || "(Arquivo de log vazio)");
        viewer.scrollTop = viewer.scrollHeight;
    } catch (e) {
        viewer.innerText = "Erro ao abrir log: " + e;
    }
}

