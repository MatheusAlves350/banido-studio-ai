// F4: backup e restauração do banco de vozes e do banco de dados.
// Usa window.fetch (já com X-Banido-Token injetado por js/api.js).
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }

    const KIND_LABEL = { manual: "Manual", auto: "Automático", antes_de_restaurar: "Antes de restaurar", importado: "Importado" };

    async function api(url, options) {
        const res = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
        let data = null;
        try { data = await res.json(); } catch (_) { /* resposta sem JSON (ex.: download) */ }
        if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
        return data;
    }

    function post(url, body) {
        return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    }

    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function buildDom() {
        const footer = document.querySelector(".sidebar-footer");
        if (footer && !$("btn-open-backup")) {
            const b = document.createElement("button");
            b.id = "btn-open-backup";
            b.type = "button";
            b.className = "btn-settings";
            b.style.marginTop = "8px";
            b.textContent = "Backup e restauração";
            b.addEventListener("click", openModal);
            const updBtn = $("upd-version-btn");
            if (updBtn) footer.insertBefore(b, updBtn);
            else footer.appendChild(b);
        }
        if ($("backup-modal")) return;
        const modal = document.createElement("div");
        modal.id = "backup-modal";
        modal.className = "modal-overlay";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal-card modal-wide" role="dialog" aria-modal="true" aria-labelledby="backup-title" style="max-width: 760px;">
                <div class="modal-header">
                    <h3 id="backup-title">Backup e restauração</h3>
                    <button type="button" id="backup-close" class="btn-close" aria-label="Fechar">Fechar</button>
                </div>
                <div class="modal-body">
                    <p style="font-size: 13px; line-height: 1.5;">
                        O backup guarda o <strong>banco de vozes</strong> dos seus amigos e o <strong>banco de dados</strong> do programa
                        (histórico, transcrições e configurações). Gravações, cortes e modelos não entram, e as chaves de API também não.
                        <span id="backup-privacy"></span>
                    </p>
                    <p style="font-size: 12.5px; opacity: 0.85;">Uma cópia automática é feita a cada 7 dias ao abrir o programa (as 4 últimas são mantidas).</p>
                    <div class="form-group">
                        <label for="backup-folder-input">Pasta dos backups (de preferência em outro disco)</label>
                        <div class="key-row">
                            <input type="text" id="backup-folder-input" placeholder="Ex.: E:\\Backups\\BanidoStudio" autocomplete="off">
                            <button type="button" id="backup-folder-save" class="btn-secondary">Salvar pasta</button>
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0;">
                        <button type="button" id="backup-create" class="btn-primary">Fazer backup agora</button>
                        <button type="button" id="backup-import-btn" class="btn-secondary">Importar backup de um arquivo .zip...</button>
                        <input type="file" id="backup-import-file" accept=".zip" style="display: none;">
                    </div>
                    <div id="backup-msg" style="font-size: 13px; min-height: 18px; margin-bottom: 8px;"></div>
                    <div id="backup-list">Carregando...</div>
                    <div id="backup-preview" style="display: none; margin-top: 14px; padding: 12px; border: 1px solid var(--border-color); border-radius: 8px; font-size: 13px; line-height: 1.5;"></div>
                </div>
            </div>`;
        document.body.appendChild(modal);
        modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
        $("backup-close").addEventListener("click", closeModal);
        $("backup-create").addEventListener("click", createBackup);
        $("backup-folder-save").addEventListener("click", saveFolder);
        $("backup-import-btn").addEventListener("click", () => $("backup-import-file").click());
        $("backup-import-file").addEventListener("change", importFile);
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && modal.style.display !== "none") closeModal();
        });
    }

    function setMsg(text, isError) {
        const el = $("backup-msg");
        el.textContent = text || "";
        el.style.color = isError ? "#f87171" : "";
    }

    function openModal() {
        $("backup-modal").style.display = "flex";
        $("backup-preview").style.display = "none";
        setMsg("");
        refresh();
    }

    function closeModal() { $("backup-modal").style.display = "none"; }

    async function refresh() {
        try {
            const st = await api("/api/backup/status");
            $("backup-folder-input").value = st.folder;
            $("backup-privacy").textContent = st.privacy_notice || "";
            renderList(st.backups);
        } catch (e) {
            $("backup-list").textContent = "Não foi possível ler os backups: " + e.message;
        }
    }

    function renderList(items) {
        const box = $("backup-list");
        if (!items.length) {
            box.innerHTML = "<em>Nenhum backup ainda. Clique em “Fazer backup agora”.</em>";
            return;
        }
        box.innerHTML = `<table style="width: 100%; font-size: 13px; border-collapse: collapse;">
            <thead><tr style="text-align: left;"><th>Data</th><th>Tipo</th><th>Tamanho</th><th></th></tr></thead>
            <tbody>${items.map(b => `
                <tr data-name="${esc(b.name)}">
                    <td>${esc(b.modified)}</td><td>${esc(KIND_LABEL[b.kind] || b.kind)}</td><td>${b.size_mb} MB</td>
                    <td style="text-align: right; white-space: nowrap;">
                        <button type="button" class="btn-secondary btn-sm" data-act="download">Baixar</button>
                        <button type="button" class="btn-secondary btn-sm" data-act="restore">Restaurar...</button>
                    </td>
                </tr>`).join("")}</tbody></table>`;
        box.querySelectorAll("button[data-act]").forEach(btn => {
            const name = btn.closest("tr").dataset.name;
            btn.addEventListener("click", () => (btn.dataset.act === "download" ? download(name) : preview(name)));
        });
    }

    async function createBackup() {
        const btn = $("backup-create");
        btn.disabled = true;
        setMsg("Criando backup...");
        try {
            const r = await post("/api/backup/create");
            setMsg(`Backup criado: ${r.name} (${r.size_mb} MB).`);
            await refresh();
        } catch (e) {
            setMsg("Não foi possível criar o backup: " + e.message, true);
        } finally {
            btn.disabled = false;
        }
    }

    async function saveFolder() {
        try {
            const r = await post("/api/backup/folder", { folder: $("backup-folder-input").value });
            setMsg("Pasta salva: " + r.folder);
            renderList(r.backups);
        } catch (e) {
            setMsg(e.message, true);
        }
    }

    async function download(name) {
        try {
            const res = await fetch("/api/backup/download", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`);
            const url = URL.createObjectURL(await res.blob());
            const a = document.createElement("a");
            a.href = url;
            a.download = name;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            setMsg("Não foi possível baixar: " + e.message, true);
        }
    }

    async function importFile() {
        const input = $("backup-import-file");
        const file = input.files && input.files[0];
        input.value = "";
        if (!file) return;
        setMsg(`Importando ${file.name}...`);
        try {
            const info = await api("/api/backup/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file });
            setMsg("Backup importado e verificado. Confira o resumo abaixo antes de restaurar.");
            await refresh();
            showPreview(info);
        } catch (e) {
            setMsg("Não foi possível importar: " + e.message, true);
        }
    }

    async function preview(name) {
        setMsg("Verificando o backup...");
        try {
            const info = await post("/api/backup/preview", { name });
            setMsg("");
            showPreview(info);
        } catch (e) {
            setMsg("Backup com problema: " + e.message, true);
        }
    }

    function showPreview(info) {
        const box = $("backup-preview");
        const vozes = (info.vozes || []).length ? info.vozes.map(esc).join(", ") : "nenhuma";
        box.style.display = "block";
        box.innerHTML = `
            <strong>Restaurar o backup de ${esc(info.criado_em)}?</strong>
            <ul style="margin: 8px 0 8px 18px;">
                <li>Vozes no backup: ${vozes} (${info.arquivos_de_voz} arquivo(s); ${info.arquivos_de_voz_que_serao_substituidos} serão substituídos).</li>
                <li>Vídeos no banco do backup: ${info.videos_no_backup}.</li>
                <li>${esc(info.aviso)}</li>
            </ul>
            <button type="button" id="backup-confirm-restore" class="btn-primary">Sim, restaurar este backup</button>
            <button type="button" id="backup-cancel-restore" class="btn-secondary">Cancelar</button>`;
        $("backup-cancel-restore").addEventListener("click", () => { box.style.display = "none"; });
        $("backup-confirm-restore").addEventListener("click", () => restore(info.name));
    }

    async function restore(name) {
        const btn = $("backup-confirm-restore");
        btn.disabled = true;
        setMsg("Restaurando...");
        try {
            const r = await post("/api/backup/restore", { name });
            $("backup-preview").style.display = "none";
            setMsg(`Restauração concluída. O estado anterior foi guardado em ${r.backup_de_seguranca}. Recarregue a página (Ctrl+F5).`);
            await refresh();
        } catch (e) {
            btn.disabled = false;
            setMsg("Não foi possível restaurar: " + e.message, true);
        }
    }

    document.addEventListener("DOMContentLoaded", buildDom);
})();
