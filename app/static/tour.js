// Tour inicial de quatro passos e dicas contextuais (E8).
// Os textos ficam todos em TEXTOS, no começo do arquivo, para serem revisados e trocados com facilidade.
// O que já foi visto fica no navegador (localStorage): só guarda "visto" ou "não visto", nenhum dado pessoal.
(function () {
    "use strict";

    const TEXTOS = {
        tourTitulo: "Como funciona, em 4 passos",
        passos: [
            { alvo: "sidebar-obs-label", titulo: "Coloque as gravações na pasta do OBS",
              texto: "As gravações que o OBS salva aparecem sozinhas na lista à esquerda. Se alguma não aparecer, clique em “Atualizar lista”." },
            { alvo: "step-setup", titulo: "Escolha o jogo e clique em Processar",
              texto: "Escolha uma gravação, diga qual jogo foi e clique em Processar. Pode deixar rodando e usar o PC para outras coisas." },
            { alvo: "flow-steps", titulo: "Confirme os nomes das vozes",
              texto: "Ouça cada trecho e diga quem está falando. Na próxima gravação o programa reconhece sozinho." },
            { alvo: "flow-steps", titulo: "Copie o texto para o YouTube",
              texto: "Na aba “Pacote de publicação” estão o título, a descrição e os capítulos, cada um com um botão de copiar." },
        ],
        botoes: { proximo: "Próximo", voltar: "Voltar", pular: "Pular o tour", fim: "Entendi, vamos começar", dica: "Entendi" },
        contador: (n, total) => `Passo ${n} de ${total}`,
        reabrir: "Mostrar o tour de novo",
    };

    // Uma dica por tela. "tela" é descoberta por telaAtual() abaixo.
    const DICAS = {
        inicio: "Antes do primeiro vídeo, confira a lista “Seu computador está pronto?”. Ela mostra o que falta, e como resolver.",
        processar: "Dica: depois de clicar em Processar, você pode fechar esta tela. O processamento continua e o programa avisa quando terminar.",
        vozes: "Dica: confirme o nome de cada voz só uma vez. Nas próximas gravações o programa já reconhece essa pessoa.",
        publicar: "Dica: use “Copiar tudo” e cole no YouTube Studio. A publicação é feita por você; o programa não envia nada.",
    };

    const K_TOUR = "banido_tour_visto";
    const K_DICAS = "banido_dicas_vistas";

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // O navegador pode bloquear o armazenamento: nesse caso o tour só não "lembra", e tudo continua funcionando.
    function lerLS(chave, padrao) {
        try {
            const v = window.localStorage.getItem(chave);
            return v == null ? padrao : v;
        } catch (_) { return padrao; }
    }
    function gravarLS(chave, valor) {
        try { window.localStorage.setItem(chave, valor); } catch (_) {}
    }
    function dicasVistas() {
        try { return JSON.parse(lerLS(K_DICAS, "[]")) || []; } catch (_) { return []; }
    }

    let passo = 0;
    let tourAberto = false;
    let dicaAtual = null;

    function visivel(el) {
        return !!el && el.offsetParent !== null && el.style.display !== "none";
    }

    function haModalAberto() {
        return Array.from(document.querySelectorAll(".modal-overlay")).some(m => getComputedStyle(m).display !== "none");
    }

    function destacar(id) {
        document.querySelectorAll(".tour-destaque").forEach(e => e.classList.remove("tour-destaque"));
        const el = $(id);
        if (visivel(el)) {
            el.classList.add("tour-destaque");
            el.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
    }

    function fecharTour(marcarVisto) {
        tourAberto = false;
        document.querySelectorAll(".tour-destaque").forEach(e => e.classList.remove("tour-destaque"));
        $("tour-card")?.remove();
        if (marcarVisto) gravarLS(K_TOUR, "1");
    }

    function mostrarPasso() {
        const total = TEXTOS.passos.length;
        const p = TEXTOS.passos[passo];
        let card = $("tour-card");
        if (!card) {
            card = document.createElement("div");
            card.id = "tour-card";
            card.className = "tour-card";
            card.setAttribute("role", "dialog");
            card.setAttribute("aria-live", "polite");
            card.setAttribute("aria-label", TEXTOS.tourTitulo);
            document.body.appendChild(card);
        }
        const ultimo = passo === total - 1;
        card.innerHTML = `
            <div class="tour-topo"><strong>${esc(TEXTOS.tourTitulo)}</strong><span>${esc(TEXTOS.contador(passo + 1, total))}</span></div>
            <h4>${esc(p.titulo)}</h4>
            <p>${esc(p.texto)}</p>
            <div class="tour-acoes">
                <button type="button" class="btn-link" data-tour="pular">${esc(TEXTOS.botoes.pular)}</button>
                <span>
                    ${passo > 0 ? `<button type="button" class="btn-secondary btn-sm" data-tour="voltar">${esc(TEXTOS.botoes.voltar)}</button>` : ""}
                    <button type="button" class="btn-primary btn-sm" data-tour="${ultimo ? "fim" : "proximo"}">${esc(ultimo ? TEXTOS.botoes.fim : TEXTOS.botoes.proximo)}</button>
                </span>
            </div>`;
        card.querySelector(".btn-primary[data-tour]")?.focus();
        destacar(p.alvo);
    }

    function abrirTour() {
        removerDica();
        tourAberto = true;
        passo = 0;
        mostrarPasso();
    }

    function onTourClick(ev) {
        const b = ev.target.closest("[data-tour]");
        if (!b) return;
        const acao = b.dataset.tour;
        if (acao === "proximo") { passo = Math.min(passo + 1, TEXTOS.passos.length - 1); mostrarPasso(); }
        else if (acao === "voltar") { passo = Math.max(passo - 1, 0); mostrarPasso(); }
        else { fecharTour(true); }
    }

    // ------------------------------------------------------------ dicas

    function telaAtual() {
        if (visivel($("empty-state"))) return "inicio";
        if (!visivel($("video-workspace"))) return null;
        if (visivel($("tab-publicar")) || visivel($("tab-youtube"))) return "publicar";
        if (visivel($("tab-vozes"))) return "vozes";
        if (visivel($("step-setup")) && !visivel($("results-container"))) return "processar";
        return null;
    }

    function removerDica() {
        $("dica-card")?.remove();
        dicaAtual = null;
    }

    function mostrarDica(tela) {
        dicaAtual = tela;
        const card = document.createElement("div");
        card.id = "dica-card";
        card.className = "tour-card tour-dica";
        card.setAttribute("role", "status");
        card.innerHTML = `<p>${esc(DICAS[tela])}</p>
            <div class="tour-acoes"><span></span><button type="button" class="btn-secondary btn-sm">${esc(TEXTOS.botoes.dica)}</button></div>`;
        card.querySelector("button").addEventListener("click", () => {
            const vistas = dicasVistas();
            if (!vistas.includes(tela)) vistas.push(tela);
            gravarLS(K_DICAS, JSON.stringify(vistas));
            removerDica();
        });
        document.body.appendChild(card);
    }

    function verificar() {
        if (tourAberto || haModalAberto()) return;
        if (lerLS(K_TOUR, "") !== "1") {
            abrirTour();
            return;
        }
        const tela = telaAtual();
        if (dicaAtual && dicaAtual !== tela) removerDica();
        if (!tela || dicaAtual || !DICAS[tela] || dicasVistas().includes(tela)) return;
        mostrarDica(tela);
    }

    function reiniciar() {
        gravarLS(K_TOUR, "");
        gravarLS(K_DICAS, "[]");
        removerDica();
        abrirTour();
    }

    function botaoReabrir() {
        const host = $("btn-open-obs") || $("btn-open-fila") || $("btn-open-history");
        if (!host || $("btn-tour-de-novo")) return;
        const btn = document.createElement("button");
        btn.id = "btn-tour-de-novo";
        btn.type = "button";
        btn.className = host.className;
        btn.textContent = TEXTOS.reabrir;
        btn.addEventListener("click", reiniciar);
        host.parentNode.insertBefore(btn, host.nextSibling);
    }

    // Para os testes e para quem quiser abrir o tour por outro botão.
    window.BanidoTour = { abrir: reiniciar, textos: TEXTOS, dicas: DICAS };

    document.addEventListener("DOMContentLoaded", () => {
        document.addEventListener("click", e => { if ($("tour-card")?.contains(e.target)) onTourClick(e); });
        // Espera os outros botões da barra lateral existirem antes de acrescentar o seu.
        setTimeout(botaoReabrir, 0);
        setTimeout(verificar, 1500);
        setInterval(verificar, 1500);
    });
})();
