// Guia para iniciantes: diagnóstico de primeiros passos, etapas do vídeo, abas e explicações ("?").
// Não altera a lógica do app.js: só observa o estado da página e organiza a navegação.
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    function esc(str) {
        return String(str == null ? "" : str)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    // ------------------------------------------------------------------
    // Primeiros passos
    // ------------------------------------------------------------------
    const STATUS_TEXT = { ok: "Tudo certo", warn: "Atenção", error: "Precisa resolver" };

    async function loadSetupCheck() {
        const list = $("setup-items");
        const summary = $("setup-summary");
        const btn = $("btn-setup-recheck");
        if (!list) return;
        if (btn) { btn.disabled = true; btn.textContent = "Verificando..."; }
        try {
            const res = await fetch("/api/setup/check", { cache: "no-store" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            renderSetup(data);
        } catch (e) {
            summary.textContent = "Não foi possível verificar agora. O servidor está aberto?";
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = "Verificar de novo"; }
        }
    }

    function renderSetup(data) {
        const list = $("setup-items");
        const summary = $("setup-summary");
        const items = data.items || [];
        const order = { error: 0, warn: 1, ok: 2 };
        const sorted = [...items].sort((a, b) => order[a.status] - order[b.status]);
        const pending = items.filter(i => i.status !== "ok");

        if (data.errors > 0) {
            summary.textContent = data.errors === 1
                ? "Falta 1 coisa para o Banido Studio funcionar. Veja como resolver abaixo."
                : `Faltam ${data.errors} coisas para o Banido Studio funcionar. Veja como resolver abaixo.`;
            summary.className = "setup-summary is-error";
        } else if (pending.length > 0) {
            summary.textContent = "Já dá para usar. Há algumas sugestões para funcionar melhor.";
            summary.className = "setup-summary is-warn";
        } else {
            summary.textContent = "Tudo pronto. Escolha uma gravação na lista para começar.";
            summary.className = "setup-summary is-ok";
        }

        list.innerHTML = sorted.map(i => `
            <li class="setup-item is-${esc(i.status)}">
                <span class="setup-dot" aria-hidden="true"></span>
                <div class="setup-body">
                    <div class="setup-title"><span class="sr-only">${esc(STATUS_TEXT[i.status] || "")}: </span>${esc(i.title)}</div>
                    ${i.detail ? `<div class="setup-detail">${esc(i.detail)}</div>` : ""}
                    ${i.status !== "ok" && i.fix ? `<div class="setup-fix">${esc(i.fix)}</div>` : ""}
                    ${i.status !== "ok" && i.action === "open-settings" ? `<button type="button" class="btn-secondary btn-sm setup-action" data-action="open-settings">Abrir configurações</button>` : ""}
                </div>
            </li>`).join("");

        list.querySelectorAll('[data-action="open-settings"]').forEach(b => {
            b.addEventListener("click", () => $("btn-open-settings")?.click());
        });
    }

    // ------------------------------------------------------------------
    // Abas de resultados
    // ------------------------------------------------------------------
    function tabs() {
        return Array.from(document.querySelectorAll('.tabs [role="tab"]'));
    }

    function selectTab(panelId, focus = false) {
        tabs().forEach(t => {
            const on = t.getAttribute("aria-controls") === panelId;
            t.setAttribute("aria-selected", on ? "true" : "false");
            t.tabIndex = on ? 0 : -1;
            const panel = $(t.getAttribute("aria-controls"));
            if (panel) panel.hidden = !on;
            if (on && focus) t.focus();
        });
    }

    function setupTabs() {
        tabs().forEach((t, idx, all) => {
            t.addEventListener("click", () => selectTab(t.getAttribute("aria-controls")));
            t.addEventListener("keydown", (e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const next = all[(idx + (e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
                selectTab(next.getAttribute("aria-controls"), true);
            });
        });
        selectTab("tab-titulo");
    }

    // ------------------------------------------------------------------
    // Etapas (1 jogo, 2 processar, 3 vozes, 4 publicar)
    // ------------------------------------------------------------------
    function isShown(el) {
        return !!el && el.style.display !== "none" && el.offsetParent !== null;
    }

    function pendingVoices() {
        const grid = $("speakers-grid");
        if (!grid) return 0;
        return grid.querySelectorAll(".speaker-suggestion").length;
    }

    function computeSteps() {
        const game = ($("custom-game-input")?.value || "").trim();
        const processing = isShown($("progress-container"));
        const done = isShown($("results-container"));
        const voices = pendingVoices();

        const state = {
            1: game || done || processing ? "done" : "todo",
            2: done ? "done" : (processing ? "running" : "todo"),
            3: done ? (voices === 0 ? "done" : "todo") : "locked",
            4: done ? "todo" : "locked",
        };
        let current = [1, 2, 3, 4].find(n => state[n] === "todo" || state[n] === "running") || 4;
        return { state, current, voices, done };
    }

    function renderSteps() {
        const nav = $("flow-steps");
        if (!nav) return;
        const { state, current, voices } = computeSteps();
        nav.querySelectorAll(".flow-step").forEach(btn => {
            const n = Number(btn.dataset.step);
            btn.classList.remove("is-done", "is-current", "is-locked", "is-running");
            if (state[n] === "done") btn.classList.add("is-done");
            if (state[n] === "locked") btn.classList.add("is-locked");
            if (state[n] === "running") btn.classList.add("is-running");
            if (n === current) btn.classList.add("is-current");
            btn.setAttribute("aria-current", n === current ? "step" : "false");
            btn.disabled = state[n] === "locked";
        });
        const count = $("tab-count-vozes");
        if (count) count.textContent = voices > 0 ? String(voices) : "";
    }

    function setupSteps() {
        const nav = $("flow-steps");
        if (!nav) return;
        nav.querySelectorAll(".flow-step").forEach(btn => {
            btn.addEventListener("click", () => {
                if (btn.dataset.tab) {
                    selectTab(btn.dataset.tab);
                    document.querySelector(".tabs")?.scrollIntoView({ behavior: "smooth", block: "start" });
                } else if (btn.dataset.target) {
                    $(btn.dataset.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
                    if (btn.dataset.step === "1") $("custom-game-input")?.focus();
                }
            });
        });

        let timer = null;
        const schedule = () => { clearTimeout(timer); timer = setTimeout(renderSteps, 120); };
        const ws = $("video-workspace");
        if (ws) {
            new MutationObserver(schedule).observe(ws, {
                subtree: true, childList: true, attributes: true, attributeFilter: ["style", "class"]
            });
        }
        $("custom-game-input")?.addEventListener("input", schedule);
        renderSteps();
    }

    // Ao trocar de vídeo, volta para a primeira aba
    function setupVideoChangeReset() {
        const list = $("video-list");
        list?.addEventListener("click", (e) => {
            if (e.target.closest(".video-item")) selectTab("tab-titulo");
        });
    }

    // ------------------------------------------------------------------
    // Explicações ("?")
    // ------------------------------------------------------------------
    function setupHelp() {
        const pop = $("help-popover");
        if (!pop) return;
        let openFor = null;

        function close() {
            pop.hidden = true;
            if (openFor) openFor.setAttribute("aria-expanded", "false");
            openFor = null;
        }

        document.addEventListener("click", (e) => {
            const btn = e.target.closest(".help");
            if (!btn) {
                if (!pop.contains(e.target)) close();
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            if (openFor === btn) { close(); return; }
            close();
            pop.textContent = btn.dataset.help || "";
            pop.hidden = false;
            const r = btn.getBoundingClientRect();
            const w = Math.min(340, window.innerWidth - 24);
            pop.style.width = `${w}px`;
            pop.style.left = `${Math.max(12, Math.min(r.left - 12, window.innerWidth - w - 12))}px`;
            pop.style.top = `${r.bottom + 8}px`;
            btn.setAttribute("aria-expanded", "true");
            btn.setAttribute("aria-describedby", "help-popover");
            openFor = btn;
        });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
        $("content-panel")?.addEventListener("scroll", close, { passive: true });

        document.querySelectorAll(".help").forEach(b => {
            b.setAttribute("aria-label", "O que é isso?");
            b.setAttribute("aria-expanded", "false");
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        setupTabs();
        setupSteps();
        setupVideoChangeReset();
        setupHelp();
        loadSetupCheck();
        $("btn-setup-recheck")?.addEventListener("click", loadSetupCheck);
    });
})();
