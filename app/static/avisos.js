// Avisos ao terminar (E4): link do Discord, aviso de teste e faixa na tela quando um processamento acaba.
// Usa window.fetch (o programa já injeta o token X-Banido-Token).
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    async function api(path, method, body) {
        const opts = { method: method || "GET" };
        if (body !== undefined) {
            opts.headers = { "Content-Type": "application/json" };
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(path, opts);
        let data = {};
        try { data = await res.json(); } catch (_) {}
        if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
        return data;
    }

    function showState(st) {
        const input = $("input-discord-webhook");
        const fb = $("discord-webhook-feedback");
        const clear = $("btn-clear-discord-webhook");
        if (!input || !fb) return;
        input.value = "";
        if (st.has_discord_webhook) {
            input.placeholder = `Link salvo (${st.discord_webhook_masked}). Cole outro para trocar`;
            fb.innerHTML = `<span class="text-ok">Link salvo.</span> Os avisos também vão para o Discord.`;
            clear.style.display = "inline-block";
        } else {
            input.placeholder = "https://discord.com/api/webhooks/...";
            fb.textContent = "No Discord: Editar canal, Integrações, Webhooks, Novo webhook, Copiar URL.";
            clear.style.display = "none";
        }
    }

    async function loadState() {
        try { showState(await api("/api/notify")); } catch (_) {}
    }

    async function saveWebhook() {
        const fb = $("discord-webhook-feedback");
        try {
            showState(await api("/api/notify/webhook", "POST", { url: $("input-discord-webhook").value }));
        } catch (e) {
            fb.innerHTML = `<span class="text-error">${esc(e.message)}</span>`;
        }
    }

    async function clearWebhook() {
        if (!confirm("Remover o link do Discord? Os avisos do Windows continuam.")) return;
        try {
            showState(await api("/api/notify/webhook", "POST", { clear: true }));
        } catch (e) {
            $("discord-webhook-feedback").innerHTML = `<span class="text-error">${esc(e.message)}</span>`;
        }
    }

    async function sendTest() {
        const fb = $("notify-test-feedback");
        fb.textContent = "Enviando...";
        try {
            const r = await api("/api/notify/test", "POST");
            const partes = [];
            partes.push(r.windows ? "aviso do Windows enviado" : "o aviso do Windows só funciona no Windows");
            if (r.discord_configured) partes.push(r.discord ? "Discord enviado" : "o Discord não recebeu (confira o link)");
            fb.textContent = partes.join("; ") + ".";
        } catch (e) {
            fb.innerHTML = `<span class="text-error">${esc(e.message)}</span>`;
        }
    }

    // Faixa na tela quando um processamento termina (o mesmo aviso, para quem não vê o balão do Windows).
    let wasBusy = false;
    async function watchJobEnd() {
        let st;
        try {
            const res = await fetch("/api/status");
            if (!res.ok) return;
            st = await res.json();
        } catch (_) { return; }
        const busy = (st.active_count || 0) > 0;
        if (wasBusy && !busy) {
            try {
                const h = await api("/api/history?limit=1");
                const j = (h.jobs || [])[0];
                if (j && j.status === "completed") banner(`Processamento concluído: ${j.filename}`, "ok");
                else if (j && j.status === "error") banner(`O processamento falhou: ${j.filename}. ${j.error_friendly || ""}`, "error");
            } catch (_) {}
        }
        wasBusy = busy;
    }

    function banner(text, kind) {
        let el = $("job-end-banner");
        if (!el) {
            el = document.createElement("div");
            el.id = "job-end-banner";
            el.className = "job-end-banner";
            el.setAttribute("role", "status");
            document.body.appendChild(el);
        }
        el.className = "job-end-banner is-" + kind;
        el.innerHTML = `<span>${esc(text)}</span><button type="button" aria-label="Fechar">Fechar</button>`;
        el.querySelector("button").addEventListener("click", () => el.remove());
    }

    document.addEventListener("DOMContentLoaded", () => {
        $("btn-save-discord-webhook")?.addEventListener("click", saveWebhook);
        $("btn-clear-discord-webhook")?.addEventListener("click", clearWebhook);
        $("btn-test-notify")?.addEventListener("click", sendTest);
        $("btn-open-settings")?.addEventListener("click", loadState);
        setInterval(watchJobEnd, 5000);
    });
})();
