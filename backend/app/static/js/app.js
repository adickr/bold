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
  const tbody = document.querySelector("[data-live-tbody]");
  const liveCount = document.querySelector("[data-live-count]");
  let pollTimer = null;
  let seenIds = new Set(
    Array.from(document.querySelectorAll("[data-live-tbody] tr[data-vehicle-id]"))
      .map((tr) => tr.getAttribute("data-vehicle-id"))
      .filter(Boolean)
  );
  let lastDoneSources = 0;
  let finishedHandled = false;

  function zar(value) {
    if (value == null || value === "") return "—";
    return `R${Number(value).toLocaleString("en-ZA")}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function ensureSourceRow(source, label) {
    let li = listEl.querySelector(`[data-source="${source}"]`);
    if (li) return li;
    li = document.createElement("li");
    li.dataset.source = source;
    li.innerHTML = `<span class="dot"></span><span class="label">${escapeHtml(label || source)}</span><span class="meta">waiting</span>`;
    listEl.appendChild(li);
    return li;
  }

  function renderProgress(status) {
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
      else if (s.status === "done") meta.textContent = s.found != null ? `${s.found} found` : "done";
      else if (s.status === "failed") meta.textContent = s.error ? `failed · ${s.error}` : "failed";
      else meta.textContent = "waiting";
    });
  }

  function renderSpot(key, vehicle, titleFallback) {
    const article = document.querySelector(`[data-spot="${key}"]`);
    if (!article) return;
    const heading = article.querySelector("h2");
    const h2 = heading ? heading.outerHTML : `<h2>${titleFallback}</h2>`;
    if (!vehicle) {
      article.innerHTML = `${h2}<p class="muted">${key === "top_deal" ? "No vehicles yet — run collectors." : "None tracked"}</p>`;
      return;
    }
    const priceLine =
      key === "top_deal"
        ? `${zar(vehicle.price)} · score ${vehicle.deal_score ?? "—"}`
        : key === "most_motivated"
          ? `${escapeHtml(vehicle.motivation_level || "—")} · ${zar(vehicle.price)}`
          : zar(vehicle.price);
    article.innerHTML = `${h2}<a href="/vehicles/${vehicle.id}">
      <p class="spot-title">${escapeHtml(vehicle.year || "")} ${escapeHtml(vehicle.variant || "Fortuner")}</p>
      <p class="spot-price">${priceLine}</p>
    </a>`;
  }

  function renderStats(stats) {
    if (!stats) return;
    const map = {
      active_matching: stats.active_matching,
      new_today: stats.new_today,
      reductions_this_week: stats.reductions_this_week,
      median_asking_price: zar(stats.median_asking_price),
    };
    Object.entries(map).forEach(([key, value]) => {
      const el = document.querySelector(`[data-stat="${key}"]`);
      if (el) el.textContent = value ?? "—";
    });
    renderSpot("top_deal", stats.top_deal, "Top deal");
    renderSpot("best_grs", stats.best_grs, "Best GR-S");
    renderSpot("best_vx", stats.best_vx, "Best VX");
    renderSpot("most_motivated", stats.most_motivated, "Most motivated");
  }

  function vehicleRowHtml(v, isNew) {
    const mileage = v.mileage != null ? `${Number(v.mileage).toLocaleString("en-ZA")} km` : "—";
    const primary = v.primary_source && v.primary_source.url
      ? ` · <a href="${escapeHtml(v.primary_source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(v.primary_source.label || "Source")}</a>`
      : "";
    const sources = (v.sources || [])
      .filter((s) => s.status === "active" || s.status === "relisted")
      .filter((s) => s.url)
      .map((s) => `<a class="btn-link external" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.label || s.source)}</a>`)
      .join("");
    const img = v.image
      ? `<a href="${escapeHtml(v.detail_path)}"><img src="${escapeHtml(v.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" /></a>`
      : `<span class="ph">—</span>`;
    return `<tr data-vehicle-id="${escapeHtml(v.id)}" class="${isNew ? "is-new-listing" : ""}">
      <td class="thumb">${img}</td>
      <td>${escapeHtml(v.year || "—")}</td>
      <td>
        <a href="${escapeHtml(v.detail_path)}"><strong>${escapeHtml(v.variant || "Fortuner")}</strong>${v.is_stretch ? ' <em class="tag">stretch</em>' : ""}</a>
        <div class="row-actions">
          <a href="${escapeHtml(v.detail_path)}">Details</a>${primary}
        </div>
      </td>
      <td>${zar(v.price)}</td>
      <td>${mileage}</td>
      <td>${escapeHtml(v.location || "—")}</td>
      <td>${escapeHtml(v.dealer || "—")}</td>
      <td>${escapeHtml(v.days_tracked ?? "—")}</td>
      <td>${v.total_reduction ? zar(v.total_reduction) : "—"}</td>
      <td><span class="score">${v.deal_score != null ? escapeHtml(v.deal_score) : "—"}</span></td>
      <td>${escapeHtml(v.motivation_level || "—")}</td>
      <td class="links-cell">
        <a class="btn-link" href="${escapeHtml(v.detail_path)}">Agent detail</a>
        ${sources}
      </td>
      <td>${escapeHtml(v.shortlist_status || "—")}</td>
    </tr>`;
  }

  function renderVehicles(vehicles, matchingCount) {
    if (!tbody) return;
    if (liveCount) {
      liveCount.textContent = matchingCount != null ? `${matchingCount} matching` : "";
    }
    if (!vehicles || !vehicles.length) {
      tbody.innerHTML = `<tr><td colspan="13"><div class="empty-state"><p class="muted">No vehicles match the current filters yet — keep scanning.</p></div></td></tr>`;
      return;
    }
    const html = vehicles.map((v) => {
      const id = String(v.id);
      const isNew = !seenIds.has(id);
      return vehicleRowHtml(v, isNew);
    }).join("");
    tbody.innerHTML = html;
    vehicles.forEach((v) => seenIds.add(String(v.id)));
  }

  async function poll() {
    try {
      const res = await fetch("/live/snapshot?limit=24", { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`snapshot ${res.status}`);
      const snap = await res.json();
      const status = snap.collect || {};
      renderProgress(status);
      renderStats(snap.stats);
      renderVehicles(snap.vehicles || [], snap.matching_count);

      const doneCount = (status.sources || []).filter((s) => s.status === "done" || s.status === "failed").length;
      if (doneCount > lastDoneSources) {
        lastDoneSources = doneCount;
      }

      if (status.running) {
        pollTimer = setTimeout(poll, 1500);
      } else if (status.finished_at && panel.dataset.autoPoll === "1" && !finishedHandled) {
        finishedHandled = true;
        // One final snapshot already applied — show completion without hard reload
        if (messageEl) {
          messageEl.textContent = status.message || `Scan finished · ${status.ok}/${status.total} sources succeeded`;
        }
        panel.classList.add("is-complete");
        // Keep URL tidy
        if (window.history && window.history.replaceState) {
          const ok = status.ok ?? 0;
          const total = status.total ?? 0;
          window.history.replaceState({}, "", `/?collected=${ok}&total=${total}`);
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
      finishedHandled = false;
      if (messageEl) messageEl.textContent = "Starting live scan…";
      if (percentEl) percentEl.textContent = "2%";
      if (barEl) barEl.style.width = "2%";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Scanning…";
      }
      // Begin polling immediately; POST redirect will also land on ?scanning=1
      setTimeout(poll, 400);
    });
  }

  if (panel.dataset.autoPoll === "1" || panel.classList.contains("is-active")) {
    poll();
  }
})();
