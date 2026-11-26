// Fila de vários vídeos e processamento noturno (E3).
// Usa window.fetch (o programa já injeta o token X-Banido-Token) e as variáveis globais do painel (allVideos).
(function () {
    "use strict";

    let snap = null;
    let timer = null;
    const STATE_LABEL = { waiting: "Aguardando", running: "Em andamento", done: "Concluído", failed: "Falhou" };

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
        const el = $("fila-feedback");
        if (!el) return;
        el.hidden = !text;
        el.textContent = text || "";
        el.className = "setup-feedback" + (kind ? " is-" + kind : "");
    }

    function title(filename) {
        return typeof describeRecording === "function" ? describeRecording(filename).title : filename;
    }

    function whenText(video) {
        return typeof describeRecording === "function" ? describeRecording(video.filename).when : "";
    }

    function buildModal() {
        if ($("fila-modal")) return;
        const modal = document.createElement("div");
        modal.className = "modal-overlay";
        modal.id = "fila-modal";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal-card setup-card fila-card" role="dialog" aria-modal="true" aria-labelledby="fila-title">
                <div class="modal-header">
                    <h3 id="fila-title">Fila de vários vídeos</h3>
                    <button id="fila-close" class="btn-close" type="button" aria-label="Fechar">Fechar</button>
                </div>
                <div class="modal-body">
                    <p class="setup-intro">Marque as gravações, escolha quando começar e deixe o programa trabalhar. Ele processa um vídeo por vez. Se um falhar, ele segue para o próximo e anota o motivo.</p>

                    <div id="fila-power-banner" class="fila-power" hidden></div>

                    <h4>1. Escolha as gravações</h4>
                    <div class="fila-tools">
                        <button type="button" id="fila-pick-pending" class="btn-secondary btn-sm">Marcar as não processadas</button>
                        <button type="button" id="fila-pick-none" class="btn-secondary btn-sm">Desmarcar tudo</button>
                    </div>
                    <div id="fila-videos" class="fila-videos"></div>

                    <h4>2. Quando começar</h4>
                    <label class="check-row"><input type="radio" name="fila-start" value="now" checked> <span>Agora</span></label>
                    <label class="check-row"><input type="radio" name="fila-start" value="at"> <span>À noite, às <input type="time" id="fila-time" value="23:00"></span></label>
                    <small id="fila-start-hint">O relógio do PC é a referência. Se o horário já passou hoje, começa amanhã.</small>

                    <h4>3. Quando terminar</h4>
                    <select id="fila-after">
                        <option value="none">Não fazer nada (o PC continua ligado)</option>
                        <option value="sleep">Suspender o PC</option>
                        <option value="shutdown">Desligar o PC</option>
                    </select>
                    <small>Antes de suspender ou desligar, o programa avisa e espera 1 minuto. Dá para cancelar nesse prazo.</small>

                    <p class="setup-intro" style="margin-top:12px;">A fila usa a mesma IA, o mesmo modelo e as mesmas opções de volume e falas que estão na tela principal.</p>
                    <p id="fila-feedback" class="setup-feedback" hidden></p>

                    <h4>Fila atual</h4>
                    <div id="fila-start-info" class="fila-start-info" hidden></div>
                    <div id="fila-items" class="fila-items"></div>
                    <div class="fila-tools">
                        <button type="button" id="fila-start-now" class="btn-secondary btn-sm" hidden>Começar agora</button>
                        <button type="button" id="fila-clear" class="btn-secondary btn-sm">Limpar concluídos e falhas</button>
                        <button type="button" id="fila-cancel" class="btn-secondary btn-sm">Tirar da fila o que não começou</button>
                    </div>
                </div>
                <div class="modal-footer setup-footer">
                    <button type="button" id="fila-close2" class="btn-secondary">Fechar</button>
                    <button type="button" id="fila-add" class="btn-primary">Adicionar à fila</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        $("fila-close").addEventListener("click", close);
        $("fila-close2").addEventListener("click", close);
        $("fila-add").addEventListener("click", addToQueue);
        $("fila-clear").addEventListener("click", () => act("/api/queue/clear"));
        $("fila-cancel").addEventListener("click", () => {
            if (confirm("Tirar da fila tudo o que ainda não começou? O vídeo em andamento continua até o fim.")) act("/api/queue/cancel");
        });
        $("fila-start-now").addEventListener("click", () => act("/api/queue/start-now"));
        $("fila-pick-pending").addEventListener("click", () => pick(v => v.status === "unprocessed" || v.status === "error"));
        $("fila-pick-none").addEventListener("click", () => pick(() => false));
        $("fila-time").addEventListener("focus", () => { document.querySelector('input[name="fila-start"][value="at"]').checked = true; });
        $("fila-items").addEventListener("click", ev => {
            const btn = ev.target.closest("[data-remove]");
            if (btn) act(`/api/queue/remove/${btn.dataset.remove}`);
        });
        $("fila-power-banner").addEventListener("click", ev => {
            if (ev.target.closest("button")) act("/api/queue/cancel-power");
        });
    }

    function pick(rule) {
        document.querySelectorAll("#fila-videos input[type=checkbox]").forEach(cb => {
            const v = (typeof allVideos !== "undefined" ? allVideos : []).find(x => String(x.id) === cb.value);
            cb.checked = Boolean(v && rule(v));
        });
    }

    function renderVideos() {
        const queued = new Set((snap?.items || []).filter(i => i.status === "waiting" || i.status === "running").map(i => i.video_id));
        const list = (typeof allVideos !== "undefined" ? allVideos : []).filter(v => v.status !== "processing" && !queued.has(v.id));
        const box = $("fila-videos");
        const checked = new Set(Array.from(box.querySelectorAll("input:checked")).map(cb => cb.value));
        if (!list.length) {
            box.innerHTML = `<p class="fila-empty">Nenhuma gravação livre para a fila agora.</p>`;
            return;
        }
        const labels = { unprocessed: "Não processado", completed: "Pronto", error: "Erro" };
        box.innerHTML = list.map(v => `
            <label class="fila-video">
                <input type="checkbox" value="${v.id}"${checked.has(String(v.id)) ? " checked" : ""}>
                <span class="fila-video-name">${esc(title(v.filename))}<small>${esc(whenText(v))}</small></span>
                <span class="fila-video-status">${esc(labels[v.status] || v.status)}</span>
            </label>`).join("");
    }

    function renderQueue() {
        const items = snap?.items || [];
        const box = $("fila-items");
        if (!items.length) {
            box.innerHTML = `<p class="fila-empty">A fila está vazia.</p>`;
        } else {
            box.innerHTML = items.map(i => {
                const erro = i.status === "failed" && i.error_friendly ? `<small class="fila-error">${esc(i.error_friendly)}</small>` : "";
                const tirar = i.status === "waiting" ? `<button type="button" class="btn-link" data-remove="${i.id}">Tirar</button>` : "";
                return `<div class="fila-item is-${esc(i.status)}">
                    <span class="fila-item-name">${esc(title(i.filename || ("Vídeo " + i.video_id)))}${erro}</span>
                    <span class="fila-item-state">${esc(STATE_LABEL[i.status] || i.status)}</span>${tirar}
                </div>`;
            }).join("");
        }
        const s = snap?.settings || {};
        const waiting = items.some(i => i.status === "waiting");
        const info = $("fila-start-info");
        info.hidden = !(waiting && s.start_at);
        if (!info.hidden) {
            const [dia, hora] = s.start_at.split(" ");
            const [a, m, d] = dia.split("-");
            info.textContent = `Começa em ${d}/${m}/${a} às ${hora} (hora do PC).`;
        }
        $("fila-start-now").hidden = !(waiting && s.start_at);
        const after = { sleep: "suspender", shutdown: "desligar" }[s.after];
        if (after && (waiting || items.some(i => i.status === "running"))) {
            info.hidden = false;
            info.textContent = (info.textContent ? info.textContent + " " : "") + `Quando terminar, o PC vai ${after}.`;
        }
    }

    function renderPower() {
        const pend = snap?.pending_power;
        const text = pend ? `O PC vai ${pend.action === "shutdown" ? "desligar" : "suspender"} em ${pend.seconds_left} s.` : "";
        for (const [id, inModal] of [["fila-power-banner", true], ["fila-power-global", false]]) {
            let el = $(id);
            if (!pend) { if (el && !inModal) el.remove(); if (el && inModal) el.hidden = true; continue; }
            if (!el) {
                el = document.createElement("div");
                el.id = id;
                el.className = inModal ? "fila-power" : "job-end-banner is-error fila-power-global";
                el.setAttribute("role", "alert");
                el.addEventListener("click", ev => { if (ev.target.closest("button")) act("/api/queue/cancel-power"); });
                document.body.appendChild(el);
            }
            el.hidden = false;
            el.innerHTML = `<span>${esc(text)}</span><button type="button" class="btn-secondary btn-sm">Cancelar</button>`;
        }
    }

    function updateButton() {
        const btn = $("btn-open-fila");
        if (!btn) return;
        const n = (snap?.items || []).filter(i => i.status === "waiting" || i.status === "running").length;
        btn.textContent = n ? `Fila de vídeos (${n})` : "Fila de vídeos";
    }

    function render() {
        updateButton();
        renderPower();
        if ($("fila-modal")?.style.display === "flex") {
            renderVideos();
            renderQueue();
        }
    }

    async function refresh() {
        try {
            snap = await api("/api/queue");
            render();
        } catch (_) {}
    }

    async function act(path) {
        try {
            snap = await api(path, "POST");
            feedback("");
            render();
        } catch (e) {
            feedback(e.message, "error");
        }
    }

    async function addToQueue() {
        const ids = Array.from(document.querySelectorAll("#fila-videos input:checked")).map(cb => Number(cb.value));
        if (!ids.length) { feedback("Marque pelo menos uma gravação.", "error"); return; }
        const at = document.querySelector('input[name="fila-start"]:checked').value === "at";
        const after = $("fila-after").value;
        let confirmPower = Boolean(snap?.settings?.power_confirmed);
        if (after !== "none" && !confirmPower) {
            const nome = after === "shutdown" ? "DESLIGAR" : "SUSPENDER";
            confirmPower = confirm(`Quando a fila terminar, o programa vai ${nome} o PC, com aviso de 1 minuto para cancelar.\n\nSalve o que estiver aberto em outros programas. Confirmar?`);
            if (!confirmPower) return;
        }
        const sepEl = $("chk-separate-tracks");
        const body = {
            video_ids: ids,
            engine: $("select-engine")?.value || "gemini",
            custom_model: $("select-active-model")?.value || "",
            normalize_audio: Boolean($("chk-normalize-on-process")?.checked),
            separate_tracks: sepEl ? Boolean(sepEl.checked) : null,
            start_time: at ? ($("fila-time").value || "23:00") : "",
            after: after,
            confirm_power: confirmPower,
        };
        $("fila-add").disabled = true;
        try {
            const res = await api("/api/queue/add", "POST", body);
            snap = res;
            const pulados = (res.skipped || []).length;
            feedback(`${res.added.length} gravação(ões) na fila.` + (pulados ? ` ${pulados} ficou(aram) de fora (já estavam na fila ou em processamento).` : ""), "ok");
            if (typeof loadVideos === "function") loadVideos(false);
            render();
        } catch (e) {
            feedback(e.message, "error");
        } finally {
            $("fila-add").disabled = false;
        }
    }

    async function open() {
        buildModal();
        await refresh();
        $("fila-modal").style.display = "flex";
        render();
        if (timer) clearInterval(timer);
        timer = setInterval(() => { if ($("fila-modal").style.display === "flex") refresh(); }, 3000);
    }

    function close() {
        $("fila-modal").style.display = "none";
        if (timer) { clearInterval(timer); timer = null; }
    }

    function addOpenButton() {
        const host = $("btn-open-history");
        if (!host || $("btn-open-fila")) return;
        const btn = document.createElement("button");
        btn.id = "btn-open-fila";
        btn.type = "button";
        btn.className = host.className;
        btn.textContent = "Fila de vídeos";
        btn.title = "Processar várias gravações em sequência, inclusive à noite";
        btn.addEventListener("click", open);
        host.parentNode.insertBefore(btn, host.nextSibling);
    }

    document.addEventListener("DOMContentLoaded", () => {
        addOpenButton();
        refresh();
        setInterval(() => { if (!timer) refresh(); }, 15000);  // mantém o contador no botão e o aviso de desligamento
    });
})();
