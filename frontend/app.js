(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Anonymous per-browser user id so each visitor gets their own history.
  const userId = (() => {
    let id = null;
    try { id = localStorage.getItem("reelmatch-user"); } catch (_) {}
    if (!id) {
      id = "u" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
      try { localStorage.setItem("reelmatch-user", id); } catch (_) {}
    }
    return id;
  })();

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-User-Id": userId, ...(opts.headers || {}) },
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return res.json();
  }

  // movie_id -> liked (true / false / null)
  const watched = new Map();
  let lastQuery = "";

  const EXAMPLES = [
    "mind-bending sci-fi with a twist",
    "funny movies for the whole family",
    "Tom Hanks",
    "movies like Inception",
    "scary 80s horror",
    "heist movies",
    "feel-good true story",
    "Christopher Nolan",
    "romantic comedy in New York",
    "sad movie that will make me cry",
    "Korean thriller",
    "space survival",
  ];

  // ------------------------------------------------------------ rendering
  const runtime = (m) => (m.runtime ? `${Math.floor(m.runtime / 60)}h ${m.runtime % 60}m` : "");
  const metaLine = (m) => [m.year || "", runtime(m)].filter(Boolean).join(" · ");

  function hashHue(s) {
    let h = 0;
    for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return h % 360;
  }

  function posterHTML(m) {
    const hue = hashHue(m.title);
    const fallback = `<div class="poster-fallback" style="background:linear-gradient(160deg,hsl(${hue} 45% 32%),hsl(${(hue + 40) % 360} 55% 12%))">
        <div class="pf-title">${esc(m.title)}</div><div class="pf-year">${m.year || ""}</div></div>`;
    const img = m.poster
      ? `<img src="${esc(m.poster)}" alt="${esc(m.title)} poster" loading="lazy" onerror="this.remove()">`
      : "";
    return fallback + img;
  }

  function providersHTML(m, max = 3) {
    const w = m.where_to_watch;
    if (!w) return "";
    const names = [...(w.stream || []), ...(w.free || [])].slice(0, max);
    if (!names.length) return `<div class="providers"><span class="prov" style="background:#444;color:#ddd">Rent / Buy</span></div>`;
    return `<div class="providers">${names.map((n) => `<span class="prov">${esc(n)}</span>`).join("")}</div>`;
  }

  function cardHTML(m, { reasons = true, historyControls = false } = {}) {
    const isWatched = watched.has(m.id);
    const liked = watched.get(m.id);
    const reasonList = reasons && m.reasons && m.reasons.length
      ? `<div class="reasons">${m.reasons.slice(0, 2).map((r) => `<div>${esc(r)}</div>`).join("")}</div>` : "";
    const actions = historyControls
      ? `<div class="card-actions">
           <button class="btn small good ${liked === true ? "on" : ""}" data-act="like" data-id="${m.id}" title="Liked it">👍</button>
           <button class="btn small bad ${liked === false ? "on" : ""}" data-act="dislike" data-id="${m.id}" title="Didn't like it">👎</button>
           <button class="btn small" data-act="remove" data-id="${m.id}" title="Remove from history">✕</button>
         </div>`
      : `<div class="card-actions">
           <button class="btn ${isWatched ? "" : ""}" data-act="toggle" data-id="${m.id}">${isWatched ? "✓ Watched" : "+ Watched"}</button>
         </div>`;
    return `<article class="card" data-id="${m.id}">
      <div class="poster" data-act="open" data-id="${m.id}">
        ${posterHTML(m)}
        <button class="ribbon ${isWatched ? "on" : ""}" data-act="toggle" data-id="${m.id}"
          title="${isWatched ? "Remove from watched" : "Mark as watched"}">${isWatched ? "✓" : "+"}</button>
      </div>
      <div class="card-body">
        <div class="rating"><span class="star">★</span> ${m.rating.toFixed(1)}
          <span class="card-meta">${metaLine(m) ? "· " + metaLine(m) : ""}</span></div>
        <div class="card-title" data-act="open" data-id="${m.id}">${esc(m.title)}</div>
        <div class="card-meta">${esc(m.genres.slice(0, 3).join(" · "))}</div>
        ${reasonList}
        ${providersHTML(m)}
        ${actions}
      </div>
    </article>`;
  }

  function renderGrid(el, movies, opts) {
    el.innerHTML = movies.length ? movies.map((m) => cardHTML(m, opts)).join("")
      : `<div class="empty">No movies found.</div>`;
  }

  function skeletons(el, n = 12) {
    el.innerHTML = Array.from({ length: n }, () => `<div class="skeleton"></div>`).join("");
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
  }

  // ------------------------------------------------------------ data flows
  async function runSearch(q) {
    q = q.trim();
    if (!q) return;
    lastQuery = q;
    $("#search-input").value = q;
    const section = $("#results-section");
    section.classList.remove("hidden");
    $("#results-title").textContent = `Recommendations for “${q}”`;
    $("#interpreted").innerHTML = "";
    skeletons($("#results"));
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const hide = $("#hide-watched").checked ? "&hide_watched=1" : "";
      const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=12${hide}`);
      const i = data.interpreted;
      const tags = [
        ...i.people.map((p) => `<span class="tag">Person: <b>${esc(p)}</b></span>`),
        ...i.genres.map((g) => `<span class="tag">Genre: <b>${esc(g)}</b></span>`),
        ...i.similar_to.map((t) => `<span class="tag">Like: <b>${esc(t)}</b></span>`),
        ...(i.years ? [`<span class="tag">Years: <b>${i.years[0]}–${i.years[1]}</b></span>`] : []),
        ...(i.keywords || []).map((k) => `<span class="tag">Theme: <b>${esc(k)}</b></span>`),
      ];
      $("#interpreted").innerHTML = tags.join("");
      renderGrid($("#results"), data.results);
    } catch (e) {
      $("#results").innerHTML = `<div class="empty">Something went wrong: ${esc(e.message)}</div>`;
    }
  }

  async function loadHistory() {
    const items = await api("/api/history");
    watched.clear();
    items.forEach((m) => watched.set(m.id, m.liked));
    $("#history-count").textContent = items.length;
    const el = $("#history");
    el.innerHTML = items.length
      ? items.map((m) => cardHTML(m, { reasons: false, historyControls: true })).join("")
      : `<div class="empty">You haven't marked any movies as watched yet. Use “+ Watched” on any movie, or add one above.</div>`;
  }

  async function loadForYou() {
    const el = $("#for-you");
    const data = await api("/api/recommendations?limit=12");
    if (!data.results.length) {
      $("#for-you-note").textContent = "";
      el.innerHTML = `<div class="empty">Mark a few movies as watched (and 👍 / 👎 them) to get personalised picks here.</div>`;
      return;
    }
    $("#for-you-note").textContent = `Based on ${data.based_on} movie${data.based_on === 1 ? "" : "s"} you've watched`;
    renderGrid(el, data.results);
  }

  async function loadTop() {
    const movies = await api("/api/trending?limit=20");
    $("#top").innerHTML = movies.map((m) => cardHTML(m, { reasons: false })).join("");
  }

  async function refreshPersonal() {
    await loadHistory();
    await loadForYou();
    // Refresh ribbons / buttons in other sections without refetching.
    document.querySelectorAll("#results .card, #top .card, #modal .card").forEach(syncCard);
    const detailBtn = document.querySelector("#modal [data-act='toggle-detail']");
    if (detailBtn) syncDetailButton(detailBtn);
  }

  function syncCard(card) {
    const id = card.dataset.id;
    const on = watched.has(id);
    const ribbon = card.querySelector(".ribbon");
    if (ribbon) { ribbon.classList.toggle("on", on); ribbon.textContent = on ? "✓" : "+"; }
    const btn = card.querySelector(".card-actions [data-act='toggle']");
    if (btn) btn.textContent = on ? "✓ Watched" : "+ Watched";
  }

  function syncDetailButton(btn) {
    const on = watched.has(btn.dataset.id);
    btn.textContent = on ? "✓ Watched" : "+ Mark as watched";
    btn.classList.toggle("primary", !on);
  }

  async function setWatched(id, liked = null) {
    await api("/api/history", { method: "POST", body: JSON.stringify({ movie_id: id, liked }) });
  }

  async function toggleWatched(id) {
    if (watched.has(id)) {
      await api(`/api/history/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Removed from your watch history");
    } else {
      await setWatched(id);
      toast("Added to your watch history — recommendations updated");
    }
    await refreshPersonal();
  }

  // ------------------------------------------------------------ detail modal
  async function openMovie(id) {
    const modal = $("#modal");
    const body = $("#modal-body");
    body.innerHTML = `<div class="skeleton" style="aspect-ratio:3/1"></div>`;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    try {
      const m = await api(`/api/movies/${encodeURIComponent(id)}`);
      const w = m.where_to_watch;
      const row = (label, list) => list && list.length
        ? `<div class="wtw-row"><span class="label">${label}</span>${list.map((n) => `<span class="prov">${esc(n)}</span>`).join("")}</div>` : "";
      const note = w.source === "live"
        ? `Live availability for your region via JustWatch/TMDB.`
        : `Availability changes often — check the link for the latest.`;
      body.innerHTML = `
        <div class="detail">
          <div class="poster">${posterHTML(m)}</div>
          <div>
            <h2>${esc(m.title)}</h2>
            <div class="sub">${metaLine(m)}</div>
            <div class="big-rating"><span class="star">★</span> <b>${m.rating.toFixed(1)}</b><span class="muted">/10</span></div>
            <div class="genre-pills">${m.genres.map((g) => `<span>${esc(g)}</span>`).join("")}</div>
            <p class="synopsis">${esc(m.synopsis)}</p>
            <dl>
              <dt>Director</dt><dd>${m.director.split(",").map((d) => `<a href="#" data-search="${esc(d.trim())}">${esc(d.trim())}</a>`).join(", ")}</dd>
              <dt>Stars</dt><dd>${m.cast.map((c) => `<a href="#" data-search="${esc(c)}">${esc(c)}</a>`).join(" · ")}</dd>
              <dt>Themes</dt><dd>${m.keywords.slice(0, 6).map((k) => `<a href="#" data-search="${esc(k)}">${esc(k)}</a>`).join(" · ")}</dd>
            </dl>
            <div class="wtw">
              <h3>Where to watch</h3>
              ${row("Stream", w.stream)}${row("Free", w.free)}${row("Rent", w.rent)}${row("Buy", w.buy)}
              <div class="note">${note} <a href="${esc(w.link)}" target="_blank" rel="noopener">See all options ↗</a></div>
            </div>
            <div class="detail-actions">
              <button class="btn" data-act="toggle-detail" data-id="${m.id}"></button>
              <button class="btn" data-search="movies like ${esc(m.title)}">More like this</button>
            </div>
          </div>
        </div>
        <div class="similar">
          <h3>More like this</h3>
          <div class="shelf">${m.similar.map((s) => cardHTML(s, { reasons: false })).join("")}</div>
        </div>`;
      syncDetailButton(body.querySelector("[data-act='toggle-detail']"));
    } catch (e) {
      body.innerHTML = `<div class="empty">Could not load movie: ${esc(e.message)}</div>`;
    }
  }

  function closeModal() {
    $("#modal").classList.add("hidden");
    document.body.style.overflow = "";
  }

  // ------------------------------------------------------------ events
  document.addEventListener("click", async (ev) => {
    const searchLink = ev.target.closest("[data-search]");
    if (searchLink) {
      ev.preventDefault();
      closeModal();
      runSearch(searchLink.dataset.search);
      return;
    }
    if (ev.target.closest("[data-close]")) { closeModal(); return; }
    const el = ev.target.closest("[data-act]");
    if (!el) return;
    ev.stopPropagation();
    const { act, id } = el.dataset;
    try {
      if (act === "open") openMovie(id);
      else if (act === "toggle" || act === "toggle-detail") await toggleWatched(id);
      else if (act === "remove") { await api(`/api/history/${encodeURIComponent(id)}`, { method: "DELETE" }); await refreshPersonal(); }
      else if (act === "like" || act === "dislike") {
        const want = act === "like";
        const next = watched.get(id) === want ? null : want;
        await setWatched(id, next);
        await refreshPersonal();
      }
    } catch (e) {
      toast(e.message);
    }
  });

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch($("#search-input").value);
  });
  $("#hide-watched").addEventListener("change", () => lastQuery && runSearch(lastQuery));

  $("#example-chips").innerHTML = EXAMPLES
    .map((q) => `<button class="chip" data-search="${esc(q)}">${esc(q)}</button>`).join("");

  // Autocomplete for adding watched movies.
  const addInput = $("#add-input");
  const sugg = $("#add-suggestions");
  let suggTimer;
  addInput.addEventListener("input", () => {
    clearTimeout(suggTimer);
    suggTimer = setTimeout(async () => {
      const q = addInput.value.trim();
      if (!q) { sugg.classList.add("hidden"); return; }
      const hits = await api(`/api/movies?q=${encodeURIComponent(q)}`);
      sugg.innerHTML = hits.length
        ? hits.map((h) => `<li data-add="${esc(h.id)}">${esc(h.title)} ${h.year ? `<span>(${h.year})</span>` : ""}</li>`).join("")
        : `<li class="muted">No match in the catalog</li>`;
      sugg.classList.remove("hidden");
    }, 150);
  });
  sugg.addEventListener("click", async (e) => {
    const li = e.target.closest("[data-add]");
    if (!li) return;
    sugg.classList.add("hidden");
    addInput.value = "";
    await setWatched(li.dataset.add);
    toast("Added to your watch history — recommendations updated");
    await refreshPersonal();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".add-watched")) sugg.classList.add("hidden");
  });

  // ------------------------------------------------------------ boot
  (async () => {
    try {
      const health = await api("/api/health");
      $("#data-note").textContent = health.live_data
        ? "Searching all movies on TMDB. Streaming availability by JustWatch."
        : `Searching the bundled catalog of ${health.movies} movies. Set TMDB_API_KEY on the server to search every movie.`;
      if (health.live_data) $("#top-title").textContent = "Trending this week";
      await refreshPersonal();
      await loadTop();
      const q = new URLSearchParams(location.search).get("q");
      if (q) runSearch(q);
    } catch (e) {
      toast("Could not reach the server: " + e.message);
    }
  })();
})();
