let allVideos = [];
let activeVideoId = null;
let pollInterval = null;
let statusHeartbeat = null;
let activeHistoryJobId = null;
let lastRawLogText = "";
let lastYoutubeTagsCsv = "";
let isBatchAudioRunning = false;
let wasBatchAudioRunning = false;
let gameHistory = [];
let activeSegments = [];
let activePreviewUrls = [];
let studioConfig = {
    host_name: "Host",
    group_name: "Banido Studio",
    base_dir: "data",
    obs_dir: "obs_recordings",
    clips_dir: "Cortes",
    voice_bank_dir: "vozes_amigos",
    temp_dir: "temp",
    gpu: ""
};

function revokeAllPreviewUrls() {
    while (activePreviewUrls.length > 0) {
        const u = activePreviewUrls.pop();
        try {
            URL.revokeObjectURL(u);
        } catch (_) {}
    }
}

// Listas preenchidas por GET /api/models/{provider} (fonte única no servidor: app/model_catalog.py).
let GOOGLE_MODELS = [];
let NVIDIA_MODELS = [];

function fillModelSelect(select, models, selected) {
    if (!select) return;
    select.innerHTML = "";
    models.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.value;
        opt.textContent = m.label;
        select.appendChild(opt);
    });
    if (models.length) {
        select.value = models.some(m => m.value === selected) ? selected : models[0].value;
    }
}

async function loadModelCatalog(refresh = false) {
    const q = refresh ? "?refresh=true" : "";
    const [g, n] = await Promise.all([
        fetch(`/api/models/gemini${q}`).then(r => r.json()).catch(() => null),
        fetch(`/api/models/nvidia${q}`).then(r => r.json()).catch(() => null)
    ]);
    if (g && Array.isArray(g.models)) GOOGLE_MODELS = g.models;
    if (n && Array.isArray(n.models)) NVIDIA_MODELS = n.models;
    fillModelSelect(document.getElementById("select-google-default-model"), GOOGLE_MODELS);
    fillModelSelect(document.getElementById("select-nvidia-default-model"), NVIDIA_MODELS);
}

document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
    loadSettings(true);
    loadGameHistory();
    loadVideos(true);
    pollServerStatus();
    statusHeartbeat = setInterval(pollServerStatus, 3000);
});

