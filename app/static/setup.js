// Assistente de primeira configuração: pergunta o essencial na primeira abertura e grava o .env.
// Usa window.fetch (já com o token X-Banido-Token injetado por app.js).
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

    function buildModal() {
        if ($("setup-modal")) return;
        const modal = document.createElement("div");
        modal.className = "modal-overlay";
        modal.id = "setup-modal";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal-card setup-card" role="dialog" aria-modal="true" aria-labelledby="setup-title">
                <div class="modal-header">
                    <h3 id="setup-title">Configuração inicial</h3>
                    <button id="setup-close" class="btn-close" type="button" aria-label="Fechar">✕</button>
                </div>
                <div class="modal-body">
                    <p class="setup-intro">Responda o essencial para o Banido Studio funcionar do seu jeito. Tudo pode ser mudado depois.</p>

                    <div class="form-group">
                        <label for="setup-obs"><strong>Pasta das gravações do OBS</strong></label>
                        <div class="setup-row">
                            <input type="text" id="setup-obs" placeholder="Ex.: E:\\Videos\\OBS" autocomplete="off">
                            <button type="button" id="setup-pick" class="btn-secondary" style="white-space: nowrap;">Escolher pasta...</button>
                        </div>
                        <small>É a pasta onde o OBS salva os vídeos (.mkv). O programa lista e processa o que estiver nela.</small>
                    </div>

                    <div class="form-group setup-sep">
                        <label for="setup-host"><strong>Seu nome nas falas</strong></label>
                        <input type="text" id="setup-host" maxlength="60" placeholder="Ex.: Matheus">
                        <small>Nome do dono do microfone (Faixa 2). Aparece nas transcrições e nos títulos.</small>
                    </div>

                    <div class="form-group setup-sep">
                        <label for="setup-group"><strong>Nome do canal ou do grupo</strong></label>
                        <input type="text" id="setup-group" maxlength="60" placeholder="Ex.: Banido Studio">
                    </div>

                    <div class="form-group setup-sep">
                        <label for="setup-key"><strong>Chave do Google Gemini</strong> <span class="setup-optional">(opcional)</span></label>
                        <input type="password" id="setup-key" placeholder="AIzaSy..." autocomplete="off">
                        <small>Gera títulos, capítulos e descrição. É grátis em <a href="https://aistudio.google.com" target="_blank" rel="noopener">aistudio.google.com</a>. Sem a chave, você ainda pode usar o Claude Web.</small>
                    </div>

                    <p id="setup-feedback" class="setup-feedback" hidden></p>
                </div>
                <div class="modal-footer setup-footer">
                    <button type="button" id="setup-skip" class="btn-secondary">Pular por enquanto</button>
                    <button type="button" id="setup-save" class="btn-primary">Salvar e continuar</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        $("setup-close").addEventListener("click", close);
        $("setup-skip").addEventListener("click", skip);
        $("setup-save").addEventListener("click", save);
        $("setup-pick").addEventListener("click", pickFolder);
    }

    function feedback(text, kind) {
        const el = $("setup-feedback");
        el.hidden = !text;
        el.textContent = text || "";
        el.className = "setup-feedback" + (kind ? " is-" + kind : "");
    }

    function open() {
        buildModal();
        $("setup-obs").value = info.obs_dir || "";
        $("setup-host").value = info.host_name || "";
        $("setup-group").value = info.group_name || "";
        $("setup-key").value = "";
        $("setup-key").placeholder = info.has_google_api_key ? "Já existe uma chave salva (deixe vazio para manter)" : "AIzaSy...";
        $("setup-pick").hidden = !info.can_pick_folder;
        feedback("");
        $("setup-modal").style.display = "flex";
    }

    function close() {
        const modal = $("setup-modal");
        if (modal) modal.style.display = "none";
    }

    async function pickFolder() {
        const btn = $("setup-pick");
        btn.disabled = true;
        btn.textContent = "Aguardando...";
        feedback("Uma janela de pastas foi aberta. Se não aparecer, procure por ela na barra de tarefas.", "info");
        try {
            const res = await api("/api/setup/pick-folder", "POST");
            if (res.folder) $("setup-obs").value = res.folder;
            feedback("");
        } catch (e) {
            feedback(e.message, "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "Escolher pasta...";
        }
    }

    async function skip() {
        try { await api("/api/setup", "POST", { skip: true }); } catch (_) {}
        close();
    }

    async function save() {
        const btn = $("setup-save");
        btn.disabled = true;
        feedback("Salvando...", "info");
        try {
            const res = await api("/api/setup", "POST", {
                obs_dir: $("setup-obs").value,
                host_name: $("setup-host").value,
                group_name: $("setup-group").value,
                google_api_key: $("setup-key").value,
            });
            if (!res.restart_needed) {
                feedback("Configuração salva.", "ok");
                setTimeout(() => { close(); location.reload(); }, 700);
                return;
            }
            if (res.restart_supported) {
                feedback("Configuração salva. Reiniciando o programa para aplicar... (alguns segundos)", "ok");
                await api("/api/update/restart", "POST");
                waitForServerAndReload(120000);
            } else {
                feedback("Configuração salva. Feche esta janela do programa e abra o Banido Studio de novo pelo atalho para aplicar.", "ok");
                btn.hidden = true;
            }
        } catch (e) {
            feedback(e.message, "error");
            btn.disabled = false;
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
                feedback("O programa não voltou a tempo. Abra o Banido Studio de novo pelo atalho.", "error");
            }
        }, 1000);
    }

    async function load() {
        try {
            info = await api("/api/setup");
        } catch (_) {
            return null;
        }
        return info;
    }

    // Botão para refazer o assistente, dentro do modal de Configurações.
    function addReopenButton() {
        const host = document.getElementById("instance-paths-info");
        if (!host || document.getElementById("setup-reopen")) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.id = "setup-reopen";
        btn.className = "btn-secondary";
        btn.style.marginTop = "10px";
        btn.textContent = "Abrir assistente de configuração";
        btn.addEventListener("click", async () => {
            const settings = document.getElementById("settings-modal");
            if (settings) settings.style.display = "none";
            await load();
            if (info) open();
        });
        host.parentNode.appendChild(btn);
    }

    document.addEventListener("DOMContentLoaded", async () => {
        addReopenButton();
        await load();
        if (info && info.needs_setup) open();
    });
})();
