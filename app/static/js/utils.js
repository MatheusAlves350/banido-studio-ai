function formatDuration(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const rem = s % 60;
    if (hrs > 0) return `${hrs}h ${String(mins).padStart(2, "0")}m ${String(rem).padStart(2, "0")}s`;
    if (mins > 0) return `${mins}m ${String(rem).padStart(2, "0")}s`;
    return `${rem}s`;
}

function parseTimestampToSeconds(val) {
    if (typeof val === "number" && !Number.isNaN(val)) return val;
    if (typeof val !== "string") return NaN;
    const trimmed = val.trim();
    if (!trimmed) return NaN;
    const num = Number(trimmed);
    if (!Number.isNaN(num)) return num;
    const parts = trimmed.split(":").map(Number);
    if (parts.some(Number.isNaN)) return NaN;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return NaN;
}

function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// Nome amigável: "2026-11-20 21-14-03 - Overwatch 2 - Título.mkv" -> título + "qui., 20/11/2026 às 21:14"
function describeRecording(filename) {
    const name = String(filename || "").replace(/\.mkv$/i, "");
    const m = name.match(/^(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})(?:-(\d{2}))?(?:\s*-\s*(.+))?$/);
    if (!m) return { title: name, when: "" };
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
    const weekday = d.toLocaleDateString("pt-BR", { weekday: "short" });
    const when = `${weekday} ${m[3]}/${m[2]}/${m[1]} às ${m[4]}:${m[5]}`;
    return { title: m[7] ? m[7] : `Gravação de ${m[3]}/${m[2]}`, when };
}

const STATUS_LABELS = {
    unprocessed: "Não processado",
    processing: "Processando",
    completed: "Pronto",
    error: "Erro"
};

function colorizeLogHtml(rawText) {
    if (!rawText) return `<span style="color:#64748b;">Nenhum log registrado ainda para este vídeo.</span>`;
    return rawText.split("\n").map(line => {
        const safe = escapeHtml(line);
        if (line.includes("[SUCESSO]") || line.includes("✅")) {
            return `<span class="log-line-ok">${safe}</span>`;
        }
        if (line.includes("[ERRO") || line.includes("❌") || line.includes("Traceback") || line.includes("RuntimeError")) {
            return `<span class="log-line-err">${safe}</span>`;
        }
        if (line.includes("[AVISO") || line.includes("🛑") || line.includes("⚠️")) {
            return `<span class="log-line-warn">${safe}</span>`;
        }
        if (line.includes("[IA ")) {
            return `<span class="log-line-ai">${safe}</span>`;
        }
        if (line.includes("[WHISPER]") || line.includes("[SRT]") || line.includes("[VOZES]")) {
            return `<span class="log-line-whisper">${safe}</span>`;
        }
        if (line.includes("[FALA #")) {
            return `<span class="log-line-speech">${safe}</span>`;
        }
        if (line.includes("[FFMPEG]") || line.includes("[MOSAICO]") || line.includes("[TELEMETRIA]") || line.includes("[AUDIO NORM]") || line.includes("[AUDIO LOTE]")) {
            return `<span class="log-line-ffmpeg">${safe}</span>`;
        }
        return safe;
    }).join("\n");
}

