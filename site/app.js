/* stars — home page search. Data comes from index.js (window.STARS), so the page also works from file://. */
(() => {
  const D = window.STARS || [];
  const $ = s => document.querySelector(s);
  const STOP = new Set("a an and are as at be by for from how i in into is it me my of on or that the this to tool tools want with need app apps something some".split(" "));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = n => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);

  // Pre-lowercase searchable fields once.
  for (const d of D) {
    d._name = d.n.toLowerCase(); d._owner = (d.d || "").toLowerCase();
    d._cat = (d.c || "").toLowerCase();
    d._kw = d.k.map(k => k.toLowerCase());
    d._kwj = " " + d._kw.join(" | ") + " ";
    d._one = (d.o || "").toLowerCase();
    d._use = (d.u || []).join(" | ").toLowerCase();
    d._tags = (d.g || []).join(" ").toLowerCase();
  }

  const state = { q: "", src: "all", list: "", cat: "", shown: 40 };
  const terms = q => q.toLowerCase().split(/[^a-z0-9+#.\-]+/).filter(w => w.length > 1 && !STOP.has(w));
  const wordRe = {};
  const hasWord = (text, t) => (wordRe[t] ||= new RegExp("(^|[^a-z0-9])" + t.replace(/[.+#\-]/g, "\\$&"))).test(text);

  function score(d, ts, phrase) {
    if (!ts.length) return 1;
    let total = 0, matched = 0;
    for (const t of ts) {
      let s = 0;
      if (d._name === t) s += 14; else if (d._name.includes(t)) s += 8;
      if (hasWord(d._cat, t)) s += 5;
      if (d._kw.includes(t)) s += 6; else if (hasWord(d._kwj, t)) s += 4; else if (d._kwj.includes(t)) s += 1.5;
      if (hasWord(d._one, t)) s += 3;
      if (hasWord(d._use, t)) s += 2;
      if (hasWord(d._tags, t)) s += 2;
      if (d._owner === t) s += 2;
      if (s > 0) matched++;
      total += s;
    }
    if (!matched) return 0;
    const coverage = matched / ts.length;
    if (ts.length > 1 && coverage < 0.5) return 0;
    if (phrase && (d._kwj.includes(phrase) || d._one.includes(phrase) || d._use.includes(phrase))) total += 10;
    total *= coverage * coverage;
    total += Math.log10((d.s || 0) + 10) * 0.6 + (d.m ? 1.5 : 0);
    return total;
  }

  function filtered() {
    const ok = { all: () => true, mine: d => d.t === "r" && d.m, awesome: d => d.t === "r" && !d.m,
                 tool: d => d.t === "w", article: d => d.t === "a" }[state.src] || (() => true);
    return D.filter(d => ok(d)
      && (!state.list || (d.g || []).includes(state.list))
      && (!state.cat || d._cat === state.cat));
  }

  function run() {
    const ts = terms(state.q);
    const phrase = ts.length > 1 ? state.q.trim().toLowerCase() : "";
    let rows = filtered();
    if (ts.length) {
      rows = rows.map(d => [d, score(d, ts, phrase)]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]).map(x => x[0]);
    } else {
      const rank = d => (d.m ? 3 : d.t === "r" ? 2 : d.x ? 0 : 1);
      rows = rows.slice().sort((a, b) => (rank(b) - rank(a)) || ((b.s || 0) - (a.s || 0)) || a.n.localeCompare(b.n));
    }
    return { rows, ts };
  }

  function hl(text, ts) {
    let out = esc(text);
    for (const t of ts) out = out.replace(new RegExp("(^|[^a-z0-9])(" + t.replace(/[.+#\-]/g, "\\$&") + ")", "gi"), "$1<mark>$2</mark>");
    return out;
  }

  const TYPE = { w: "Tool / site", a: "Article" };
  function card(d, ts) {
    const hits = ts.length ? d.k.filter(k => ts.some(t => k.toLowerCase().includes(t))).slice(0, 4) : [];
    const via = !d.m && d.v ? `<span class="via">via ${esc(d.v)}</span>` : "";
    const href = d.t === "r" ? `r/${d.id}.html` : (d.x || `p/${d.id}.html`);
    const ext = d.x ? ` target="_blank" rel="noopener"` : "";
    const title = d.t === "r"
      ? `<span class="name"><span class="owner">${esc(d.d)}/</span>${hl(d.n, ts)}</span>`
      : `<span class="name page">${hl(d.n, ts)}</span><span class="dom">${esc(d.d)}${d.x ? " ↗" : ""}</span>`;
    const ICON = { r: d.m ? "★" : "◆", w: "⚙", a: "¶" };
    return `<li><a class="card t-${d.t}${d.m ? " mine" : ""}" href="${esc(href)}"${ext}><span class="ico" aria-hidden="true">${ICON[d.t]}</span><div>
      ${d.t !== "r" ? `<span class="type">${TYPE[d.t]}${d.x ? " · not summarized" : ""}</span>` : ""}
      ${title}${d.c ? `<span class="cat">${esc(d.c)}</span>` : ""}
      <p class="one">${hl(d.o || "", ts)}</p>
      <div class="meta">${(d.g || []).map(g => `<span class="tag-list">${esc(g)}</span>`).join("")}${d.l ? `<span>${esc(d.l)}</span>` : ""}${via}
      ${hits.length ? `<span class="hits">${hits.map(h => `<span>${esc(h)}</span>`).join("")}</span>` : ""}</div>
      </div>${d.s ? `<span class="stars" aria-label="${d.s} GitHub stars">★ ${fmt(d.s)}</span>` : ""}</a></li>`;
  }

  function render() {
    const { rows, ts } = run();
    const total = rows.length;
    $("#count").textContent = state.q || state.list || state.cat || state.src !== "all"
      ? `${total.toLocaleString()} match${total === 1 ? "" : "es"}` : `${D.length.toLocaleString()} repos, tools and articles`;
    $("#clear").hidden = !(state.q || state.list || state.cat || state.src !== "all");
    $("#results").innerHTML = rows.slice(0, state.shown).map(d => card(d, ts)).join("") ||
      `<li class="empty">Nothing matched. Try fewer or plainer words, like “file sharing” or “terminal”.</li>`;
    $("#more").hidden = total <= state.shown;
    const browsing = !state.q && !state.list && !state.cat;
    $("#browse").hidden = !browsing;
    if (browsing) {
      const cc = {};
      const GENERIC = /^(links?|misc(ellaneous)?|other|others|resources?|writing|general|tools?|apps?|external links|related|contents?|table of contents|about|license|contributing|reading|articles?|books?|videos?|talks?)$/;
      for (const d of filtered()) if (d._cat && !d.x && !GENERIC.test(d._cat)) cc[d._cat] = (cc[d._cat] || 0) + 1;
      $("#catgrid").innerHTML = Object.entries(cc).sort((x, y) => y[1] - x[1]).slice(0, 24)
        .map(([c, n]) => `<button type="button" class="cat-tile" data-cat="${esc(c)}"><span>${esc(c)}</span><small>${n}</small></button>`).join("");
    }
    $("#more").textContent = `Show more (${(total - state.shown).toLocaleString()} left)`;
    // Category refine chips from the current result set
    const cats = {};
    for (const d of rows.slice(0, ts.length ? 60 : 400)) if (d._cat) cats[d._cat] = (cats[d._cat] || 0) + 1;
    const top = Object.entries(cats).sort((a, b) => b[1] - a[1]).slice(0, 14);
    $("#refine").innerHTML = (state.q || state.cat || state.list) && (top.length > 1 || state.cat) ? `<span class="label">Refine</span>` +
      (state.cat ? `<button class="chip" aria-pressed="true" data-cat="">${esc(state.cat)} ✕</button>` : "") +
      top.filter(([c]) => c !== state.cat).map(([c, n]) => `<button class="chip" data-cat="${esc(c)}">${esc(c)}<small>${n}</small></button>`).join("") : "";
    for (const b of document.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", String(b.dataset.src === state.src));
    $("#list").value = state.list;
    writeHash();
  }

  function writeHash() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.src !== "all") p.set("src", state.src);
    if (state.list) p.set("list", state.list);
    if (state.cat) p.set("cat", state.cat);
    const h = p.toString();
    history.replaceState(null, "", h ? "#" + h : location.pathname);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    state.q = p.get("q") || ""; state.src = p.get("src") || "all"; state.list = p.get("list") || ""; state.cat = p.get("cat") || "";
    $("#q").value = state.q;
  }

  // Wire up
  const listCounts = {};
  for (const d of D) for (const g of d.g || []) listCounts[g] = (listCounts[g] || 0) + 1;
  $("#list").innerHTML = `<option value="">All my lists</option>` +
    Object.entries(listCounts).sort((a, b) => b[1] - a[1]).map(([g, n]) => `<option value="${esc(g)}">${esc(g)} (${n})</option>`).join("");
  const cnt = f => D.filter(f).length.toLocaleString();
  $("#n-all").textContent = D.length.toLocaleString(); $("#n-mine").textContent = cnt(d => d.t === "r" && d.m);
  $("#n-aw").textContent = cnt(d => d.t === "r" && !d.m); $("#n-tool").textContent = cnt(d => d.t === "w"); $("#n-art").textContent = cnt(d => d.t === "a");

  let timer;
  $("#q").addEventListener("input", e => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; state.shown = 40; state.cat = ""; render(); }, 90); });
  document.querySelectorAll(".seg button").forEach(b => b.onclick = () => { state.src = b.dataset.src; state.shown = 40; render(); });
  $("#list").onchange = e => { state.list = e.target.value; if (state.list) state.src = "mine"; state.shown = 40; render(); };
  $("#refine").onclick = e => { const b = e.target.closest("[data-cat]"); if (!b) return; state.cat = b.dataset.cat; state.shown = 40; render(); };
  $("#more").onclick = () => { state.shown += 60; render(); };
  $("#catgrid").onclick = e => { const b = e.target.closest("[data-cat]"); if (!b) return; state.cat = b.dataset.cat; state.shown = 40; render(); $("#refine").scrollIntoView({ behavior: "smooth", block: "start" }); };
  $("#clear").onclick = () => { Object.assign(state, { q: "", src: "all", list: "", cat: "", shown: 40 }); $("#q").value = ""; render(); $("#q").focus(); };
  document.querySelectorAll(".try").forEach(b => b.onclick = () => { $("#q").value = state.q = b.textContent; state.cat = ""; state.shown = 40; render(); });
  document.addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); $("#q").select(); }
    if (e.key === "Escape" && document.activeElement === $("#q")) { $("#q").value = ""; state.q = ""; render(); }
  });
  window.addEventListener("hashchange", () => { readHash(); render(); });
  readHash(); render();
})();
