// Mosaicos para anexar no Claude Web (B8): lista de imagens (sessão inteira, por hora, mudanças de cena).
async function refreshMosaicFiles(videoId) {
    const box = document.getElementById("claude-mosaic-files");
    const list = document.getElementById("claude-mosaic-list");
    if (!box || !list) return;
    try {
        const res = await fetch(`/api/mosaics/${videoId}`);
        if (videoId !== activeVideoId || !res.ok) return;
        const d = await res.json();
        list.innerHTML = "";
        (d.files || []).forEach(f => {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = f.url;
            a.download = f.name;
            a.textContent = `${f.label} (baixar)`;
            li.appendChild(a);
            list.appendChild(li);
        });
        box.hidden = list.children.length === 0;
    } catch (_) {
        box.hidden = true;
    }
}
