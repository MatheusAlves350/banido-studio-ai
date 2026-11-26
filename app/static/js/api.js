// Proteção CSRF automática: envia o token de sessão em todas as chamadas /api/
const BANIDO_API_TOKEN = document.querySelector('meta[name="banido-token"]')?.getAttribute("content") || "";
const _nativeFetch = window.fetch.bind(window);
window.fetch = function apiFetch(input, init = {}) {
    const url = typeof input === "string" ? input : (input && input.url ? input.url : "");
    if (BANIDO_API_TOKEN && typeof url === "string" && url.startsWith("/api/")) {
        const opts = { ...init };
        const headers = new Headers(opts.headers || {});
        if (!headers.has("X-Banido-Token")) {
            headers.set("X-Banido-Token", BANIDO_API_TOKEN);
        }
        opts.headers = headers;
        return _nativeFetch(input, opts);
    }
    return _nativeFetch(input, init);
};

