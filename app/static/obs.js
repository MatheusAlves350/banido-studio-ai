// Conferir o OBS (E5): mostra como o OBS está, deixa escolher e editar, mostra o que vai mudar e só aplica com confirmação.
// Usa window.fetch (o programa já injeta o token X-Banido-Token).
(function () {
    "use strict";

    let state = null;
    let previewTimer = null;

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

    function feedback(text, kind) {
        const el = $("obs-feedback");
        el.hidden = !text;
        el.textContent = text || "";
        el.className = "setup-feedback" + (kind ? " is-" + kind : "");
    }

    function tracksText(t) {
        if (!t || !t.length) return "nenhuma";
        return t.length === 1 ? `faixa ${t[0]}` : `faixas ${t.slice(0, -1).join(", ")} e ${t[t.length - 1]}`;
    }

    function buildModal() {
        if ($("obs-modal")) return;
        const modal = document.createElement("div");
        modal.className = "modal-overlay";
        modal.id = "obs-modal";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal-card setup-card obs-card" role="dialog" aria-modal="true" aria-labelledby="obs-title">
                <div class="modal-header">
                    <h3 id="obs-title">Conferir o OBS</h3>
                    <button id="obs-close" class="btn-close" type="button" aria-label="Fechar">Fechar</button>
                </div>
                <div class="modal-body">
                    <p class="setup-intro">Aqui você vê como o áudio do OBS está configurado e escolhe o que mudar. <strong>Nada muda no OBS até você clicar em Aplicar</strong>, e antes disso o programa guarda uma cópia de segurança. Dá para desfazer depois.</p>
                    <div id="obs-body"></div>
                    <p id="obs-feedback" class="setup-feedback" hidden></p>
                </div>
                <div class="modal-footer setup-footer">
                    <button type="button" id="obs-undo" class="btn-secondary" hidden>Desfazer a última alteração</button>
                    <span class="obs-footer-right">
                        <button type="button" id="obs-cancel" class="btn-secondary">Fechar sem aplicar</button>
                        <button type="button" id="obs-apply" class="btn-primary">Aplicar no OBS</button>
                    </span>
                </div>
            </div>`;
        document.body.appendChild(modal);
        $("obs-close").addEventListener("click", close);
        $("obs-cancel").addEventListener("click", close);
        $("obs-apply").addEventListener("click", apply);
        $("obs-undo").addEventListener("click", undo);
        $("obs-body").addEventListener("change", schedulePreview);
    }

    function choice() {
        const roles = {};
        document.querySelectorAll("#obs-body select[data-key]").forEach(s => { roles[s.dataset.key] = s.value; });
        return {
            layout: document.querySelector('#obs-body input[name="obs-layout"]:checked')?.value || "6",
            roles,
            add_filters: Boolean($("obs-filters")?.checked),
        };
    }

    function renderBody() {
        const body = $("obs-body");
        if (!state.found) {
            body.innerHTML = `<p class="obs-warn">${esc(state.message)}</p>`;
            return;
        }
        const layouts = state.layouts.map(l => `
            <label class="obs-layout"><input type="radio" name="obs-layout" value="${l.id}"${l.id === state.default_layout ? " checked" : ""}>
                <span><strong>${esc(l.label)}</strong><small>${esc(l.help)}</small></span></label>`).join("");
        const rows = state.sources.map(s => `
            <tr>
                <td>${esc(s.name)}</td>
                <td>${esc(tracksText(s.tracks_now))}</td>
                <td><select data-key="${esc(s.key)}" aria-label="Função de ${esc(s.name)}">
                    ${state.roles.map(r => `<option value="${r.id}"${r.id === s.role ? " selected" : ""}>${esc(r.label)}</option>`).join("")}
                </select></td>
                <td class="obs-new" data-new-for="${esc(s.key)}">-</td>
            </tr>`).join("");
        const aviso = state.obs_running
            ? `<p class="obs-warn">O OBS está aberto. Você pode escolher e ver o que mudaria, mas para aplicar é preciso fechar o OBS. O que aparece aqui é o último estado que o OBS salvou.</p>`
            : "";
        body.innerHTML = `
            <p class="obs-scene">Configuração lida: <code>${esc(state.scene_name)}</code></p>
            ${aviso}
            <h4>1. Quantas faixas de áudio gravar</h4>
            <div class="obs-layouts">${layouts}</div>
            <h4>2. O que é cada fonte de áudio</h4>
            <p class="obs-help">Escolha a função de cada fonte. O programa coloca cada uma na faixa certa. Em “Não mexer nesta fonte” nada é alterado.</p>
            <div class="obs-table-wrap"><table class="obs-table">
                <thead><tr><th>Fonte no OBS</th><th>Faixas hoje</th><th>Função</th><th>Ficará em</th></tr></thead>
                <tbody>${rows}</tbody></table></div>
            <h4>3. Melhorar a voz</h4>
            <label class="check-row"><input type="checkbox" id="obs-filters" checked>
                <span>Acrescentar compressor e limitador no seu microfone e no Discord (recomendado). Filtros que você já tem são mantidos.</span></label>
            <h4>O que vai mudar</h4>
            <div id="obs-diff" class="obs-diff">Calculando...</div>`;
        schedulePreview();
    }

    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(runPreview, 150);
    }

    async function runPreview() {
        if (!state?.found) return;
        try {
            const res = await api("/api/obs/preview", "POST", choice());
            document.querySelectorAll("#obs-body [data-new-for]").forEach(td => { td.textContent = "não muda"; });
            res.plan.forEach(p => {
                const td = document.querySelector(`#obs-body [data-new-for="${p.key}"]`);
                if (td) td.textContent = tracksText(p.tracks_new);
            });
            const box = $("obs-diff");
            if (!res.diff.length) {
                box.textContent = "Nada a mudar: o OBS já está assim.";
            } else {
                box.innerHTML = "<ul>" + res.diff.map(d =>
                    `<li><strong>${esc(d.name)}</strong> (${esc(d.role_label)}): ${d.changes.map(esc).join("; ")}.</li>`).join("") + "</ul>";
            }
            $("obs-apply").disabled = !res.diff.length || state.obs_running;
            feedback("");
        } catch (e) {
            feedback(e.message, "error");
        }
    }

    async function apply() {
        if (!confirm("Aplicar estas mudanças no OBS? O programa guarda uma cópia de segurança antes.")) return;
        $("obs-apply").disabled = true;
        feedback("Aplicando...", "info");
        try {
            const res = await api("/api/obs/apply", "POST", { ...choice(), base_hash: state.base_hash, confirm: true });
            feedback(res.message + (res.changed ? " Abra o OBS para conferir." : ""), "ok");
            await load();
        } catch (e) {
            feedback(e.message, "error");
            $("obs-apply").disabled = false;
        }
    }

    async function undo() {
        if (!confirm("Voltar o OBS para a última cópia de segurança?")) return;
        try {
            const res = await api("/api/obs/undo", "POST", { confirm: true });
            await load();
            feedback(res.message, "ok");
        } catch (e) {
            feedback(e.message, "error");
        }
    }

    async function load() {
        try {
            state = await api("/api/obs/state");
        } catch (e) {
            state = { found: false, message: e.message, roles: [], layouts: [], sources: [] };
        }
        renderBody();
        $("obs-undo").hidden = !state.can_undo;
        $("obs-apply").hidden = !state.found;
    }

    async function open() {
        buildModal();
        feedback("");
        $("obs-modal").style.display = "flex";
        $("obs-body").textContent = "Lendo a configuração do OBS...";
        await load();
    }

    function close() {
        $("obs-modal").style.display = "none";
    }

    function addOpenButton() {
        const host = $("btn-open-fila") || $("btn-open-history");
        if (!host || $("btn-open-obs")) return;
        const btn = document.createElement("button");
        btn.id = "btn-open-obs";
        btn.type = "button";
        btn.className = host.className;
        btn.textContent = "Conferir o OBS";
        btn.title = "Ver e ajustar as faixas de áudio do OBS, sem mexer em nada até você confirmar";
        btn.addEventListener("click", open);
        host.parentNode.insertBefore(btn, host.nextSibling);
    }

    document.addEventListener("DOMContentLoaded", addOpenButton);
})();
