// Configurações avançadas: edita as opções do arquivo .env sem abrir o arquivo.
// Usa window.fetch (já com o token X-Banido-Token injetado pelo programa).
(function () {
    "use strict";

    let info = null;

    async function api(path, method = "GET", body) {
        const opts = { method };
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

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function controlHtml(f, value) {
        const id = "env-" + f.key;
        if (f.kind === "choice") {
            const opts = f.options.map(([v, l]) => `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(l)}</option>`).join("");
            return `<select id="${id}" data-key="${f.key}">${opts}</select>`;
        }
        if (f.kind === "bool") {
            return `<select id="${id}" data-key="${f.key}">
                <option value="1"${value === "1" ? " selected" : ""}>Ligado</option>
                <option value="0"${value === "0" ? " selected" : ""}>Desligado</option></select>`;
        }
        const ph = f.default ? ` placeholder="${esc(f.default)}"` : "";
        const input = `<input type="text" id="${id}" data-key="${f.key}" value="${esc(value)}"${ph} autocomplete="off">`;
        if (f.kind === "folder") {
            const pick = info.can_pick_folder
                ? `<button type="button" class="btn-secondary env-pick" data-target="${id}" data-title="${esc(f.label)}">Escolher pasta...</button>` : "";
            return `<div class="setup-row">${input}${pick}</div>`;
        }
        return input;
    }

    function fieldHtml(f, value) {
        const help = f.help ? `<small>${esc(f.help)}</small>` : "";
        const opt = f.optional ? ` <span class="setup-optional">(opcional)</span>` : "";
        return `<div class="form-group env-field"><label for="env-${f.key}"><strong>${esc(f.label)}</strong>${opt}</label>${controlHtml(f, value)}${help}</div>`;
    }

    function bodyHtml() {
        const groups = [];
        info.fields.forEach(f => {
            let g = groups.find(x => x.name === f.group);
            if (!g) { g = { name: f.group, advanced: f.advanced, fields: [] }; groups.push(g); }
            g.fields.push(f);
        });
        return groups.map(g => {
            const inner = g.fields.map(f => fieldHtml(f, info.values[f.key] || "")).join("");
            if (g.advanced) {
                return `<details class="env-group"><summary>${esc(g.name)}</summary>${inner}</details>`;
            }
            return `<section class="env-group"><h4>${esc(g.name)}</h4>${inner}</section>`;
        }).join("");
    }

    function buildModal() {
        if ($("env-modal")) return;
        const modal = document.createElement("div");
        modal.className = "modal-overlay";
        modal.id = "env-modal";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal-card setup-card env-card" role="dialog" aria-modal="true" aria-labelledby="env-title">
                <div class="modal-header">
                    <h3 id="env-title">Configurações avançadas</h3>
                    <button id="env-close" class="btn-close" type="button" aria-label="Fechar">✕</button>
                </div>
                <div class="modal-body">
                    <p class="setup-intro">Aqui você muda o jeito que o programa trabalha. Se não tiver certeza, deixe como está: os valores atuais já funcionam. As chaves de API ficam na tela de Configurações.</p>
                    <div id="env-body"></div>
                    <p id="env-feedback" class="setup-feedback" hidden></p>
                </div>
                <div class="modal-footer setup-footer">
                    <button type="button" id="env-cancel" class="btn-secondary">Cancelar</button>
                    <button type="button" id="env-save" class="btn-primary">Salvar e reiniciar</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        $("env-close").addEventListener("click", close);
        $("env-cancel").addEventListener("click", close);
        $("env-save").addEventListener("click", save);
        $("env-body").addEventListener("click", (ev) => {
            const btn = ev.target.closest(".env-pick");
            if (btn) pickFolder(btn);
        });
    }

    function feedback(text, kind) {
        const el = $("env-feedback");
        el.hidden = !text;
        el.textContent = text || "";
        el.className = "setup-feedback" + (kind ? " is-" + kind : "");
    }

    function close() {
        const modal = $("env-modal");
        if (modal) modal.style.display = "none";
    }

    async function open() {
        try {
            info = await api("/api/env-settings");
        } catch (e) {
            alert("Não consegui carregar as configurações: " + e.message);
            return;
        }
        buildModal();
        $("env-body").innerHTML = bodyHtml();
        $("env-save").hidden = false;
        $("env-save").disabled = false;
        feedback("");
        $("env-modal").style.display = "flex";
    }

    async function pickFolder(btn) {
        const input = $(btn.dataset.target);
        const label = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Aguardando...";
        feedback("Uma janela de pastas foi aberta. Se não aparecer, procure por ela na barra de tarefas.", "info");
        try {
            const res = await api("/api/env-settings/pick-folder?title=" + encodeURIComponent(btn.dataset.title || "Escolha a pasta"), "POST");
            if (res.folder) input.value = res.folder;
            feedback("");
        } catch (e) {
            feedback(e.message, "error");
        } finally {
            btn.disabled = false;
            btn.textContent = label;
        }
    }

    function collect() {
        const values = {};
        document.querySelectorAll("#env-body [data-key]").forEach(el => { values[el.dataset.key] = el.value; });
        return values;
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
                feedback("O programa não voltou a tempo. Abra o Banido Studio de novo pelo atalho.", "error");
            }
        }, 1000);
    }

    async function save() {
        const btn = $("env-save");
        btn.disabled = true;
        feedback("Salvando...", "info");
        try {
            const res = await api("/api/env-settings", "POST", { values: collect() });
            if (!res.restart_needed) {
                feedback("Nada mudou. Nenhuma alteração foi salva.", "ok");
                btn.disabled = false;
                return;
            }
            if (res.restart_supported) {
                feedback("Configurações salvas. Reiniciando o programa para aplicar... (alguns segundos)", "ok");
                await api("/api/update/restart", "POST");
                waitForServerAndReload(120000);
            } else {
                feedback("Configurações salvas. Feche a janela do programa e abra o Banido Studio de novo pelo atalho para aplicar.", "ok");
                btn.hidden = true;
            }
        } catch (e) {
            feedback(e.message, "error");
            btn.disabled = false;
        }
    }

    // Botão dentro do modal de Configurações.
    function addOpenButton() {
        const host = $("instance-paths-info");
        if (!host || $("env-open")) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.id = "env-open";
        btn.className = "btn-secondary";
        btn.style.marginTop = "10px";
        btn.style.marginRight = "8px";
        btn.textContent = "Configurações avançadas";
        btn.addEventListener("click", () => {
            const settings = $("settings-modal");
            if (settings) settings.style.display = "none";
            open();
        });
        host.parentNode.insertBefore(btn, host.nextSibling);
    }

    document.addEventListener("DOMContentLoaded", addOpenButton);
})();
