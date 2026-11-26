// =========================================================================
// CONFIGURAÇÕES E CHAVES DE API
// =========================================================================

async function loadSettings(applyToActionBar = false) {
    try {
        if (!GOOGLE_MODELS.length || !NVIDIA_MODELS.length) await loadModelCatalog();
        const res = await fetch("/api/settings");
        const data = await res.json();
        const googleInput = document.getElementById("input-google-key");
        const nvidiaInput = document.getElementById("input-nvidia-key");
        const feedback = document.getElementById("google-key-feedback");
        const nvidiaFeedback = document.getElementById("nvidia-key-feedback");
        const googleModelSelect = document.getElementById("select-google-default-model");
        const nvidiaModelSelect = document.getElementById("select-nvidia-default-model");
        const engineSelect = document.getElementById("select-engine");

        const btnClearGoogle = document.getElementById("btn-clear-google-key");
        const btnClearNvidia = document.getElementById("btn-clear-nvidia-key");

        googleInput.value = "";
        if (data.has_google_api_key) {
            const mask = data.google_api_key_masked || "****";
            googleInput.placeholder = `Chave salva (${mask}) — digite nova para substituir`;
            feedback.innerHTML = `<span class="text-ok">Chave salva (${escapeHtml(mask)}).</span> Deixe o campo em branco para manter a atual.`;
            if (btnClearGoogle) btnClearGoogle.style.display = "inline-block";
        } else {
            googleInput.placeholder = "AIzaSy...";
            feedback.innerHTML = `Gratuita: entre em <a href="https://aistudio.google.com" target="_blank" rel="noopener">aistudio.google.com</a>, clique em “Get API key” e cole aqui.`;
            if (btnClearGoogle) btnClearGoogle.style.display = "none";
        }

        nvidiaInput.value = "";
        if (data.has_nvidia_api_key) {
            const mask = data.nvidia_api_key_masked || "****";
            nvidiaInput.placeholder = `Chave salva (${mask}) — digite nova para substituir`;
            if (nvidiaFeedback) {
                nvidiaFeedback.innerHTML = `<span class="text-ok">Chave salva (${escapeHtml(mask)}).</span> Deixe o campo em branco para manter a atual.`;
            }
            if (btnClearNvidia) btnClearNvidia.style.display = "inline-block";
        } else {
            nvidiaInput.placeholder = "nvapi-...";
            if (nvidiaFeedback) {
                nvidiaFeedback.innerHTML = `Alternativa ao Gemini, com modelos abertos na nuvem da NVIDIA.`;
            }
            if (btnClearNvidia) btnClearNvidia.style.display = "none";
        }

        if (data.google_model && googleModelSelect) {
            const validG = GOOGLE_MODELS.some(m => m.value === data.google_model) ? data.google_model : GOOGLE_MODELS[0].value;
            googleModelSelect.value = validG;
        }
        if (data.nvidia_model && nvidiaModelSelect) {
            const validN = NVIDIA_MODELS.some(m => m.value === data.nvidia_model) ? data.nvidia_model : NVIDIA_MODELS[0].value;
            nvidiaModelSelect.value = validN;
        }

        if (applyToActionBar) {
            if (data.ai_provider && engineSelect) {
                engineSelect.value = data.ai_provider;
            }
            const curEngine = engineSelect ? engineSelect.value : "gemini";
            const preferred = curEngine === "nvidia" ? data.nvidia_model : data.google_model;
            updateActiveModelDropdown(preferred);
        }
    } catch (e) {
        console.error("Erro ao carregar configurações:", e);
    }
}

async function testGoogleKey(showAlert = true) {
    const key = document.getElementById("input-google-key").value.trim();
    const model = document.getElementById("select-google-default-model").value;
    const feedback = document.getElementById("google-key-feedback");
    feedback.innerHTML = `Testando a chave no modelo ${escapeHtml(model)}...`;

    try {
        const res = await fetch("/api/test-google-key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: key, model: model })
        });
        const data = await res.json();
        if (data.success) {
            feedback.innerHTML = `<span class="text-ok">A chave funciona (modelo ${escapeHtml(data.model)}).</span>`;
            if (showAlert) alert(`A chave do Google Gemini funciona (modelo ${data.model}).`);
        } else {
            feedback.innerHTML = `<span class="text-error">A chave não funcionou no modelo ${escapeHtml(data.model)}: ${escapeHtml(data.error)}</span>`;
            if (showAlert) alert("Erro ao testar a chave: " + data.error);
        }
    } catch (e) {
        feedback.innerHTML = `<span style="color: #ef4444;">Erro ao testar: ${e}</span>`;
    }
}

async function testNvidiaKey(showAlert = true) {
    const key = document.getElementById("input-nvidia-key").value.trim();
    const model = document.getElementById("select-nvidia-default-model").value;
    const feedback = document.getElementById("nvidia-key-feedback");
    feedback.innerHTML = `Testando a chave no modelo ${escapeHtml(model)}...`;

    try {
        const res = await fetch("/api/test-nvidia-key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: key, model: model })
        });
        const data = await res.json();
        if (data.success) {
            feedback.innerHTML = `<span class="text-ok">A chave funciona (modelo ${escapeHtml(data.model)}).</span>`;
            if (showAlert) alert(`A chave da NVIDIA funciona (modelo ${data.model}).`);
        } else {
            feedback.innerHTML = `<span class="text-error">A chave não funcionou no modelo ${escapeHtml(data.model)}: ${escapeHtml(data.error)}</span>`;
            if (showAlert) alert("Erro ao testar chave NVIDIA: " + data.error);
        }
    } catch (e) {
        feedback.innerHTML = `<span style="color: #ef4444;">Erro ao testar: ${e}</span>`;
    }
}

async function clearSavedApiKey(provider) {
    const label = provider === "google" ? "Google Gemini" : "NVIDIA NIM";
    if (!confirm(`Deseja remover a chave de API salva do ${label}?`)) return;
    const googleModel = document.getElementById("select-google-default-model").value;
    const nvidiaModel = document.getElementById("select-nvidia-default-model").value;
    const aiProvider = document.getElementById("select-engine").value || "gemini";
    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                clear_google_key: provider === "google",
                clear_nvidia_key: provider === "nvidia",
                ai_provider: aiProvider,
                google_model: googleModel,
                nvidia_model: nvidiaModel
            })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadModelCatalog(true);
        await loadSettings(false);
        alert(`Chave do ${label} removida com sucesso.`);
    } catch (e) {
        alert("Erro ao remover chave: " + e);
    }
}

async function saveSettings() {
    const googleKey = document.getElementById("input-google-key").value.trim();
    const nvidiaKey = document.getElementById("input-nvidia-key").value.trim();
    const googleModel = document.getElementById("select-google-default-model").value;
    const nvidiaModel = document.getElementById("select-nvidia-default-model").value;
    const aiProvider = document.getElementById("select-engine").value || "gemini";

    const payload = {
        ai_provider: aiProvider,
        google_model: googleModel,
        nvidia_model: nvidiaModel
    };
    if (googleKey) payload.google_api_key = googleKey;
    if (nvidiaKey) payload.nvidia_api_key = nvidiaKey;

    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        if (googleKey || nvidiaKey) {
            await loadModelCatalog(true);
        }
        alert("Configurações e modelos salvos com sucesso!");
        await loadSettings(false);
        const preferred = aiProvider === "nvidia" ? nvidiaModel : googleModel;
        updateActiveModelDropdown(preferred);
        document.getElementById("settings-modal").style.display = "none";
    } catch (e) {
        alert("Erro ao salvar configurações: " + e);
    }
}

async function removeVideoAudioBackup() {
    if (!activeVideoId) return;
    if (!confirm("Remover a cópia do áudio original deste vídeo?\n\nA imagem e as demais faixas não são refeitas, então a qualidade não muda. Só faça isso depois de conferir que o volume ficou bom.")) {
        return;
    }
    const btn = document.getElementById("btn-remove-audio-backup");
    const origTxt = btn ? btn.textContent : "";
    try {
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Removendo...";
        }
        const res = await fetch(`/api/audio/remove-backup/${activeVideoId}`, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        alert(data.message || "Backup removido com sucesso.");
        if (btn && !data.skipped) btn.style.display = "none";
        await loadVideos(false);
    } catch (e) {
        alert("Não foi possível remover a cópia do áudio original: " + e.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = origTxt;
        }
    }
}

async function resumePausedProcessing() {
    if (!activeVideoId) return;
    const btn = document.getElementById("btn-resume-paused");
    try {
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Retomando...";
        }
        const res = await fetch(`/api/process/${activeVideoId}/resume-override`, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        if (btn) btn.style.display = "none";
    } catch (e) {
        alert("Não foi possível retomar o processamento: " + e.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Continuar mesmo assim";
        }
    }
}
