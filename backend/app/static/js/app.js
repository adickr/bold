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
  const listingsSection = document.querySelector("[data-live-listings]");
  const loadMoreWrap = document.querySelector("[data-load-more-wrap]");
  const loadMoreBtn = document.querySelector("[data-load-more]");
  const loadMoreMeta = document.querySelector("[data-load-more-meta]");
  const PAGE_SIZE = 12;
  let listLimit = Number(listingsSection?.getAttribute("data-list-limit") || PAGE_SIZE);
  let matchingTotal = Number(listingsSection?.getAttribute("data-matching-count") || 0);
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
      else if (s.status === "done") {
        const bits = [];
        if (s.found != null) bits.push(`${s.found} found`);
        if (s.new) bits.push(`${s.new} new`);
        if (s.updated) bits.push(`${s.updated} updated`);
        meta.textContent = bits.length ? bits.join(" · ") : "done";
      } else if (s.status === "failed") meta.textContent = s.error ? `failed · ${s.error}` : "failed";
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
    const src = vehicle.primary_source || {};
    const href = src.url || vehicle.detail_path || `/vehicles/${vehicle.id}`;
    const external = Boolean(src.url);
    const cta = src.label ? `Open on ${src.label} →` : "Open listing →";
    const target = external ? ` target="_blank" rel="noopener noreferrer"` : "";
    article.innerHTML = `${h2}<a class="spot-card" href="${escapeHtml(href)}"${target}>
      <p class="spot-title">${escapeHtml(vehicle.year || "")} ${escapeHtml(vehicle.variant || "Fortuner")}</p>
      <p class="spot-price">${priceLine}</p>
      <span class="spot-cta">${escapeHtml(cta)}</span>
    </a>`;
  }

  function renderMarketSpark(history) {
    const root = document.querySelector("[data-market-spark]");
    if (!root) return;
    const chart = root.querySelector("[data-spark-chart]");
    const label = root.querySelector("[data-spark-label]");
    if (label && history && history.label) label.textContent = history.label;
    if (!chart) return;

    const points = (history && history.points) || [];
    const known = points
      .map((p, idx) => ({ idx, value: p.active_count, date: p.date }))
      .filter((p) => p.value != null);
    if (!known.length) {
      chart.innerHTML = `<p class="spark-empty">History builds as daily snapshots accumulate.</p>`;
      return;
    }

    const values = known.map((p) => Number(p.value));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = max === min ? Math.max(2, Math.round(max * 0.05) || 2) : 0;
    const lo = min - pad;
    const hi = max + pad || 1;
    const w = 280;
    const h = 64;
    const left = 2;
    const right = 2;
    const top = 6;
    const bottom = 6;
    const innerW = w - left - right;
    const innerH = h - top - bottom;
    const n = Math.max(points.length - 1, 1);

    function xy(idx, value) {
      const x = left + (idx / n) * innerW;
      const y = top + innerH - ((value - lo) / (hi - lo || 1)) * innerH;
      return [x, y];
    }

    // One datapoint → flat line across the window so the chart still reads as a chart
    const linePts = known.length === 1
      ? [xy(0, known[0].value), xy(n, known[0].value)]
      : known.map((p) => xy(p.idx, p.value));
    const line = linePts.map((pt, i) => `${i === 0 ? "M" : "L"}${pt[0].toFixed(1)} ${pt[1].toFixed(1)}`).join(" ");
    const first = linePts[0];
    const last = linePts[linePts.length - 1];
    const area = `${line} L${last[0].toFixed(1)} ${(top + innerH).toFixed(1)} L${first[0].toFixed(1)} ${(top + innerH).toFixed(1)} Z`;
    const tipVal = values[values.length - 1];
    const thinHistory = known.length < 2;
    const title = thinHistory
      ? `Today · ${tipVal} active`
      : `${known[0].date} → ${known[known.length - 1].date}`;

    if (label) {
      label.textContent = thinHistory
        ? `Today · ${tipVal} active matches (trend builds daily)`
        : (history && history.label) || "Active matches · last 30 days";
    }

    chart.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(title)}">
      <path class="spark-area" d="${area}"></path>
      <path class="spark-line" d="${line}"></path>
      <circle class="spark-dot" cx="${tip[0].toFixed(1)}" cy="${tip[1].toFixed(1)}" r="2.6"></circle>
    </svg>`;
  }

  function renderStats(stats) {
    if (!stats) return;
    const map = {
      active_matching: stats.active_matching,
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
    renderPriceDistribution(stats.price_distribution || [], stats.median_asking_price);
    renderLastFetch(stats.last_fetch);
    renderMarketSpark(stats.market_history);
  }

  function formatFetchTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const diffSec = Math.round((now - d) / 1000);
    let relative = "";
    if (diffSec < 60) relative = "just now";
    else if (diffSec < 3600) relative = `${Math.floor(diffSec / 60)}m ago`;
    else if (diffSec < 86400) relative = `${Math.floor(diffSec / 3600)}h ago`;
    else relative = `${Math.floor(diffSec / 86400)}d ago`;
    const absolute = d.toLocaleString(undefined, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${absolute} (${relative})`;
  }

  let lastFetchState = null;

  function renderLastFetch(lastFetch) {
    lastFetchState = lastFetch || null;
    const root = document.querySelector("[data-fetch-meta]");
    if (!root) return;
    const empty = root.querySelector("[data-fetch-empty]");
    let line = root.querySelector(".fetch-line:not([data-fetch-empty])");
    const highlights = root.querySelector("[data-fetch-highlights]");
    const actions = root.querySelector(".fetch-actions");
    const btn = root.querySelector("[data-what-changed-btn]");
    const dialog = document.querySelector("[data-changes-dialog]");

    if (!lastFetch || !lastFetch.last_fetch_at) {
      if (line && !line.matches("[data-fetch-empty]")) line.remove();
      if (empty) {
        empty.hidden = false;
      } else {
        const p = document.createElement("p");
        p.className = "fetch-line muted";
        p.dataset.fetchEmpty = "1";
        p.innerHTML = 'No fetch yet — run <strong>Collect live listings now</strong>.';
        root.prepend(p);
      }
      if (highlights) {
        highlights.innerHTML = "";
        highlights.hidden = true;
      }
      if (actions) actions.hidden = true;
      renderChangesDialog(null);
      return;
    }

    if (empty) empty.remove();
    if (!line) {
      line = document.createElement("p");
      line.className = "fetch-line";
      line.innerHTML = `<span class="muted">Last fetch</span>
        <time class="mono" data-fetch-at></time>
        <span class="fetch-sep">·</span>
        <span class="fetch-changes" data-fetch-changes></span>`;
      root.prepend(line);
    }
    const timeEl = line.querySelector("[data-fetch-at]");
    const changesEl = line.querySelector("[data-fetch-changes]");
    if (timeEl) {
      timeEl.setAttribute("datetime", lastFetch.last_fetch_at);
      timeEl.textContent = formatFetchTime(lastFetch.last_fetch_at);
    }
    if (changesEl) {
      changesEl.textContent = lastFetch.summary || "No new stock or price cuts";
      changesEl.classList.toggle("has-changes", !!lastFetch.has_material);
      changesEl.classList.toggle("no-changes", !lastFetch.has_material);
    }
    if (highlights) {
      const items = lastFetch.highlights || [];
      if (!items.length) {
        highlights.innerHTML = "";
        highlights.hidden = true;
      } else {
        highlights.hidden = false;
        highlights.innerHTML = items
          .slice(0, 6)
          .map((h) => {
            const detail = h.detail ? ` <span class="mono">${escapeHtml(h.detail)}</span>` : "";
            return `<li data-kind="${escapeHtml(h.kind || "")}"><a href="${escapeHtml(h.href || "#")}">${escapeHtml(h.label || "Change")}${detail}</a></li>`;
          })
          .join("");
      }
    }
    if (actions) actions.hidden = false;
    if (btn) {
      btn.hidden = false;
      btn.textContent = lastFetch.updates_label || "Show all updates";
      wireWhatChangedButton(btn);
    }
    renderChangesDialog(lastFetch);
    if (dialog && dialog.open) {
      // keep open content fresh during live collect
    }
  }

  function fmtKm(value) {
    if (value == null || value === "") return "";
    return `${Number(value).toLocaleString("en-ZA").replace(/,/g, " ")} km`;
  }

  function renderChangesDialog(lastFetch) {
    const body = document.querySelector("[data-changes-body]");
    const summaryEl = document.querySelector("[data-changes-summary]");
    if (!body) return;

    if (!lastFetch || !lastFetch.last_fetch_at) {
      if (summaryEl) summaryEl.textContent = "No fetch yet";
      body.innerHTML = `<p class="muted" data-changes-empty>Run a collect to see what changed.</p>`;
      return;
    }

    const when = formatFetchTime(lastFetch.last_fetch_at);
    if (summaryEl) {
      const head = lastFetch.full_summary || lastFetch.summary || "";
      summaryEl.innerHTML = `${escapeHtml(head)} · last fetch <time datetime="${escapeHtml(lastFetch.last_fetch_at)}">${escapeHtml(when)}</time>`;
    }

    const changes = lastFetch.changes || {};
    const newItems = changes.new || [];
    const cutItems = changes.price_cuts || [];
    const updateItems = changes.updates || [];
    if (!lastFetch.has_changes) {
      body.innerHTML = `<p class="muted" data-changes-empty>No listing changes in the last scan.</p>`;
      return;
    }

    let html = "";
    if (newItems.length) {
      html += `<section><h3>New listings <span class="mono">${escapeHtml(lastFetch.new ?? newItems.length)}</span></h3><ul class="changes-list">`;
      html += newItems
        .map((item) => {
          const scoreBit = item.deal_score != null ? `score ${escapeHtml(item.deal_score)} · ` : "";
          const metaBits = [fmtKm(item.mileage), item.location].filter(Boolean).join(" · ");
          return `<li><a href="${escapeHtml(item.href || "#")}"><strong>${escapeHtml(item.title || "Fortuner")}</strong><span class="mono">${scoreBit}${escapeHtml(item.price_label || "—")}</span>${metaBits ? `<span class="muted tiny">${escapeHtml(metaBits)}</span>` : ""}</a></li>`;
        })
        .join("");
      html += `</ul></section>`;
    }
    if (cutItems.length) {
      html += `<section><h3>Price cuts <span class="mono">${escapeHtml(lastFetch.price_cuts ?? cutItems.length)}</span></h3><ul class="changes-list">`;
      html += cutItems
        .map((item) => {
          const priceBit = item.change_label
            ? `${escapeHtml(item.change_label)}${item.price_label ? ` → ${escapeHtml(item.price_label)}` : ""}`
            : escapeHtml(item.price_label || "—");
          const loc = item.location ? `<span class="muted tiny">${escapeHtml(item.location)}</span>` : "";
          return `<li data-kind="cut"><a href="${escapeHtml(item.href || "#")}"><strong>${escapeHtml(item.title || "Fortuner")}</strong><span class="mono cut">${priceBit}</span>${loc}</a></li>`;
        })
        .join("");
      html += `</ul></section>`;
    }
    if (updateItems.length) {
      const updatedTotal = Number(lastFetch.updated || 0);
      const countLabel = updatedTotal > updateItems.length
        ? `${updateItems.length}/${updatedTotal}`
        : String(updateItems.length);
      html += `<section><h3>Detail updates <span class="mono">${escapeHtml(countLabel)}</span></h3>`;
      html += `<p class="muted tiny section-note">Mileage, dealer, title, and other field changes from the last scan.</p>`;
      html += `<ul class="changes-list">`;
      html += updateItems
        .map((item) => {
          const detail = escapeHtml(item.change_summary || (item.details || []).join(" · ") || "Updated");
          const loc = item.location ? `<span class="muted tiny">${escapeHtml(item.location)}</span>` : "";
          return `<li data-kind="update"><a href="${escapeHtml(item.href || "#")}"><strong>${escapeHtml(item.title || "Fortuner")}</strong><span class="change-detail">${detail}</span>${loc}</a></li>`;
        })
        .join("");
      html += `</ul></section>`;
    } else if (Number(lastFetch.updated || 0) > 0) {
      html += `<section><h3>Detail updates <span class="mono">${escapeHtml(lastFetch.updated)}</span></h3>`;
      html += `<p class="muted">${Number(lastFetch.updated)} listing${Number(lastFetch.updated) === 1 ? "" : "s"} updated, but no field-level detail was stored for this scan.</p></section>`;
    }
    if (!html) {
      html = `<p class="muted" data-changes-empty>Changes were counted but no detail rows are available yet.</p>`;
    }
    body.innerHTML = html;
  }

  function openChangesDialog() {
    const dialog = document.querySelector("[data-changes-dialog]");
    if (!dialog) return;
    renderChangesDialog(lastFetchState);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function wireWhatChangedButton(btn) {
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      openChangesDialog();
    });
  }

  document.querySelectorAll("[data-what-changed-btn]").forEach(wireWhatChangedButton);

  // Seed dialog state from server-rendered JSON
  (function seedLastFetchFromDom() {
    const raw = document.querySelector("[data-last-fetch-json]");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw.textContent || "null");
      if (parsed && parsed.last_fetch_at) {
        lastFetchState = parsed;
      }
    } catch (_err) {
      /* ignore malformed bootstrap payload */
    }
  })();

  // Format any server-rendered last-fetch timestamp on load
  document.querySelectorAll("[data-fetch-at]").forEach((el) => {
    const iso = el.getAttribute("datetime");
    if (iso) el.textContent = formatFetchTime(iso);
  });

  function renderPriceDistribution(buckets, medianPrice) {
    const section = document.querySelector("[data-price-dist]");
    if (!section) return;
    const headMeta = section.querySelector(".section-head .mono, .section-head [data-price-median]");
    if (headMeta && medianPrice != null) {
      headMeta.textContent = `Median ${zar(medianPrice)}`;
    }
    let chart = section.querySelector("[data-price-chart]");
    const empty = section.querySelector("[data-price-empty]");
    if (!buckets.length) {
      if (chart) chart.remove();
      if (empty) {
        empty.hidden = false;
      } else {
        const p = document.createElement("p");
        p.className = "muted";
        p.dataset.priceEmpty = "1";
        p.textContent = "No priced matches yet — run a collect to fill the chart.";
        section.appendChild(p);
      }
      return;
    }
    if (empty) empty.remove();
    const maxCount = Math.max(1, ...buckets.map((b) => Number(b.count) || 0));
    if (!chart) {
      chart = document.createElement("div");
      chart.className = "price-chart";
      chart.dataset.priceChart = "1";
      section.appendChild(chart);
    }
    chart.style.setProperty("--max-count", String(maxCount));
    chart.innerHTML = buckets
      .map((b) => {
        const count = Number(b.count) || 0;
        const label = escapeHtml(b.label || "");
        const emptyCls = count === 0 ? " is-empty" : "";
        const n = count ? String(count) : "";
        return `<div class="price-bar${emptyCls}" style="--n: ${count}" title="${count} car${count === 1 ? "" : "s"} · ${label}">
          <span class="n">${n}</span>
          <span class="bar" aria-hidden="true"></span>
          <span class="lbl">${label}</span>
        </div>`;
      })
      .join("");
  }

  function scoreCellHtml(v) {
    if (v.deal_score == null) return `<span class="score">—</span>`;
    if (!v.deal_score_breakdown) {
      return `<span class="score">${escapeHtml(v.deal_score)}</span>`;
    }
    const payload = escapeHtml(JSON.stringify(v.deal_score_breakdown));
    return `<span class="score score-tip" tabindex="0" data-tip-kind="deal" data-score-tip="${payload}">${escapeHtml(v.deal_score)}</span>`;
  }

  function motivationCellHtml(v) {
    if (!v.motivation_level) return `<span class="score">—</span>`;
    if (!v.motivation_breakdown) {
      return `<span class="score">${escapeHtml(v.motivation_level)}</span>`;
    }
    const payload = escapeHtml(JSON.stringify(v.motivation_breakdown));
    return `<span class="score score-tip motivation-tip" tabindex="0" data-tip-kind="motivation" data-score-tip="${payload}">${escapeHtml(v.motivation_level)}</span>`;
  }

  function vehicleRowHtml(v, isNew) {
    const mileage = v.mileage != null ? `${Number(v.mileage).toLocaleString("en-ZA")} km` : "—";
    const sources = (v.sources || [])
      .filter((s) => s.status === "active" || s.status === "relisted")
      .filter((s) => s.url)
      .map((s) => `<a class="btn-link external" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.label || s.source)}</a>`)
      .join("");
    const linksHtml = sources
      ? `<div class="source-links">${sources}</div>`
      : `<div class="source-links"><span class="muted">—</span></div>`;
    const vote = v.vote || "";
    const rowClass = [
      isNew ? "is-new-listing" : "",
      vote === "down" ? "is-thumbs-down" : "",
      vote === "up" ? "is-thumbs-up" : "",
    ].filter(Boolean).join(" ");
    return `<tr data-vehicle-id="${escapeHtml(v.id)}" class="${rowClass}" data-vote="${escapeHtml(vote)}">
      <td>${escapeHtml(v.year || "—")}</td>
      <td>
        <a href="${escapeHtml(v.detail_path)}"><strong>${escapeHtml(v.variant || "Fortuner")}</strong>${v.is_stretch ? ' <em class="tag">stretch</em>' : ""}</a>
      </td>
      <td>${zar(v.price)}</td>
      <td>${mileage}</td>
      <td>${escapeHtml(v.location || "—")}</td>
      <td>${escapeHtml(v.dealer || "—")}</td>
      <td>${escapeHtml(v.days_tracked ?? "—")}</td>
      <td>${v.total_reduction ? zar(v.total_reduction) : "—"}</td>
      <td>${scoreCellHtml(v)}</td>
      <td>${motivationCellHtml(v)}</td>
      <td class="links-cell">${linksHtml}</td>
      <td class="vote-cell">
        <div class="vote-controls" data-vote-controls data-vehicle-id="${escapeHtml(v.id)}">
          <button type="button" class="vote-btn vote-up${vote === "up" ? " is-active" : ""}" data-vote="up" aria-label="Thumbs up" aria-pressed="${vote === "up" ? "true" : "false"}">👍</button>
          <button type="button" class="vote-btn vote-down${vote === "down" ? " is-active" : ""}" data-vote="down" aria-label="Thumbs down" aria-pressed="${vote === "down" ? "true" : "false"}">👎</button>
        </div>
      </td>
    </tr>`;
  }

  function updateLoadMore(shown, matchingCount) {
    matchingTotal = matchingCount != null ? Number(matchingCount) : matchingTotal;
    if (listingsSection) {
      listingsSection.setAttribute("data-list-limit", String(listLimit));
      listingsSection.setAttribute("data-matching-count", String(matchingTotal || 0));
    }
    if (!loadMoreWrap) return;
    const hasMore = matchingTotal > shown && shown > 0;
    loadMoreWrap.hidden = !hasMore;
    if (loadMoreMeta) {
      loadMoreMeta.textContent = matchingTotal
        ? `Showing ${shown} of ${matchingTotal}`
        : "";
    }
    if (loadMoreBtn) {
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = "Load more";
    }
  }

  function renderVehicles(vehicles, matchingCount, nationwideCount) {
    if (!tbody) return;
    if (liveCount) {
      liveCount.textContent = matchingCount != null ? `${matchingCount} matching` : "";
    }
    if (!vehicles || !vehicles.length) {
      let extra = "";
      if (nationwideCount > 0) {
        extra = `<p>${nationwideCount} active nationwide — none match WC · 4x4 · ≤100k km yet.
          <a class="btn-link" href="/listings?sort=deal_score_desc&amp;drivetrain=4x4&amp;max_mileage=100000">Show all SA</a>
          · <a class="btn-link" href="/listings?sort=deal_score_desc">Show everything</a></p>`;
      } else {
        extra = `<p class="muted">Keep scanning — results appear here as each source finishes.</p>`;
      }
      tbody.innerHTML = `<tr><td colspan="12"><div class="empty-state"><p class="muted">No vehicles match the current filters yet.</p>${extra}</div></td></tr>`;
      updateLoadMore(0, matchingCount || 0);
      return;
    }
    const html = vehicles.map((v) => {
      const id = String(v.id);
      const isNew = !seenIds.has(id);
      return vehicleRowHtml(v, isNew);
    }).join("");
    tbody.innerHTML = html;
    vehicles.forEach((v) => seenIds.add(String(v.id)));
    updateLoadMore(vehicles.length, matchingCount);
  }

  async function fetchSnapshot() {
    const res = await fetch(`/live/snapshot?limit=${listLimit}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`snapshot ${res.status}`);
    return res.json();
  }

  async function poll() {
    try {
      const snap = await fetchSnapshot();
      const status = snap.collect || {};
      renderProgress(status);
      renderStats(snap.stats);
      renderVehicles(snap.vehicles || [], snap.matching_count, snap.nationwide_count);

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

  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", async () => {
      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = "Loading…";
      listLimit += PAGE_SIZE;
      try {
        const snap = await fetchSnapshot();
        renderStats(snap.stats);
        renderVehicles(snap.vehicles || [], snap.matching_count, snap.nationwide_count);
      } catch (_err) {
        listLimit = Math.max(PAGE_SIZE, listLimit - PAGE_SIZE);
        loadMoreBtn.disabled = false;
        loadMoreBtn.textContent = "Load more";
      }
    });
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

  // Paint sparkline from server-rendered history on first load
  (function paintInitialSpark() {
    const root = document.querySelector("[data-market-spark]");
    if (!root) return;
    try {
      const raw = root.getAttribute("data-history");
      if (raw) renderMarketSpark(JSON.parse(raw));
    } catch (_err) {
      /* ignore */
    }
  })();
})();

(function scoreTipPopup() {
  const DEAL_LABELS = {
    price_value: "Price value",
    completeness: "Completeness",
    mileage: "Mileage",
    reduction_history: "Reduction history",
    time_on_market: "Time on market",
    history_quality: "History quality",
    risk_penalty: "Risk penalty",
    total: "Total",
  };
  const DEAL_ORDER = [
    "price_value",
    "completeness",
    "mileage",
    "reduction_history",
    "time_on_market",
    "history_quality",
    "risk_penalty",
    "total",
  ];
  const MOTIVATION_LABELS = {
    days_on_market: "Days on market",
    price_reductions: "Price reductions",
    reduction_timing: "Reduction timing",
    multi_site: "Multi-site listing",
    dealer_stock: "Dealer similar stock",
    timing: "End-of-month timing",
    language_cues: "Clearance language",
    total: "Total",
  };
  const MOTIVATION_ORDER = [
    "days_on_market",
    "price_reductions",
    "reduction_timing",
    "multi_site",
    "dealer_stock",
    "timing",
    "language_cues",
    "total",
  ];
  const SKIP = new Set(["note", "inferred", "estimate_only", "reasons", "level", "score"]);

  const tip = document.createElement("div");
  tip.className = "score-tip-float";
  tip.hidden = true;
  tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);

  let hideTimer = null;
  let activeEl = null;

  function escapeText(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseBreakdown(el) {
    const raw = el.getAttribute("data-score-tip");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (_err) {
      return null;
    }
  }

  function renderBreakdown(el, breakdown) {
    const kind = el.getAttribute("data-tip-kind") || "deal";
    const labels = kind === "motivation" ? MOTIVATION_LABELS : DEAL_LABELS;
    const order = kind === "motivation" ? MOTIVATION_ORDER : DEAL_ORDER;
    const keys = order.filter((k) => breakdown[k] != null);
    for (const k of Object.keys(breakdown)) {
      if (!keys.includes(k) && !SKIP.has(k) && typeof breakdown[k] === "number") {
        keys.push(k);
      }
    }
    const rows = keys
      .map((k) => {
        const label = labels[k] || k.replace(/_/g, " ");
        return `<li data-key="${escapeText(k)}"><span>${escapeText(label)}</span><strong>${escapeText(breakdown[k])}</strong></li>`;
      })
      .join("");
    let body = rows
      ? `<ul class="score-break">${rows}</ul>`
      : "";
    if (!rows && Array.isArray(breakdown.reasons) && breakdown.reasons.length) {
      body = `<ul class="score-break">${breakdown.reasons
        .map((r) => `<li><span>${escapeText(String(r).replace(/_/g, " "))}</span><strong>✓</strong></li>`)
        .join("")}</ul>`;
    }
    const note = breakdown.note
      ? `<span class="tip-note">${escapeText(breakdown.note)}</span>`
      : "";
    let title = kind === "motivation" ? "Motivation breakdown" : "Deal score breakdown";
    if (kind === "motivation") {
      const level = breakdown.level ? String(breakdown.level).replace(/_/g, " ") : "";
      const score = breakdown.score != null ? breakdown.score : breakdown.total;
      if (level || score != null) {
        title = `Motivation${level ? ` · ${level}` : ""}${score != null ? ` (${score})` : ""}`;
      }
    }
    tip.classList.toggle("motivation", kind === "motivation");
    tip.innerHTML = `<span class="tip-title">${escapeText(title)}</span>${body}${note}`;
  }

  function placeTip(el) {
    const rect = el.getBoundingClientRect();
    tip.hidden = false;
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
    let top = rect.top - tipRect.height - 10;
    if (top < 8) top = rect.bottom + 10;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  function showTip(el) {
    const breakdown = parseBreakdown(el);
    if (!breakdown) return;
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    activeEl = el;
    renderBreakdown(el, breakdown);
    placeTip(el);
  }

  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      tip.hidden = true;
      activeEl = null;
    }, 120);
  }

  document.addEventListener("pointerover", (e) => {
    const el = e.target.closest(".score-tip");
    if (!el) return;
    showTip(el);
  });
  document.addEventListener("pointerout", (e) => {
    const el = e.target.closest(".score-tip");
    if (!el) return;
    const related = e.relatedTarget && e.relatedTarget.closest
      ? e.relatedTarget.closest(".score-tip")
      : null;
    if (related === el) return;
    scheduleHide();
  });
  document.addEventListener("focusin", (e) => {
    const el = e.target.closest(".score-tip");
    if (el) showTip(el);
  });
  document.addEventListener("focusout", (e) => {
    const el = e.target.closest(".score-tip");
    if (!el) return;
    scheduleHide();
  });
  window.addEventListener(
    "scroll",
    () => {
      if (activeEl && !tip.hidden) placeTip(activeEl);
    },
    true
  );
  window.addEventListener("resize", () => {
    if (activeEl && !tip.hidden) placeTip(activeEl);
  });
})();

(function voteControls() {
  function applyVoteToRow(row, vote) {
    row.dataset.vote = vote || "";
    row.classList.toggle("is-thumbs-down", vote === "down");
    row.classList.toggle("is-thumbs-up", vote === "up");
    row.querySelectorAll(".vote-btn").forEach((btn) => {
      const active = btn.getAttribute("data-vote") === vote;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function moveThumbsDownToBottom(tbody) {
    if (!tbody) return;
    const downs = Array.from(tbody.querySelectorAll("tr[data-vote='down']"));
    downs.forEach((row) => tbody.appendChild(row));
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".vote-btn");
    if (!btn) return;
    const controls = btn.closest("[data-vote-controls]");
    if (!controls) return;
    e.preventDefault();
    const vehicleId = controls.getAttribute("data-vehicle-id");
    const vote = btn.getAttribute("data-vote");
    if (!vehicleId || !vote) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/vehicles/${vehicleId}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ vote }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Vote failed");
      const row = controls.closest("tr");
      if (row) {
        applyVoteToRow(row, data.vote);
        if (data.vote === "down") {
          const tbody = row.parentElement;
          moveThumbsDownToBottom(tbody);
        }
      }
    } catch (_err) {
      btn.classList.add("vote-error");
      setTimeout(() => btn.classList.remove("vote-error"), 800);
    } finally {
      btn.disabled = false;
    }
  });
})();
