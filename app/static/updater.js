// Atualizações pelo GitHub: aviso de nova versão, lista de novidades e atualização em 1 clique.
// Funciona para instalações com Git (commits) e para quem instalou pelo BanidoStudio-Setup.exe (releases).
// Usa window.fetch (já com o token X-Banido-Token injetado por app.js).
(function () {
    "use strict";

    const state = { info: null, pollTimer: null, dismissedSha: null };

    function esc(str) {
        return String(str == null ? "" : str)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function fmtDate(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }) +
            " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function plural(n, one, many) {
        return `${n} ${n === 1 ? one : many}`;
    }

    async function api(path, method = "GET") {
        const res = await fetch(path, { method });
        let data = {};
        try { data = await res.json(); } catch (_) {}
        if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
        return data;
    }

    // ---------- DOM ----------
    function buildDom() {
        const footer = document.querySelector(".sidebar-footer");
        if (footer && !document.getElementById("upd-version-btn")) {
            const btn = document.createElement("button");
            btn.id = "upd-version-btn";
            btn.className = "upd-version-btn";
            btn.type = "button";
            btn.title = "Ver versão instalada e atualizações";
            btn.innerHTML = `<span class="upd-version-label">Versão</span><span id="upd-version-sha">...</span><span id="upd-version-dot" class="upd-dot" hidden></span>`;
            btn.addEventListener("click", openModal);
            footer.appendChild(btn);
        }

        const main = document.querySelector(".main-content");
        if (main && !document.getElementById("upd-banner")) {
            const banner = document.createElement("div");
            banner.id = "upd-banner";
            banner.className = "upd-banner";
            banner.hidden = true;
            banner.setAttribute("role", "status");
            banner.innerHTML = `
                <div class="upd-banner-text">
                    <strong>Nova versão disponível.</strong>
                    <span id="upd-banner-detail"></span>
                </div>
                <div class="upd-banner-actions">
                    <button type="button" class="upd-btn upd-btn-primary" id="upd-banner-open">Ver novidades e atualizar</button>
                    <button type="button" class="upd-btn upd-btn-ghost" id="upd-banner-dismiss" title="Lembrar depois">Agora não</button>
                </div>`;
            main.insertBefore(banner, main.firstChild);
            banner.querySelector("#upd-banner-open").addEventListener("click", openModal);
            banner.querySelector("#upd-banner-dismiss").addEventListener("click", () => {
                state.dismissedSha = state.info && state.info.commits && state.info.commits[0] ? state.info.commits[0].sha : "x";
                try { localStorage.setItem("banido-upd-dismissed", state.dismissedSha); } catch (_) {}
                banner.hidden = true;
            });
        }

        if (!document.getElementById("upd-modal")) {
            const modal = document.createElement("div");
            modal.id = "upd-modal";
            modal.className = "upd-modal-overlay";
            modal.hidden = true;
            modal.innerHTML = `
                <div class="upd-modal" role="dialog" aria-modal="true" aria-labelledby="upd-modal-title">
                    <div class="upd-modal-head">
                        <h3 id="upd-modal-title">Atualizações</h3>
                        <button type="button" class="upd-btn upd-btn-ghost" id="upd-close" aria-label="Fechar">Fechar</button>
                    </div>
                    <div class="upd-modal-body">
                        <dl class="upd-facts">
                            <div><dt>Versão instalada</dt><dd id="upd-local"></dd></div>
                            <div><dt>Repositório</dt><dd><a id="upd-repo-link" target="_blank" rel="noopener"></a></dd></div>
                            <div><dt>Última verificação</dt><dd id="upd-checked"></dd></div>
                        </dl>
                        <p id="upd-summary" class="upd-summary"></p>
                        <div id="upd-commits-wrap" hidden>
                            <h4>O que mudou</h4>
                            <ul id="upd-commits" class="upd-commits"></ul>
                            <p id="upd-deps-note" class="upd-note" hidden>Esta atualização instala dependências novas do Python (pode levar alguns minutos).</p>
                        </div>
                        <div id="upd-progress-wrap" hidden>
                            <h4>Andamento</h4>
                            <pre id="upd-progress-log" class="upd-log"></pre>
                        </div>
                    </div>
                    <div class="upd-modal-foot">
                        <button type="button" class="upd-btn" id="upd-check">Verificar agora</button>
                        <span class="upd-spacer"></span>
                        <button type="button" class="upd-btn upd-btn-primary" id="upd-apply" hidden>Atualizar agora</button>
                        <button type="button" class="upd-btn upd-btn-primary" id="upd-restart" hidden>Reiniciar para concluir</button>
                    </div>
                </div>`;
            document.body.appendChild(modal);
            modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
            modal.querySelector("#upd-close").addEventListener("click", closeModal);
            modal.querySelector("#upd-check").addEventListener("click", () => refresh(true));
            modal.querySelector("#upd-apply").addEventListener("click", applyUpdate);
            modal.querySelector("#upd-restart").addEventListener("click", restartServer);
            document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) closeModal(); });
        }
    }

    function openModal() {
        document.getElementById("upd-modal").hidden = false;
        render();
        refresh(false);
    }

    function closeModal() {
        document.getElementById("upd-modal").hidden = true;
    }

    // ---------- Render ----------
    function render() {
        const info = state.info;
        if (!info) return;
        const local = info.local || {};
        const apply = info.apply || {};
        const installed = local.mode === "installed";

        const shaEl = document.getElementById("upd-version-sha");
        if (shaEl) shaEl.textContent = installed ? `v${local.version}` : (local.mode === "zip" ? "ZIP" : (local.sha || "?"));
        const dot = document.getElementById("upd-version-dot");
        if (dot) dot.hidden = !info.update_available;

        // Banner
        const banner = document.getElementById("upd-banner");
        if (banner) {
            const newest = info.commits && info.commits[0] ? info.commits[0].sha : null;
            let dismissed = state.dismissedSha;
            try { dismissed = dismissed || localStorage.getItem("banido-upd-dismissed"); } catch (_) {}
            const show = info.update_available && apply.state !== "done" && newest !== dismissed;
            banner.hidden = !show && apply.state !== "done";
            const detail = document.getElementById("upd-banner-detail");
            if (apply.state === "done" && apply.installer_started) {
                banner.hidden = false;
                banner.querySelector("strong").textContent = "Atualizando...";
                detail.textContent = "O instalador foi aberto. Este painel vai reabrir sozinho quando terminar; não feche a janela do instalador.";
            } else if (apply.state === "done") {
                banner.hidden = false;
                banner.querySelector("strong").textContent = "Atualização baixada.";
                detail.textContent = "Reinicie o Banido Studio para começar a usar a nova versão.";
            } else if (show) {
                detail.textContent = installed
                    ? `Versão ${info.latest_version || ""} pronta para instalar.`
                    : `${plural(info.behind, "melhoria", "melhorias")} desde a sua versão.`;
            }
        }

        // Modal
        const modal = document.getElementById("upd-modal");
        if (!modal || modal.hidden) return;
        document.getElementById("upd-local").textContent = installed
            ? `Versão ${local.version} (instalada pelo instalador)`
            : (local.mode === "zip"
                ? "Cópia baixada como ZIP (sem Git)"
                : `${local.sha || "?"} — ${local.subject || ""} (${fmtDate(local.date)})`);
        const link = document.getElementById("upd-repo-link");
        link.href = (installed && info.release_url) || info.repo_url || "#";
        link.textContent = info.repo || "";
        document.getElementById("upd-checked").textContent = info.checked_at || "";

        const summary = document.getElementById("upd-summary");
        summary.className = "upd-summary";
        if (info.error) {
            summary.textContent = info.error;
            summary.classList.add("is-error");
        } else if (info.update_available) {
            const titulo = installed
                ? `Nova versão disponível: ${info.latest_version || ""}`
                : plural(info.behind, "atualização disponível", "atualizações disponíveis");
            summary.textContent = info.can_apply
                ? `${titulo}. Seus vídeos, transcrições e configurações não são afetados.`
                : `${titulo}, mas não dá para aplicar automaticamente: ${info.reason}`;
            if (!info.can_apply) summary.classList.add("is-warn");
        } else {
            summary.textContent = info.reason || "Você já está na versão mais recente.";
            summary.classList.add("is-ok");
        }

        const commitsWrap = document.getElementById("upd-commits-wrap");
        const list = document.getElementById("upd-commits");
        const commits = info.commits || [];
        commitsWrap.hidden = commits.length === 0;
        list.innerHTML = commits.map(c => `
            <li><span class="upd-commit-subject">${esc(c.subject)}</span>
            <span class="upd-commit-meta">${esc(c.sha)} · ${esc(fmtDate(c.date))}</span></li>`).join("");
        document.getElementById("upd-deps-note").hidden = !info.requirements_changed;

        const progWrap = document.getElementById("upd-progress-wrap");
        const logEl = document.getElementById("upd-progress-log");
        progWrap.hidden = !apply.state || apply.state === "idle";
        logEl.textContent = (apply.log || []).join("\n");
        logEl.scrollTop = logEl.scrollHeight;

        const busy = apply.state === "running";
        document.getElementById("upd-apply").hidden = !(info.update_available && info.can_apply) || apply.state === "done";
        document.getElementById("upd-apply").disabled = busy;
        document.getElementById("upd-apply").textContent = busy ? "Atualizando..." : "Atualizar agora";
        document.getElementById("upd-restart").hidden = apply.state !== "done" || !!apply.installer_started;
        document.getElementById("upd-check").disabled = busy;
    }

    // ---------- Ações ----------
    async function refresh(force) {
        const btn = document.getElementById("upd-check");
        if (force && btn) { btn.disabled = true; btn.textContent = "Verificando..."; }
        try {
            state.info = force ? await api("/api/update/check", "POST") : await api("/api/update/status");
            if (state.info.apply && state.info.apply.state === "running" && !state.pollTimer) pollProgress();
        } catch (e) {
            state.info = { ...(state.info || {}), error: `Não foi possível verificar: ${e.message}` };
        } finally {
            if (force && btn) { btn.disabled = false; btn.textContent = "Verificar agora"; }
        }
        render();
    }

    async function applyUpdate() {
        const installed = state.info && state.info.local && state.info.local.mode === "installed";
        const msg = installed
            ? "Baixar e instalar a nova versão agora?\n\nO painel vai fechar e reabrir sozinho quando o instalador terminar (alguns minutos). Seus dados e configurações são mantidos.\n\nO servidor precisa estar sem nenhum vídeo em processamento."
            : "Baixar e instalar a nova versão agora?\n\nO servidor precisa estar sem nenhum vídeo em processamento.";
        if (!confirm(msg)) return;
        try {
            const res = await api("/api/update/apply", "POST");
            state.info.apply = res.apply;
            render();
            pollProgress();
        } catch (e) {
            alert(e.message);
        }
    }

    function pollProgress() {
        clearInterval(state.pollTimer);
        state.pollTimer = setInterval(async () => {
            try {
                const apply = await api("/api/update/progress");
                state.info.apply = apply;
                if (apply.state !== "running") {
                    clearInterval(state.pollTimer);
                    state.pollTimer = null;
                    if (apply.state === "done" && apply.installer_started) {
                        // O servidor vai fechar; o instalador reabre o painel. Espera até 15 min (downloads grandes).
                        render();
                        waitForServerAndReload(15 * 60 * 1000);
                        return;
                    }
                    await refresh(false);
                    if (apply.state === "error") alert(`A atualização falhou: ${apply.error || "veja o andamento"}`);
                }
                render();
            } catch (_) {}
        }, 1500);
    }

    async function restartServer() {
        try {
            const res = await api("/api/update/restart", "POST");
            if (!res.restart_supported) {
                alert(res.message);
                return;
            }
            const btn = document.getElementById("upd-restart");
            btn.disabled = true;
            btn.textContent = "Reiniciando...";
            waitForServerAndReload(120000);
        } catch (e) {
            alert(e.message);
        }
    }

    function waitForServerAndReload(timeoutMs) {
        let wentDown = false;
        const started = Date.now();
        const timer = setInterval(async () => {
            try {
                const r = await fetch("/api/status", { cache: "no-store" });
                if (r.ok && (wentDown || Date.now() - started > 6000)) {
                    clearInterval(timer);
                    location.reload();
                }
            } catch (_) {
                wentDown = true;
            }
            if (Date.now() - started > timeoutMs) {
                clearInterval(timer);
                alert("O servidor não voltou a tempo. Abra o Banido Studio de novo pelo atalho da Área de Trabalho.");
            }
        }, 1000);
    }

    document.addEventListener("DOMContentLoaded", () => {
        buildDom();
        // Primeira verificação um pouco depois do carregamento, para não competir com a lista de vídeos
        setTimeout(() => refresh(false), 2500);
        // Revalida a cada 6 h com a página aberta (o servidor ainda mantém cache de 10 min)
        setInterval(() => refresh(false), 6 * 60 * 60 * 1000);
    });
})();
