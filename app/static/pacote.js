// Pacote de publicação (E2): título, descrição, tags e capítulos em blocos, cada um com botão de copiar.
// A publicação continua manual: nada é enviado a lugar nenhum.
(function () {
    "use strict";

    const LIMITS = { title: 100, desc: 5000, tags: 500 };
    let pack = { desc: "", tags: "", chapters: [] };

    function $(id) { return document.getElementById(id); }
    function fmt(n) { return Number(n).toLocaleString("pt-BR"); }

    function chaptersText() {
        return (pack.chapters || [])
            .map(c => `${c.timestamp || c.time || "00:00:00"} - ${c.title || c.name || "Capítulo"}`)
            .join("\n");
    }

    function currentTitle() {
        return ($("custom-title-input")?.value || "").trim();
    }

    function values() {
        return { title: currentTitle(), desc: pack.desc, tags: pack.tags, chapters: chaptersText() };
    }

    function badge(id, len, limit, unit) {
        const el = $(id);
        if (!el) return;
        el.textContent = `${fmt(len)} / ${fmt(limit)}${unit || ""}`;
        el.style.color = len > limit ? "var(--err)" : (len >= limit * 0.9 ? "var(--warn)" : "");
    }

    function refresh() {
        const v = values();
        $("pack-title").value = v.title;
        $("pack-desc").value = v.desc;
        $("pack-tags").value = v.tags;
        $("pack-chapters").value = v.chapters;
        badge("pack-title-count", v.title.length, LIMITS.title);
        badge("pack-desc-count", v.desc.length, LIMITS.desc);
        badge("pack-tags-count", v.tags.length, LIMITS.tags);
        const n = (pack.chapters || []).length;
        const ch = $("pack-chapters-count");
        ch.textContent = `${n} capítulo${n === 1 ? "" : "s"}`;
        ch.style.color = n > 0 && n < 3 ? "var(--warn)" : "";
        $("pack-empty").hidden = Boolean(v.title || v.desc || n);
    }

    async function copyText(text, button, label) {
        if (!text) {
            flash(button, "Nada para copiar");
            return;
        }
        try {
            await navigator.clipboard.writeText(text);
        } catch (_) {
            // Navegadores que bloqueiam a área de transferência: seleciona o texto para o usuário copiar com Ctrl+C.
            const area = document.createElement("textarea");
            area.value = text;
            document.body.appendChild(area);
            area.select();
            try { document.execCommand("copy"); } catch (_) {}
            area.remove();
        }
        flash(button, label || "Copiado!");
    }

    function flash(button, text) {
        if (!button) return;
        const original = button.dataset.original || button.textContent;
        button.dataset.original = original;
        button.textContent = text;
        clearTimeout(button._t);
        button._t = setTimeout(() => { button.textContent = original; }, 1800);
    }

    // Ordem do formulário do YouTube Studio: título, descrição, tags.
    function allText() {
        const v = values();
        const partes = [];
        if (v.title) partes.push(`TÍTULO\n${v.title}`);
        if (v.desc) partes.push(`DESCRIÇÃO\n${v.desc}`);
        if (v.tags) partes.push(`TAGS\n${v.tags}`);
        return partes.join("\n\n");
    }

    // Chamada por videos.js quando um vídeo pronto é exibido.
    window.renderPublishPack = function (data) {
        pack = {
            desc: (data && data.youtube_description) || "",
            tags: (data && data.youtube_tags_csv) || "",
            chapters: (data && data.chapters) || [],
        };
        refresh();
    };

    document.addEventListener("DOMContentLoaded", () => {
        $("pack-copy-all")?.addEventListener("click", e => copyText(allText(), e.currentTarget, "Tudo copiado!"));
        document.querySelectorAll("[data-pack-copy]").forEach(btn => {
            btn.addEventListener("click", () => copyText(values()[btn.dataset.packCopy], btn));
        });
        // O título pode ter sido trocado na outra aba: atualiza ao abrir esta.
        $("tab-btn-publicar")?.addEventListener("click", refresh);
        document.querySelector('[data-step="4"]')?.addEventListener("click", () => setTimeout(refresh, 0));
    });
})();
