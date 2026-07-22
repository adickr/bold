document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const sel = btn.getAttribute("data-copy");
    const el = document.querySelector(sel);
    if (!el) return;
    const text = "value" in el ? el.value : el.textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = "Copy message"; }, 1500);
    } catch (err) {
      btn.textContent = "Copy failed";
    }
  });
});

(function collectProgress() {
  const panel = document.querySelector("[data-collect-progress]");
  const form = document.querySelector("[data-collect-form]");
  if (!panel) return;

  const messageEl = panel.querySelector("[data-collect-message]");
  const percentEl = panel.querySelector("[data-collect-percent]");
  const barEl = panel.querySelector("[data-collect-bar]");
  const listEl = panel.querySelector("[data-collect-sources]");
  const btn = document.querySelector("[data-collect-btn]");
  let pollTimer = null;
  let finishedReloadTimer = null;

  function ensureSourceRow(source, label) {
    let li = listEl.querySelector(`[data-source="${source}"]`);
    if (li) return li;
    li = document.createElement("li");
    li.dataset.source = source;
    li.innerHTML = `<span class="dot"></span><span class="label">${label || source}</span><span class="meta">waiting</span>`;
    listEl.appendChild(li);
    return li;
  }

  function render(status) {
    if (!status) return;
    const running = !!status.running;
    const percent = Number(status.percent || 0);
    panel.classList.toggle("is-active", running);
    panel.classList.toggle("is-complete", !running && percent >= 100 && !!status.finished_at);
    if (messageEl) messageEl.textContent = status.message || (running ? "Scanning…" : "Ready");
    if (percentEl) percentEl.textContent = `${percent}%`;
    if (barEl) barEl.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? "Scanning…" : "Collect live listings now";
    }
    (status.sources || []).forEach((s) => {
      const li = ensureSourceRow(s.source, s.label);
      li.dataset.status = s.status || "pending";
      const meta = li.querySelector(".meta");
      if (!meta) return;
      if (s.status === "running") meta.textContent = "scanning…";
      else if (s.status === "done") {
        meta.textContent = s.found != null ? `${s.found} found` : "done";
      } else if (s.status === "failed") meta.textContent = s.error ? `failed · ${s.error}` : "failed";
      else meta.textContent = "waiting";
    });
  }

  async function poll() {
    try {
      const res = await fetch("/collect/status", { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const status = await res.json();
      render(status);
      if (status.running) {
        pollTimer = setTimeout(poll, 1200);
      } else if (status.finished_at && panel.dataset.autoPoll === "1") {
        // Reload once so dashboard numbers refresh after the scan
        if (!finishedReloadTimer) {
          finishedReloadTimer = setTimeout(() => {
            const ok = status.ok ?? 0;
            const total = status.total ?? 0;
            window.location.href = `/?collected=${ok}&total=${total}`;
          }, 900);
        }
      }
    } catch (err) {
      pollTimer = setTimeout(poll, 2000);
    }
  }

  if (form) {
    form.addEventListener("submit", () => {
      panel.classList.add("is-active");
      panel.dataset.autoPoll = "1";
      if (messageEl) messageEl.textContent = "Starting live scan…";
      if (percentEl) percentEl.textContent = "2%";
      if (barEl) barEl.style.width = "2%";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Scanning…";
      }
    });
  }

  if (panel.dataset.autoPoll === "1" || panel.classList.contains("is-active")) {
    poll();
  }
})();
