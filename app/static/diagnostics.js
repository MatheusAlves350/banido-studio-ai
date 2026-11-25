// Relatório de diagnóstico e envio seguro de logs para análise de bugs.
// Usa window.fetch (já com X-Banido-Token injetado por app.js).
(function () {
    "use strict";

    const state = { jobId: null, videoId: null, report: null };

    function $(id) { return document.getElementById(id); }

    function buildDom() {
        // Botão no banner de erro (quando um processamento falha)
        const errBtn = $("btn-show-log-from-err");
        if (errBtn && !$("btn-report-bug-from-err")) {
            const b = document.createElement("button");
            b.id = "btn-report-bug-from-err";
            b.type = "button";
            b.className = "btn-secondary";
            b.textContent = "Relatar um problema";
            b.addEventListener("click", () => openModal({ videoId: typeof activeVideoId !== "undefined" ? activeVideoId : null }));
            errBtn.parentElement.insertBefore(b, errBtn.nextSibling);
        }

        // Botão no cabeçalho do modal de Histórico de Logs
        const copyHistBtn = $("btn-copy-history-log");
        if (copyHistBtn && !$("btn-report-bug-from-history")) {
            const b = document.createElement("button");
            b.id = "btn-report-bug-from-history";
            b.type = "button";
            b.className = "btn-secondary btn-sm";
            b.textContent = "Relatar um problema";
            b.title = "Gerar relatório sanitizado desta execução para enviar ao desenvolvedor";
            b.addEventListener("click", () => openModal({ jobId: typeof activeHistoryJobId !== "undefined" ? activeHistoryJobId : null }));
            copyHistBtn.parentElement.insertBefore(b, copyHistBtn);
        }

        // Botão no rodapé da barra lateral
        const footer = document.querySelector(".sidebar-footer");
        if (footer && !$("btn-open-diagnostics")) {
            const b = document.createElement("button");
            b.id = "btn-open-diagnostics";
            b.type = "button";
            b.className = "btn-settings";
            b.style.marginTop = "8px";
            b.style.borderColor = "rgba(245, 158, 11, 0.35)";
            b.textContent = "Relatar um problema";
            b.addEventListener("click", () => openModal({ videoId: typeof activeVideoId !== "undefined" ? activeVideoId : null }));
            const updBtn = $("upd-version-btn");
            if (updBtn) footer.insertBefore(b, updBtn);
            else footer.appendChild(b);
        }

        if (!$("diag-modal")) {
            const modal = document.createElement("div");
            modal.id = "diag-modal";
            modal.className = "modal-overlay";
            modal.style.display = "none";
            modal.innerHTML = `
                <div class="modal-card modal-wide" role="dialog" aria-modal="true" aria-labelledby="diag-title" style="max-width: 780px;">
                    <div class="modal-header">
                        <h3 id="diag-title">Relatório de diagnóstico</h3>
                        <button type="button" id="diag-close" class="btn-close">✕</button>
                    </div>
                    <div class="modal-body">
                        <div id="diag-privacy-box" style="background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.4); color: #fcd34d; padding: 10px 12px; border-radius: 8px; font-size: 12.5px; line-height: 1.45; margin-bottom: 12px;">
                            <strong>⚠️ Aviso de Privacidade:</strong>
                            <span id="diag-privacy-text">Chaves de API, tokens, senhas e caminhos de usuário são removidos automaticamente. Revise o conteúdo abaixo antes de enviar e apague qualquer informação pessoal que não queira compartilhar.</span>
                        </div>
                        <div style="display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 10px; font-size: 12.5px;">
                            <label style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="checkbox" id="diag-strip-transcripts" checked>
                                <span>Ocultar falas transcritas (<code>[FALA #...]</code>) — recomendado</span>
                            </label>
                            <label style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="checkbox" id="diag-redact-filenames">
                                <span>Ocultar nomes de arquivos (<code>.mkv</code> / <code>.srt</code>)</span>
                            </label>
                        </div>
                        <textarea id="diag-report-text" rows="15" style="width: 100%; font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.45; background: rgba(15, 23, 42, 0.75); color: #e2e8f0; border: 1px solid var(--border-color); border-radius: 8px; padding: 10px; resize: vertical;"></textarea>
                    </div>
                    <div class="modal-footer" style="display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end;">
                        <button type="button" id="diag-download-btn" class="btn-secondary">💾 Baixar .txt Sanitizado</button>
                        <button type="button" id="diag-copy-btn" class="btn-secondary">📋 Copiar Relatório</button>
                        <button type="button" id="diag-github-btn" class="btn-primary">🐙 Abrir Issue no GitHub</button>
                    </div>
                </div>`;
            document.body.appendChild(modal);

            modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
            $("diag-close").addEventListener("click", closeModal);
            $("diag-strip-transcripts").addEventListener("change", fetchReport);
            $("diag-redact-filenames").addEventListener("change", fetchReport);
            $("diag-copy-btn").addEventListener("click", copyReport);
            $("diag-download-btn").addEventListener("click", downloadReport);
            $("diag-github-btn").addEventListener("click", openGithubIssue);
            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape" && modal.style.display !== "none") closeModal();
            });
        }
    }

    function openModal(opts = {}) {
        state.jobId = opts.jobId || null;
        state.videoId = opts.videoId || null;
        const modal = $("diag-modal");
        if (modal) modal.style.display = "flex";
        fetchReport();
    }

    function closeModal() {
        const modal = $("diag-modal");
        if (modal) modal.style.display = "none";
    }

    async function fetchReport() {
        const box = $("diag-report-text");
        if (!box) return;
        box.value = "Gerando relatório sanitizado...";
        const params = new URLSearchParams();
        if (state.jobId) params.set("job_id", String(state.jobId));
        else if (state.videoId) params.set("video_id", String(state.videoId));
        params.set("strip_transcripts", $("diag-strip-transcripts").checked ? "true" : "false");
        params.set("redact_filenames", $("diag-redact-filenames").checked ? "true" : "false");

        try {
            const res = await fetch(`/api/diagnostics/report?${params.toString()}`, { cache: "no-store" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            state.report = data;
            if (data.privacy_notice) $("diag-privacy-text").textContent = data.privacy_notice;
            box.value = data.report_markdown || "";
        } catch (e) {
            box.value = `Não foi possível gerar o relatório: ${e.message}`;
        }
    }

    function copyReport() {
        const txt = $("diag-report-text").value || "";
        navigator.clipboard.writeText(txt).then(
            () => alert("Relatório sanitizado copiado! Você pode colar no GitHub ou no Discord."),
            () => alert("Não foi possível copiar automaticamente. Selecione o texto e pressione Ctrl+C.")
        );
    }

    function downloadReport() {
        const txt = $("diag-report-text").value || "";
        const blob = new Blob([txt], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
        a.href = url;
        a.download = `banido-studio-diagnostico-${stamp}.txt`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function openGithubIssue() {
        const txt = $("diag-report-text").value || "";
        const title = (state.report && state.report.issue_title) || "Relatório de bug / diagnóstico";
        let repoUrl = "https://github.com/MatheusAlves350/banido-studio-ai/issues/new";
        if (state.report && state.report.github_issue_url) {
            repoUrl = state.report.github_issue_url.split("?")[0];
        }
        let body = txt;
        if (body.length > 5200) {
            body = body.slice(0, 5000) + "\n```\n\n*(Log truncado por limite de URL — anexe o arquivo .txt baixado pelo painel se necessário.)*";
        }
        const params = new URLSearchParams({ title, labels: "bug", body });
        window.open(`${repoUrl}?${params.toString()}`, "_blank", "noopener");
    }

    document.addEventListener("DOMContentLoaded", buildDom);
})();
