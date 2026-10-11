"use strict";
const ADMIN = location.pathname.startsWith("/admin");
const API = ADMIN ? "/admin/api" : "/api";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const S = { facets: null, features: null, q: "", origin: "", process: "", method: "", sort: "recent", rsort: "name", editor: ADMIN };

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
  const r = await fetch((path.startsWith("/") ? "" : API + "/") + path, o);
  let data = null;
  try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    if (r.status === 401 && ADMIN) toast("Your sign-in expired. Reload the page to sign in again.");
    const e = new Error((data && data.error) || `Request failed (${r.status})`); e.status = r.status; e.data = data; throw e;
  }
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3400);
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const todayISO = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };
const fmtDay = (d) => { if (!d) return ""; const t = new Date(d.slice(0, 10) + "T12:00:00"); return isNaN(t) ? d : t.toLocaleDateString(undefined, { month: "short", day: "numeric", year: t.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }); };
const n1 = (x) => (x == null ? "" : (+x % 1 ? (+x).toFixed(1) : String(Math.round(+x))));
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
const grams = (g) => { if (!g) return ""; const oz = g / 28.3495; return Math.abs(oz - Math.round(oz)) < 0.15 ? `${Math.round(oz)} oz` : `${Math.round(g)} g`; };
const money = (p, c) => p == null ? "" : new Intl.NumberFormat(undefined, { style: "currency", currency: c || "USD", maximumFractionDigits: p % 1 ? 2 : 0 }).format(p);
const place = (b) => [b.shop, b.city, b.country && b.country !== "United States" ? b.country : null].filter(Boolean).join(", ");

function rate(r, cls = "") {
  if (r == null) return `<span class="rate none ${cls}" title="Not rated">–</span>`;
  const p = Math.round(8 + r * 6);  // tint stays light enough for ink text (≥4.5:1) in both themes
  return `<span class="rate ${r >= 9 ? "gold" : ""} ${cls}" style="--p:${p}%" title="${n1(r)} out of 10">${n1(r)}</span>`;
}
function ageText(days, prefix = "") {
  if (days == null) return "";
  const t = days === 0 ? "roasted today" : days === 1 ? "1 day off roast" : `${days} days off roast`;
  return `<span class="age ${days >= 4 && days <= 21 ? "fresh" : ""}">${prefix}${t}</span>`;
}
const img = (file, thumb = true, cls = "") => file ? `<img class="${cls}" loading="lazy" src="/photos/${thumb ? "t-" : ""}${esc(file)}" alt="">` : "";
// line icons (24px grid, 1.75px stroke) instead of emoji, per Café Green
const ICONS = {
  cup: '<path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 11h1.5a2.5 2.5 0 0 1 0 5H17"/><path d="M8 3.5c0 1.5 1 1.5 1 3M12 3.5c0 1.5 1 1.5 1 3"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.5"/>',
  sparkle: '<path d="M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8z"/><path d="M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z"/>',
  pin: '<path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  off: '<circle cx="12" cy="12" r="7.5"/>',
  x: '<path d="M7 7l10 10M17 7L7 17"/>',
  star: '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z"/>',
};
const icon = (n, cls = "") => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n]}</svg>`;
const BEAN = icon("cup", "lg");

async function facets(force) { if (!S.facets || force) S.facets = await api("facets"); return S.facets; }
async function features() { if (!ADMIN) return {}; if (!S.features) S.features = await api("features"); return S.features; }

/* ---------- nav / router ---------- */
function renderNav(path) {
  const items = ADMIN
    ? [["#/", "Feed"], ["#/coffees", "Coffees"], ["#/roasters", "Roasters"], ["#/map", "Map"], ["#/stats", "Stats"], ["#/more", "More"], ["#/add", "＋ Bag", "add"]]
    : [["#/", "Feed"], ["#/coffees", "Coffees"], ["#/roasters", "Roasters"], ["#/stats", "Stats"], ["/admin#/", S.editor ? "Edit" : "Sign in"]];
  const sect = path.split("/")[0];
  const on = (h) => h === "#/" ? sect === "" : h.startsWith("#/") && (sect === h.slice(2) || sect + "s" === h.slice(2));
  $("#nav").innerHTML = items.map(([h, t, c]) => `<a href="${h}" class="${c || ""} ${on(h) ? "on" : ""}">${t}</a>`).join("");
  $("#fab").classList.toggle("hidden", !ADMIN || sect === "add" || /review|edit/.test(path));
}
async function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, qs] = hash.split("?");
  const p = new URLSearchParams(qs || "");
  renderNav(path);
  window.scrollTo(0, 0);
  let m;
  try {
    if ((m = path.match(/^bag\/(\d+)\/review$/)) && ADMIN) return await reviewView(+m[1], p.get("method"));
    if ((m = path.match(/^bag\/(\d+)\/edit$/)) && ADMIN) return await bagEditView(+m[1]);
    if ((m = path.match(/^bag\/(\d+)$/))) return await bagView(+m[1]);
    if ((m = path.match(/^coffee\/(\d+)\/edit$/)) && ADMIN) return await coffeeEditView(+m[1]);
    if ((m = path.match(/^coffee\/(\d+)$/))) return await coffeeView(+m[1]);
    if ((m = path.match(/^roaster\/(\d+)\/edit$/)) && ADMIN) return await roasterEditView(+m[1]);
    if ((m = path.match(/^roaster\/(\d+)$/))) return await roasterView(+m[1]);
    if (path === "coffees") { for (const k of ["q", "origin", "process", "method"]) if (p.has(k)) S[k] = p.get(k); return await coffeesView(); }
    if (path === "roasters") return await roastersView();
    if (path === "stats") return await statsView();
    if (path === "add" && ADMIN) return await addView(p);
    if (path === "map" && ADMIN) return await mapView();
    if (path === "more" && ADMIN) return await moreView();
    return await feedView();
  } catch (e) {
    view.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", route);

/* ---------- feed ---------- */
function checkIn(v) {
  const flav = (v.flavor_list || []).slice(0, 5).map((f) => `<span class="chip">${esc(f)}</span>`).join(" ");
  const recipe = [v.grind ? `grind ${esc(v.grind)}` : "", v.dose_g && v.water_g ? `${n1(v.dose_g)}:${n1(v.water_g)} g` : ""].filter(Boolean).join(" · ");
  return `<a class="check-in" href="#/bag/${v.bag_id}">
    ${v.photo ? img(v.photo, true, "thumb") : `<span class="thumb ph">${BEAN}</span>`}
    <div><div class="t">${esc(v.coffee_name)}</div>
      <div class="r">${esc(v.roaster_name)}${v.origin ? " · " + esc(v.origin) : ""}${v.process ? " · " + esc(v.process) : ""}</div>
      <div class="m"><span class="method">${esc(v.method)}</span>${recipe ? ` <span class="muted">· ${recipe}</span>` : ""}
        <span class="muted"> · ${fmtDay(v.brewed_on)}${v.days_off_roast != null ? ` · ${v.days_off_roast}d off roast` : ""}</span></div>
      ${v.notes ? `<div class="n">${esc(v.notes)}</div>` : ""}
      ${flav ? `<div class="chips" style="margin-top:6px">${flav}</div>` : ""}
    </div>${rate(v.rating)}</a>`;
}
function shelfCard(b) {
  const done = b.methods_done || [];
  return `<a class="shelf-card" href="#/bag/${b.id}">
    ${b.photos && b.photos[0] ? img(b.photos[0].file) : `<span class="ph">${BEAN}</span>`}
    <div class="b"><div class="t">${esc(b.coffee_name)}</div><div class="small muted">${esc(b.roaster_name)}</div>
      ${b.roast_age_today != null && b.roast_age_today <= 60 ? ageText(b.roast_age_today) : b.roast_date ? `<span class="age">roasted ${fmtDay(b.roast_date)}</span>` : `<span class="age">${b.status === "sealed" ? "sealed" : "open"}</span>`}
      <div class="small muted">${done.length ? "Reviewed: " + done.map(esc).join(", ") : b.status === "sealed" ? "Not opened yet" : "No review yet"}</div></div></a>`;
}
async function feedView() {
  const f = await facets();
  view.innerHTML = `${ADMIN ? `<div id="shelf"></div>` : ""}
    <div class="section-h"><h2>Recent brews</h2><span class="muted small">${f.totals.reviews} reviews · ${f.totals.bags} bags · ${f.totals.roasters} roasters</span></div>
    <div class="feed" id="feed"></div><div class="actions" style="justify-content:center;margin:16px 0"><button id="more" class="hidden">Show more</button></div>
    <footer class="foot">Coffee beans from roasters near and far.</footer>`;
  if (ADMIN) {
    api("shelf").then((s) => {
      if (!s.bags.length) { $("#shelf").innerHTML = `<div class="note">No open bags. <a href="#/add">Add the bag you just bought</a>.</div>`; return; }
      $("#shelf").innerHTML = `<div class="section-h"><h2>On the shelf</h2><span class="muted small">${s.bags.length} not finished</span></div>
        <div class="shelf">${s.bags.map(shelfCard).join("")}</div>`;
    }).catch(() => {});
  }
  let offset = 0;
  const load = async () => {
    const d = await api(`feed?limit=25&offset=${offset}`);
    offset += d.reviews.length;
    $("#feed").insertAdjacentHTML("beforeend", d.reviews.map(checkIn).join(""));
    if (!offset) $("#feed").innerHTML = `<div class="empty">No brews logged yet.${ADMIN ? ` Add a bag, then review it for each way you brew it.` : ""}</div>`;
    $("#more").classList.toggle("hidden", !d.more);
  };
  $("#more").onclick = load;
  await load();
}

/* ---------- coffees ---------- */
function coffeeCard(c) {
  const best = (c.best_methods || [])[0];
  return `<a class="card" href="#/coffee/${c.id}">
    <div class="img">${c.photo ? img(c.photo) : BEAN}</div>${c.avg_rating != null ? rate(c.avg_rating, "sm") : ""}
    <div class="b"><div class="t">${esc(c.name)}</div><div class="r">${esc(c.roaster_name)}</div>
      <div class="k">${[c.origin, c.process].filter(Boolean).map(esc).join(" · ")}</div>
      <div class="k">${c.bag_count} bag${c.bag_count === 1 ? "" : "s"}${best ? ` · best as ${esc(best.method)}` : ""}</div></div></a>`;
}
async function coffeesView() {
  const f = await facets();
  const opt = (arr, cur, label, val = (x) => x.name) => `<option value="">${label}</option>` +
    arr.map((x) => `<option value="${esc(val(x))}" ${val(x) === cur ? "selected" : ""}>${esc(cap(x.name))} (${x.n})</option>`).join("");
  view.innerHTML = `
    <div class="controls">
      <input class="search" id="q" type="search" placeholder="Search coffee, roaster, origin, notes, city…" value="${esc(S.q)}" autocomplete="off" aria-label="Search">
      <select id="origin" aria-label="Origin">${opt(f.origins, S.origin, "Origin")}</select>
      <select id="process" aria-label="Process">${opt(f.processes, S.process, "Process")}</select>
      <select id="method" aria-label="Brew method">${opt(f.used_methods, S.method, "Method")}</select>
      <select id="sort" aria-label="Sort"><option value="recent">Recently bought</option><option value="rating">Highest rated</option>
        <option value="name">Name</option><option value="roaster">Roaster</option></select>
    </div>
    <div class="summary muted small" id="summary"></div>
    <div class="grid" id="grid" style="margin-top:10px"></div><footer class="foot"></footer>`;
  $("#sort").value = S.sort;
  const load = async () => {
    const p = new URLSearchParams();
    for (const k of ["q", "origin", "process", "method", "sort"]) if (S[k]) p.set(k, S[k]);
    const d = await api("coffees?" + p);
    $("#summary").textContent = d.coffees.length === d.total ? `${d.total} coffees` : `${d.coffees.length} of ${d.total} coffees`;
    $("#grid").innerHTML = d.coffees.length ? d.coffees.map(coffeeCard).join("") : `<div class="empty">Nothing matches.</div>`;
  };
  $("#q").addEventListener("input", debounce((e) => { S.q = e.target.value; load(); }, 220));
  for (const k of ["origin", "process", "method", "sort"]) $("#" + k).onchange = (e) => { S[k] = e.target.value; load(); };
  await load();
}

/* ---------- coffee ---------- */
function barsHTML(rows, { max = 10, label = (r) => esc(cap(r.name)), value = (r) => r.avg, fmt = (r) => `${n1(r.avg)} <small>·${r.n}</small>`, title = (r) => `${r.name}: ${n1(r.avg)}/10 over ${r.n}` } = {}) {
  if (!rows || !rows.length) return `<div class="muted small">Nothing yet.</div>`;
  return `<ul class="bars">${rows.map((r) => `<li title="${esc(title(r))}"><span class="lab">${label(r)}</span>
    <span class="track"><span class="fill" style="width:${Math.max(0, Math.min(100, (value(r) || 0) / max * 100))}%"></span></span>
    <span class="val">${value(r) == null ? "–" : fmt(r)}</span></li>`).join("")}</ul>`;
}
async function coffeeView(id) {
  const c = await api("coffees/" + id);
  const photos = c.bags.flatMap((b) => b.photos || []);
  const facts = [["Origin", c.origin], ["Region", c.region], ["Producer", c.producer], ["Varietal", c.varietal],
    ["Process", cap(c.process)], ["Altitude", c.altitude], ["Roast", cap(c.roast_level)], ["Blend", c.is_blend ? "Yes" : ""]].filter((x) => x[1]);
  view.innerHTML = `
    <div class="hero">
      <div><div class="gallery">${photos.length ? photos.map((p) => img(p.file, false)).join("") : `<div class="ph">${BEAN}</div>`}</div></div>
      <div>
        <a class="crumb" href="#/roaster/${c.roaster.id}">${esc(c.roaster.name)}${c.roaster.city ? " · " + esc(c.roaster.city) : ""}</a>
        <div class="spread"><h1>${esc(c.name)}</h1>${rate(c.avg_rating)}</div>
        ${c.roaster_notes ? `<p class="lede"><span class="muted">On the bag:</span> ${esc(c.roaster_notes)}</p>` : ""}
        <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        ${c.description ? `<p>${esc(c.description)}</p>` : ""}
        ${ADMIN ? `<div class="actions"><a class="btn primary" href="#/add?coffee=${c.id}">＋ Bought it again</a><a class="btn" href="#/coffee/${c.id}/edit">Edit</a></div>` : ""}
      </div>
    </div>
    <div class="section-h"><h2>By brew method</h2><span class="muted small">average rating · reviews</span></div>
    <div class="card-box">${barsHTML(c.by_method.map((r) => ({ ...r, name: r.method })), { label: (r) => esc(r.name) })}</div>
    <div class="section-h"><h2>Bags</h2><span class="muted small">${c.bags.length}</span></div>
    ${c.bags.map((b) => `<div class="bag"><a class="bh" href="#/bag/${b.id}">
        ${b.photos && b.photos[0] ? img(b.photos[0].file) : `<span class="ph">${BEAN}</span>`}
        <div><div><strong>${fmtDay(b.purchased_on) || "Undated"}</strong>${place(b) ? " · " + esc(place(b)) : ""}</div>
        <div class="small muted">${[b.trip, b.roast_date ? "roasted " + fmtDay(b.roast_date) : "", grams(b.weight_g), ADMIN ? money(b.price, b.currency) : ""].filter(Boolean).map(esc).join(" · ")}</div></div></a>
        ${b.reviews.length ? `<div class="mini-reviews">${b.reviews.map((r) => `<span class="chip">${esc(r.method)} ${rate(r.rating, "sm")}</span>`).join("")}</div>` : ""}</div>`).join("")}
    <footer class="foot"></footer>`;
}

/* ---------- bag ---------- */
function reviewCard(r, bagId) {
  const recipe = [r.brewer ? esc(r.brewer) : "", r.grinder ? esc(r.grinder) + (r.grind ? " @ " + esc(r.grind) : "") : r.grind ? "grind " + esc(r.grind) : "",
    r.dose_g ? `${n1(r.dose_g)} g in` : "", r.water_g ? `${n1(r.water_g)} g out` : "",
    r.dose_g && r.water_g ? `1:${(r.water_g / r.dose_g).toFixed(1)}` : "", r.temp ? esc(r.temp) : "", r.brew_time ? esc(r.brew_time) : ""].filter(Boolean).join(" · ");
  return `<div class="review">${rate(r.rating)}
    <div><div class="h">${esc(r.method)}</div>
      <div class="d">${fmtDay(r.brewed_on)}${r.days_off_roast != null ? ` · ${r.days_off_roast} days off roast` : ""}${r.updated_at && r.created_at !== r.updated_at ? " · edited" : ""}</div>
      ${recipe ? `<div class="d">${recipe}</div>` : ""}
      ${r.recipe ? `<div class="d">${esc(r.recipe)}</div>` : ""}
      ${r.flavor_list.length ? `<div class="chips" style="margin-top:6px">${r.flavor_list.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</div>` : ""}
      ${r.notes ? `<div class="n">${esc(r.notes)}</div>` : ""}</div>
    ${ADMIN ? `<a class="btn" href="#/bag/${bagId}/review?method=${encodeURIComponent(r.method)}">Edit</a>` : ""}</div>`;
}
async function bagView(id) {
  const [b, f] = await Promise.all([api("bags/" + id), facets()]);
  const c = b.coffee;
  const reviewed = new Set(b.reviews.map((r) => r.method.toLowerCase()));
  const untried = f.methods.filter((m) => !reviewed.has(m.toLowerCase()));
  const facts = [
    ["Bought", [fmtDay(b.purchased_on), b.online ? "online" : place(b)].filter(Boolean).join(" · ")],
    ["Trip", b.trip], ["Roasted", b.roast_date ? `${fmtDay(b.roast_date)}${b.roast_age_at_purchase != null ? ` (${b.roast_age_at_purchase} days before purchase)` : ""}` : ""],
    ["Size", grams(b.weight_g)], ...(ADMIN ? [["Paid", money(b.price, b.currency)]] : []),
    ["Opened", fmtDay(b.opened_on)], ["Finished", fmtDay(b.finished_on)],
  ].filter((x) => x[1]);
  view.innerHTML = `
    <div class="hero">
      <div><div class="gallery" id="gal">${b.photos.length ? b.photos.map((p) => img(p.file, false)).join("") : `<div class="ph">${BEAN}</div>`}</div>
        ${ADMIN ? `<div class="thumbs">${b.photos.map((p, i) => `<span class="ti">${img(p.file)}<button data-del="${p.id}" title="Remove photo" aria-label="Remove photo">${icon("x")}</button>
            ${i ? `<button data-cover="${p.id}" title="Make this the main photo" aria-label="Make this the main photo" style="left:-8px;right:auto">${icon("star")}</button>` : ""}</span>`).join("")}</div>
          <div class="picker" style="margin-top:8px"><label class="btn">${icon("camera")} Add photos<input type="file" accept="image/*" multiple id="addph"></label></div>` : ""}
      </div>
      <div>
        <a class="crumb" href="#/roaster/${b.roaster.id}">${esc(b.roaster.name)}${b.roaster.city ? " · " + esc(b.roaster.city) : ""}</a>
        <h1><a href="#/coffee/${c.id}" style="text-decoration:none">${esc(c.name)}</a></h1>
        <div class="chips">${[c.origin, c.region, c.process, c.roast_level && c.roast_level + " roast", c.varietal].filter(Boolean).map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>
        ${c.roaster_notes ? `<p class="lede"><span class="muted">On the bag:</span> ${esc(c.roaster_notes)}</p>` : ""}
        <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}
          ${b.roast_age_today != null ? `<dt>Today</dt><dd>${ageText(b.roast_age_today)}</dd>` : ""}</dl>
        ${ADMIN && b.notes ? `<p class="note">${esc(b.notes)}</p>` : ""}
        ${ADMIN ? `<div class="actions">
          ${b.status === "sealed" ? `<button id="open">Opened today</button>` : ""}
          ${b.status !== "finished" ? `<button id="finish">Finished it</button>` : `<button id="unfinish" class="link">Not finished after all</button>`}
          <a class="btn" href="#/bag/${b.id}/edit">Edit bag</a></div>` : ""}
        ${b.other_bags.length ? `<p class="small muted">Also bought ${b.other_bags.map((o) => `<a href="#/bag/${o.id}">${fmtDay(o.purchased_on) || "another time"}${o.city ? " in " + esc(o.city) : ""}</a>`).join(", ")}.</p>` : ""}
      </div>
    </div>
    <div class="section-h"><h2>Reviews</h2><span class="muted small">one per brew method — edit as you dial it in</span></div>
    <div class="reviews">${b.reviews.length ? b.reviews.map((r) => reviewCard(r, b.id)).join("") : `<div class="muted">Not reviewed yet.</div>`}</div>
    ${ADMIN ? `<h3 style="margin-top:18px">${b.reviews.length ? "Brewed it another way?" : "Review it"}</h3>
      <div class="method-add">${untried.map((m) => `<a class="chip" href="#/bag/${b.id}/review?method=${encodeURIComponent(m)}">＋ ${esc(m)}</a>`).join("")}
      <a class="chip" href="#/bag/${b.id}/review">＋ Other…</a></div>` : ""}
    <footer class="foot"></footer>`;
  if (!ADMIN) return;
  const put = async (body, msg) => { await api("bags/" + id, { method: "PUT", body }); toast(msg); bagView(id); };
  $("#open") && ($("#open").onclick = () => put({ opened_on: todayISO() }, "Marked open"));
  $("#finish") && ($("#finish").onclick = () => put({ finished_on: todayISO(), opened_on: b.opened_on || todayISO() }, "Marked finished"));
  $("#unfinish") && ($("#unfinish").onclick = () => put({ finished_on: null }, "Back on the shelf"));
  $("#addph").onchange = async (e) => {
    const fd = new FormData();
    for (const file of e.target.files) fd.append("file", await shrink(file), "photo.jpg");
    toast("Uploading…");
    await api(`bags/${id}/photos`, { method: "POST", body: fd });
    bagView(id);
  };
  $$("[data-del]").forEach((x) => x.onclick = async () => { if (!confirm("Remove this photo?")) return; await api("photos/" + x.dataset.del, { method: "DELETE" }); bagView(id); });
  $$("[data-cover]").forEach((x) => x.onclick = async () => { await api(`photos/${x.dataset.cover}/cover`, { method: "POST" }); bagView(id); });
}

/* ---------- photos ---------- */
async function shrink(file, max = 2000) {
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const cv = document.createElement("canvas");
    cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k);
    cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
    return await new Promise((res) => cv.toBlob((b) => res(b || file), "image/jpeg", 0.88));
  } catch { return file; }
}

/* ---------- review form ---------- */
// field labels and placeholders that fit the brew method
function methodKind(m) {
  const s = (m || "").toLowerCase();
  const pour = { l: { water: "Water (g)", time: "Brew time" }, h: { dose: "20", water: "320", temp: "96 °C / 205 °F", time: "3:15", recipe: "50 g bloom 45 s, then two pours" } };
  if (/espresso|ristretto|lungo/.test(s)) return { l: { water: "Yield (g)", time: "Shot time" }, h: { dose: "18", water: "36", temp: "93 °C", time: "28 s", recipe: "5 s pre-infusion, 9 bar" } };
  if (/nitro/.test(s)) return { l: { water: "Water (g)", time: "Steep time" }, h: { dose: "100", water: "800", temp: "fridge / room temp", time: "18 h", recipe: "keg, N2 at 30–40 psi, stout faucet" } };
  if (/cold/.test(s)) return { l: { water: "Water (g)", time: "Steep time" }, h: { dose: "100", water: "800", temp: "fridge / room temp", time: "18 h", recipe: "steeped in the fridge, diluted 1:1 to serve" } };
  if (/french|press|clever|immersion/.test(s) && !/aero/.test(s)) return { l: { water: "Water (g)", time: "Steep time" }, h: { dose: "30", water: "500", temp: "96 °C / 205 °F", time: "4:00", recipe: "break the crust at 4 min, skim, wait 5 min" } };
  if (/percolat/.test(s)) return { l: { water: "Water (g)", time: "Perk time" }, h: { dose: "45", water: "1000", temp: "stovetop, medium", time: "7:00", recipe: "coarse grind, off the heat once it's dark amber" } };
  if (/aero/.test(s)) return { l: { water: "Water (g)", time: "Brew time" }, h: { dose: "15", water: "230", temp: "90 °C", time: "2:00", recipe: "inverted, stir 10 s, press 30 s" } };
  if (/moka/.test(s)) return { l: { water: "Water (g)", time: "Brew time" }, h: { dose: "16", water: "150", temp: "hot water in base", time: "4:00", recipe: "medium-low heat, off at first sputter" } };
  if (/drip|machine|batch/.test(s)) return { l: { water: "Water (g)", time: "Brew time" }, h: { dose: "60", water: "1000", temp: "machine", time: "5:30", recipe: "paper filter, 8-cup setting" } };
  return pour;
}
async function reviewView(bagId, method) {
  const [b, f, ft] = await Promise.all([api("bags/" + bagId), facets(), features()]);
  const existing = method && b.reviews.find((r) => r.method.toLowerCase() === method.toLowerCase());
  const r = existing || { method: method || "", brewed_on: todayISO(), rating: null };
  let prefill = {};
  if (!existing && method) prefill = await api(`last-recipe?method=${encodeURIComponent(method)}&coffee_id=${b.coffee.id}`);
  const v = (k) => r[k] ?? prefill[k] ?? "";
  const taken = new Set(b.reviews.map((x) => x.method.toLowerCase()));
  const methods = [...new Set([...f.methods, ...(r.method ? [r.method] : [])])];
  const sugg = [...new Set([...(b.coffee.roaster_notes || "").toLowerCase().split(/\s*[,/;&]\s*|\s+and\s+/).filter(Boolean), ...f.flavors])].slice(0, 30);
  view.innerHTML = `
    <div class="page-h"><a class="crumb" href="#/bag/${bagId}">← ${esc(b.coffee.name)} · ${esc(b.roaster.name)}</a>
      <h1>${existing ? "Edit review" : "Review"}${r.method ? ": " + esc(r.method) : ""}</h1>
      ${b.roast_date ? `<div class="small muted">Roasted ${fmtDay(b.roast_date)}</div>` : ""}</div>
    <form class="form" id="rf" autocomplete="off">
      <fieldset><legend>How was it?</legend>
        <label class="f"><span>Brew method</span>
          <select name="method" required><option value="">Pick one…</option>${methods.map((m) => `<option ${m.toLowerCase() === (r.method || "").toLowerCase() ? "selected" : ""}
            ${taken.has(m.toLowerCase()) && m.toLowerCase() !== (r.method || "").toLowerCase() ? "disabled" : ""}>${esc(m)}</option>`).join("")}<option value="__new">Another method…</option></select></label>
        <label class="f hidden" id="newm"><span>New method name</span><input name="new_method" placeholder="e.g. Origami dripper"></label>
        <div class="f"><span>Rating</span><div class="rating-in"><span id="rv">${rate(r.rating)}</span>
          <div><input type="range" name="rating" min="0" max="10" step="0.5" value="${r.rating ?? 7}" ${r.rating == null ? 'data-unset="1"' : ""} aria-label="Rating out of 10">
          <div class="ticks"><span>0</span><span>2</span><span>4</span><span>6</span><span>8</span><span>10</span></div></div></div>
          ${r.rating == null ? `<span class="hint" id="rhint">Slide to rate</span>` : ""}</div>
        <label class="f"><span>Tasting notes (comma separated)</span><input name="flavors" value="${esc(r.flavors || "")}" placeholder="blueberry, cocoa, jasmine"></label>
        <div class="chips" id="fl">${sugg.map((s) => `<button type="button" class="chip" data-f="${esc(s)}">${esc(s)}</button>`).join("")}</div>
        <label class="f"><span>Notes</span><textarea name="notes" placeholder="What stood out? What would you change next time?">${esc(r.notes || "")}</textarea></label>
      </fieldset>
      <fieldset><legend>Recipe</legend>
        <div class="f2"><label class="f"><span>Grinder</span><input name="grinder" list="grinders" value="${esc(v("grinder") || ft.default_grinder || "")}"></label>
          <label class="f"><span>Grind setting</span><input name="grind" value="${esc(v("grind"))}" inputmode="decimal" placeholder="e.g. 21"></label></div>
        <label class="f"><span>Brewer / machine</span><input name="brewer" list="brewers" value="${esc(v("brewer"))}" placeholder="e.g. Breville Precision Brewer"></label>
        <div class="f3 tight"><label class="f"><span>Coffee (g)</span><input name="dose_g" value="${esc(v("dose_g"))}" inputmode="decimal" data-h="dose"></label>
          <label class="f"><span data-l="water">Water (g)</span><input name="water_g" value="${esc(v("water_g"))}" inputmode="decimal" data-h="water"></label>
          <label class="f"><span>Ratio</span><input id="ratio" readonly tabindex="-1"></label></div>
        <div class="f3"><label class="f"><span>Water temp</span><input name="temp" value="${esc(v("temp"))}" data-h="temp"></label>
          <label class="f"><span data-l="time">Brew time</span><input name="brew_time" value="${esc(v("brew_time"))}" data-h="time"></label>
          <label class="f"><span>Brewed on</span><input name="brewed_on" type="date" value="${esc(r.brewed_on || todayISO())}"></label></div>
        <label class="f"><span>Technique</span><input name="recipe" value="${esc(v("recipe"))}" data-h="recipe"></label>
        <div class="hint" id="prehint"></div>
      </fieldset>
      <datalist id="grinders">${f.grinders.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <datalist id="brewers">${(f.brewers || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <div class="actions sticky-actions"><button class="primary big" type="submit">Save review</button>
        <a class="btn" href="#/bag/${bagId}">Cancel</a>${existing ? `<button type="button" class="danger" id="del">Delete</button>` : ""}</div>
    </form>`;
  const fm = $("#rf");
  const range = fm.rating;
  range.oninput = () => { delete range.dataset.unset; $("#rv").innerHTML = rate(+range.value); $("#rhint") && $("#rhint").remove(); };
  const applyKind = () => {
    const k = methodKind(fm.method.value === "__new" ? fm.new_method.value : fm.method.value);
    for (const [key, val] of Object.entries(k.l)) $(`[data-l=${key}]`).textContent = val;
    for (const [key, val] of Object.entries(k.h)) $(`[data-h=${key}]`).placeholder = val;
  };
  const RECIPE_KEYS = ["grinder", "grind", "brewer", "dose_g", "water_g", "temp", "brew_time", "recipe"];
  RECIPE_KEYS.forEach((k) => fm[k].addEventListener("input", () => { fm[k].dataset.touched = "1"; }));
  const preHint = (p, m) => {
    $("#prehint").textContent = existing || !p ? "" : p.source === "last" ? `Recipe filled in from the last time you brewed ${m}.`
      : p.source === "preset" ? `Equipment filled in from your ${m} preset (More → Brew methods).` : "";
  };
  preHint(method && prefill, method);
  const refill = async () => {
    const m = fm.method.value;
    if (existing || !m || m === "__new") return;
    const p = await api(`last-recipe?method=${encodeURIComponent(m)}&coffee_id=${b.coffee.id}`);
    for (const k of RECIPE_KEYS) if (!fm[k].dataset.touched) fm[k].value = p[k] ?? "";
    ratio(); preHint(p, m);
  };
  fm.method.onchange = () => { $("#newm").classList.toggle("hidden", fm.method.value !== "__new"); applyKind(); refill(); };
  fm.new_method.oninput = applyKind;
  applyKind();
  const ratio = () => { const d = parseFloat(fm.dose_g.value), w = parseFloat(fm.water_g.value); $("#ratio").value = d && w ? `1:${(w / d).toFixed(1)}` : ""; };
  fm.dose_g.oninput = fm.water_g.oninput = ratio; ratio();
  $$("[data-f]").forEach((x) => x.onclick = () => {
    const cur = fm.flavors.value.split(",").map((s) => s.trim()).filter(Boolean);
    const i = cur.findIndex((s) => s.toLowerCase() === x.dataset.f);
    if (i >= 0) cur.splice(i, 1); else cur.push(x.dataset.f);
    fm.flavors.value = cur.join(", "); x.classList.toggle("on", i < 0);
  });
  const curF = new Set(fm.flavors.value.toLowerCase().split(",").map((s) => s.trim()));
  $$("[data-f]").forEach((x) => x.classList.toggle("on", curF.has(x.dataset.f)));
  fm.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(fm));
    if (d.method === "__new") d.method = d.new_method;
    delete d.new_method;
    if (!d.method) return toast("Pick a brew method");
    if (range.dataset.unset) d.rating = null;
    if (existing) d.old_method = existing.method;
    try {
      const res = await api(`bags/${bagId}/reviews`, { method: "PUT", body: d });
      S.facets = null;
      toast(res.created ? "Review saved" : "Review updated");
      location.hash = `#/bag/${bagId}`;
    } catch (err) { toast(err.message); }
  };
  $("#del") && ($("#del").onclick = async () => {
    if (!confirm(`Delete the ${existing.method} review?`)) return;
    await api("reviews/" + existing.id, { method: "DELETE" }); S.facets = null; location.hash = `#/bag/${bagId}`;
  });
}

/* ---------- add / edit bag ---------- */
const PROCESSES = ["washed", "natural", "honey", "anaerobic natural", "anaerobic washed", "wet-hulled", "carbonic maceration", "decaf (swiss water)", "decaf (sugarcane EA)"];
const ROASTS = ["", "light", "medium-light", "medium", "medium-dark", "dark", "omni", "filter", "espresso"];
const fld = (label, name, val = "", attrs = "") => `<label class="f"><span>${label}</span><input name="${name}" value="${esc(val ?? "")}" ${attrs}></label>`;

function coffeeFields(c = {}, prefix = "c_") {
  return `
    ${fld("Coffee name", prefix + "name", c.name, `required list="coffee-names" placeholder="e.g. Ethiopia Guji Hambela"`)}
    <div class="f2">${fld("Origin (country)", prefix + "origin", c.origin, `list="origins" placeholder="Ethiopia"`)}${fld("Region", prefix + "region", c.region, `placeholder="Guji, Oromia"`)}</div>
    <div class="f2">${fld("Process", prefix + "process", c.process, `list="processes"`)}
      <label class="f"><span>Roast level</span><select name="${prefix}roast_level">${ROASTS.map((r) => `<option value="${r}" ${r === (c.roast_level || "") ? "selected" : ""}>${r ? cap(r) : "—"}</option>`).join("")}</select></label></div>
    ${fld("Tasting notes on the bag", prefix + "roaster_notes", c.roaster_notes, `placeholder="blueberry, jasmine, cocoa"`)}
    <details ${c.producer || c.varietal || c.altitude ? "open" : ""}><summary>Producer, varietal, altitude…</summary><div style="display:grid;gap:10px;margin-top:10px">
      ${fld("Producer / farm / co-op", prefix + "producer", c.producer)}
      <div class="f2">${fld("Varietal", prefix + "varietal", c.varietal, `placeholder="Heirloom, 74158"`)}${fld("Altitude", prefix + "altitude", c.altitude, `placeholder="1,900–2,100 masl"`)}</div>
      <label class="check"><input type="checkbox" name="${prefix}is_blend" ${c.is_blend ? "checked" : ""}> It's a blend</label>
    </div></details>`;
}
function bagFields(b = {}, f) {
  return `
    <div class="f2">${fld("Bought on", "purchased_on", b.purchased_on || todayISO(), `type="date"`)}${fld("Roast date", "roast_date", b.roast_date, `type="date"`)}</div>
    <div class="row"><button type="button" id="gps">${icon("pin")} Use my location</button><label class="check"><input type="checkbox" name="online" ${b.online ? "checked" : ""}> Bought online / delivered</label></div>
    <div id="gpsnote" class="hint"></div>
    <div id="where" ${b.online ? 'class="hidden"' : ""}>
      ${fld("Shop / café", "shop", b.shop, `placeholder="Where you bought it"`)}
      <div class="f3" style="margin-top:10px">${fld("City", "city", b.city)}${fld("State / region", "region", b.region)}${fld("Country", "country", b.country)}</div>
      <input type="hidden" name="lat" value="${b.lat ?? ""}"><input type="hidden" name="lng" value="${b.lng ?? ""}">
    </div>
    ${fld("Trip", "trip", b.trip, `list="trips" placeholder="e.g. Portland, Oct 2026 (leave blank if local)"`)}
    <div class="f3 tight">${fld("Size", "weight", b.weight_g ? grams(b.weight_g) : "", `placeholder="12 oz / 250 g"`)}${fld("Price", "price", b.price, `inputmode="decimal"`)}${fld("Currency", "currency", b.currency || f.default_currency, `maxlength="3" style="text-transform:uppercase"`)}</div>
    <label class="f"><span>Private notes</span><textarea name="notes" placeholder="Who recommended it, what the barista said…">${esc(b.notes || "")}</textarea></label>`;
}
function datalists(f) {
  return `<datalist id="origins">${f.origins.map((o) => `<option value="${esc(o.name)}">`).join("")}</datalist>
    <datalist id="processes">${[...new Set([...PROCESSES, ...f.processes.map((p) => p.name)])].map((p) => `<option value="${esc(p)}">`).join("")}</datalist>
    <datalist id="trips">${f.trips.map((t) => `<option value="${esc(t.name)}">`).join("")}</datalist>
    <datalist id="roaster-names">${f.roasters.map((r) => `<option value="${esc(r.name)}">${esc([r.city, r.country].filter(Boolean).join(", "))}</option>`).join("")}</datalist>
    <datalist id="coffee-names"></datalist>`;
}
function wireBagFields(fm) {
  fm.online.onchange = () => $("#where").classList.toggle("hidden", fm.online.checked);
  $("#gps").onclick = () => {
    if (!navigator.geolocation) return toast("This browser can't share location");
    $("#gpsnote").innerHTML = `<span class="spinner"></span> Finding you…`;
    navigator.geolocation.getCurrentPosition(async (pos) => {
      try {
        const p = await api(`place?lat=${pos.coords.latitude}&lng=${pos.coords.longitude}`);
        fm.lat.value = p.lat; fm.lng.value = p.lng;
        const known = p.known && p.known[0];
        const src = known || p;
        for (const k of ["shop", "city", "region", "country"]) if (src[k]) fm[k].value = src[k];
        $("#gpsnote").textContent = known ? `You've bought here before: ${known.shop}.` : p.warning || `Got it: ${[p.shop, p.city, p.country].filter(Boolean).join(", ")}. Fix the shop name if it's wrong.`;
      } catch (e) { $("#gpsnote").textContent = e.message; }
    }, (err) => { $("#gpsnote").textContent = err.code === 1 ? "Location permission was denied (it needs HTTPS: use coffee.nickandrade.com)." : "Couldn't get your location."; },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  };
}
function pick(d, prefix) {
  const o = {};
  for (const [k, v] of Object.entries(d)) if (k.startsWith(prefix)) o[k.slice(prefix.length)] = v;
  return o;
}

async function addView(p) {
  const [f, ft] = await Promise.all([facets(true), features()]);
  let preset = null;
  if (p.get("coffee")) preset = await api("coffees/" + p.get("coffee"));
  const uploads = [];
  view.innerHTML = `
    <div class="page-h"><h1>${preset ? "Bought it again" : "New bag"}</h1>
      <div class="muted small">${preset ? esc(preset.name) + " · " + esc(preset.roaster.name) : "Snap the bag, let Claude read the label, then check the details."}</div></div>
    <form class="form" id="af" autocomplete="off">
      <fieldset><legend>Photos</legend>
        <div class="picker"><label class="btn primary">${icon("camera")} Take or add photos<input type="file" accept="image/*" multiple id="ph"></label>
          ${!preset && ft.label_reading ? `<button type="button" id="read" disabled>${icon("sparkle")} Read the label</button>` : ""}</div>
        <div class="thumbs" id="thumbs"></div>
        <div class="hint">${preset ? "Optional." : ft.label_reading ? "Front and back of the bag work best. Reading takes ~10 seconds." : "Label reading is off until ANTHROPIC_API_KEY is set in /etc/conf.d/roasted."}</div>
        <div id="readnote"></div>
      </fieldset>
      <fieldset ${preset ? 'class="hidden"' : ""}><legend>Roaster</legend>
        ${fld("Roaster", "r_name", preset ? preset.roaster.name : "", `required list="roaster-names" placeholder="e.g. Heart Coffee Roasters"`)}
        <div id="rnote" class="hint"></div>
        <details id="rmore"><summary>Roaster's city & website</summary><div style="display:grid;gap:10px;margin-top:10px">
          <div class="f3">${fld("City", "r_city")}${fld("State / region", "r_region")}${fld("Country", "r_country")}</div>${fld("Website", "r_website", "", `inputmode="url"`)}</div></details>
      </fieldset>
      <fieldset ${preset ? 'class="hidden"' : ""}><legend>Coffee</legend>${coffeeFields(preset || {})}<div id="cnote" class="hint"></div></fieldset>
      <fieldset><legend>This bag</legend>${bagFields({}, f)}</fieldset>
      ${datalists(f)}
      <div class="actions sticky-actions"><button class="primary big" type="submit">Save bag</button><a class="btn" href="#/">Cancel</a></div>
    </form>`;
  const fm = $("#af");
  wireBagFields(fm);
  const thumbs = () => {
    $("#thumbs").innerHTML = uploads.map((t, i) => `<span class="ti"><img src="/photos/t-${esc(t)}" alt=""><button type="button" data-rm="${i}" aria-label="Remove photo">${icon("x")}</button></span>`).join("");
    $$("[data-rm]").forEach((x) => x.onclick = () => { uploads.splice(+x.dataset.rm, 1); thumbs(); });
    $("#read") && ($("#read").disabled = !uploads.length);
  };
  $("#ph").onchange = async (e) => {
    const fd = new FormData();
    for (const file of e.target.files) fd.append("file", await shrink(file), "photo.jpg");
    e.target.value = "";
    $("#readnote").innerHTML = `<span class="spinner"></span> Uploading…`;
    try { const r = await api("uploads", { method: "POST", body: fd }); uploads.push(...r.uploads); $("#readnote").innerHTML = ""; thumbs(); }
    catch (err) { $("#readnote").textContent = err.message; }
  };
  // roaster -> coffee name suggestions, and "this is a coffee you already have"
  let roasterCoffees = [];
  const onRoaster = async () => {
    const r = f.roasters.find((x) => x.name.toLowerCase() === fm.r_name.value.trim().toLowerCase());
    $("#rnote").textContent = r ? `Existing roaster${r.city ? " in " + r.city : ""}.` : fm.r_name.value.trim() ? "New roaster." : "";
    roasterCoffees = r ? (await api("roasters/" + r.id)).coffees : [];
    $("#coffee-names").innerHTML = roasterCoffees.map((c) => `<option value="${esc(c.name)}">`).join("");
    onCoffee();
  };
  const onCoffee = () => {
    const c = roasterCoffees.find((x) => x.name.toLowerCase() === fm.c_name.value.trim().toLowerCase());
    if (!c) { $("#cnote").textContent = fm.c_name.value.trim() && fm.r_name.value.trim() ? "New coffee." : ""; return; }
    $("#cnote").innerHTML = `Already in the log: ${c.bag_count} bag${c.bag_count === 1 ? "" : "s"}${c.avg_rating != null ? `, averaging ${n1(c.avg_rating)}` : ""}. This bag will be added to it.`;
    for (const k of ["origin", "region", "process", "roaster_notes", "producer", "varietal", "altitude"]) if (!fm["c_" + k].value && c[k]) fm["c_" + k].value = c[k];
    if (!fm.c_roast_level.value && c.roast_level) fm.c_roast_level.value = c.roast_level;
  };
  if (!preset) { fm.r_name.addEventListener("change", onRoaster); fm.c_name.addEventListener("change", onCoffee); }
  $("#read") && ($("#read").onclick = async () => {
    const btn = $("#read"); btn.disabled = true;
    $("#readnote").innerHTML = `<div class="note"><span class="spinner"></span> Reading the label…</div>`;
    try {
      const res = await api("label", { method: "POST", body: { uploads } });
      const F = res.fields;
      const set = (name, val) => { if (val != null && val !== "" && fm[name]) { if (fm[name].type === "checkbox") fm[name].checked = !!val; else fm[name].value = val; } };
      set("r_name", res.roaster_match ? res.roaster_match.name : F.roaster.name);
      for (const k of ["city", "region", "country", "website"]) set("r_" + k, F.roaster[k]);
      if (F.roaster.city || F.roaster.website) $("#rmore").open = true;
      set("c_name", res.coffee_match ? res.coffee_match.name : F.coffee.name);
      for (const k of ["origin", "region", "producer", "varietal", "process", "altitude", "roaster_notes", "is_blend"]) set("c_" + k, F.coffee[k]);
      if (F.coffee.roast_level && ROASTS.includes(F.coffee.roast_level)) fm.c_roast_level.value = F.coffee.roast_level;
      set("roast_date", F.bag.roast_date); set("weight", F.bag.weight); set("price", F.bag.price); set("currency", F.bag.currency);
      if (F.coffee.producer || F.coffee.varietal) fm.c_producer.closest("details").open = true;
      const bits = [];
      if (res.roaster_match) bits.push(`Matched your existing roaster <strong>${esc(res.roaster_match.name)}</strong>${F.roaster.name && F.roaster.name !== res.roaster_match.name ? ` (label says “${esc(F.roaster.name)}”)` : ""}.`);
      if (res.coffee_match) bits.push(`Looks like <strong>${esc(res.coffee_match.name)}</strong>, which you've bought before.`);
      if (F.bag.roast_date_printed) bits.push(`Roast date printed as “${esc(F.bag.roast_date_printed)}”.`);
      if (F.uncertain && F.uncertain.length) bits.push(`Double-check: ${F.uncertain.map(esc).join(", ")}.`);
      $("#readnote").innerHTML = `<div class="note ok">Filled in from the label. ${bits.join(" ")}</div>`;
      await onRoaster();
    } catch (err) { $("#readnote").innerHTML = `<div class="note warn">${esc(err.message)}</div>`; }
    btn.disabled = false;
  });
  fm.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(fm));
    d.online = fm.online.checked; d.c_is_blend = fm.c_is_blend.checked;
    const roaster = preset ? { id: preset.roaster.id } : pick(d, "r_");
    const coffee = preset ? { id: preset.id } : pick(d, "c_");
    const bag = {};
    for (const k of ["purchased_on", "roast_date", "shop", "city", "region", "country", "lat", "lng", "trip", "online", "weight", "price", "currency", "notes"]) bag[k] = d[k];
    const btn = fm.querySelector("[type=submit]"); btn.disabled = true;
    try {
      const b = await api("bags", { method: "POST", body: { roaster, coffee, bag, uploads } });
      S.facets = null; toast("Bag saved");
      location.hash = `#/bag/${b.id}`;
    } catch (err) { toast(err.message); btn.disabled = false; }
  };
}

async function bagEditView(id) {
  const [b, f] = await Promise.all([api("bags/" + id), facets()]);
  view.innerHTML = `
    <div class="page-h"><a class="crumb" href="#/bag/${id}">← ${esc(b.coffee.name)}</a><h1>Edit bag</h1></div>
    <form class="form" id="bf" autocomplete="off">
      <fieldset><legend>This bag</legend>${bagFields(b, f)}
        <div class="f2">${fld("Opened on", "opened_on", b.opened_on, `type="date"`)}${fld("Finished on", "finished_on", b.finished_on, `type="date"`)}</div></fieldset>
      <fieldset><legend>Which coffee</legend><div class="hint">Wrong coffee? Move this bag to another one.</div>
        <select name="coffee_id">${(await api("coffees?sort=roaster")).coffees.map((c) => `<option value="${c.id}" ${c.id === b.coffee.id ? "selected" : ""}>${esc(c.roaster_name)} — ${esc(c.name)}</option>`).join("")}</select></fieldset>
      ${datalists(f)}
      <div class="actions sticky-actions"><button class="primary big" type="submit">Save</button><a class="btn" href="#/bag/${id}">Cancel</a>
        <button type="button" class="danger" id="del">Delete bag</button></div>
    </form>`;
  const fm = $("#bf");
  wireBagFields(fm);
  fm.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(fm));
    d.online = fm.online.checked;
    for (const k of ["opened_on", "finished_on", "roast_date", "lat", "lng"]) if (d[k] === "") d[k] = null;
    // typed a different city without using GPS: let the server look the new place up
    if ((d.city !== (b.city || "") || d.country !== (b.country || "")) && String(d.lat ?? "") === String(b.lat ?? "")) { delete d.lat; delete d.lng; }
    try { await api("bags/" + id, { method: "PUT", body: d }); S.facets = null; toast("Saved"); location.hash = `#/bag/${id}`; }
    catch (err) { toast(err.message); }
  };
  $("#del").onclick = async () => {
    if (!confirm("Delete this bag, its photos and its reviews?")) return;
    const r = await api("bags/" + id, { method: "DELETE" }); S.facets = null;
    location.hash = r.coffee_has_bags ? `#/coffee/${r.coffee_id}` : "#/";
  };
}

async function coffeeEditView(id) {
  const [c, f, all] = await Promise.all([api("coffees/" + id), facets(), api("coffees?sort=roaster")]);
  view.innerHTML = `
    <div class="page-h"><a class="crumb" href="#/coffee/${id}">← ${esc(c.name)}</a><h1>Edit coffee</h1></div>
    <form class="form" id="cf" autocomplete="off">
      <fieldset><legend>Coffee</legend>${coffeeFields(c, "")}
        <label class="f"><span>Roaster</span><select name="roaster_id">${f.roasters.map((r) => `<option value="${r.id}" ${r.id === c.roaster_id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
        <label class="f"><span>Description</span><textarea name="description">${esc(c.description || "")}</textarea></label></fieldset>
      ${datalists(f)}
      <div class="actions sticky-actions"><button class="primary big" type="submit">Save</button><a class="btn" href="#/coffee/${id}">Cancel</a></div>
      <fieldset><legend>Duplicate?</legend><div class="hint">Merge moves every bag and review into the other coffee, fills in its blanks, then removes this one.</div>
        <div class="row"><select id="into" style="flex:1"><option value="">Merge into…</option>${all.coffees.filter((x) => x.id !== id).map((x) => `<option value="${x.id}">${esc(x.roaster_name)} — ${esc(x.name)}</option>`).join("")}</select>
        <button type="button" id="merge">Merge</button></div>
        <div class="row" style="margin-top:8px"><button type="button" class="danger" id="del">Delete coffee and all its bags</button></div></fieldset>
    </form>`;
  const fm = $("#cf");
  fm.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(fm)); d.is_blend = fm.is_blend.checked;
    try { await api("coffees/" + id, { method: "PUT", body: d }); S.facets = null; toast("Saved"); location.hash = `#/coffee/${id}`; }
    catch (err) { toast(err.message); }
  };
  $("#merge").onclick = async () => {
    const into = +$("#into").value; if (!into) return toast("Pick a coffee to merge into");
    if (!confirm("Merge this coffee into the one you picked?")) return;
    await api(`coffees/${id}/merge`, { method: "POST", body: { into } }); S.facets = null; location.hash = `#/coffee/${into}`;
  };
  $("#del").onclick = async () => {
    if (!confirm(`Delete ${c.name}, its ${c.bags.length} bag(s), photos and reviews?`)) return;
    await api("coffees/" + id, { method: "DELETE" }); S.facets = null; location.hash = "#/coffees";
  };
}

/* ---------- roasters ---------- */
async function roastersView() {
  view.innerHTML = `<div class="controls"><input class="search" id="rq" type="search" placeholder="Find a roaster or city…" autocomplete="off">
      <select id="rsort"><option value="name">A–Z</option><option value="bags">Most bags</option><option value="rating">Highest rated</option><option value="recent">Recently bought</option></select></div>
    <div class="grid" id="rg"></div><footer class="foot"></footer>`;
  $("#rsort").value = S.rsort;
  let rows = [];
  const draw = () => {
    const q = $("#rq").value.trim().toLowerCase();
    const list = rows.filter((r) => !q || [r.name, r.city, r.region, r.country].join(" ").toLowerCase().includes(q));
    $("#rg").innerHTML = list.length ? list.map((r) => `<a class="card" href="#/roaster/${r.id}">
      <div class="img">${r.photo ? img(r.photo) : BEAN}</div>${r.avg_rating != null ? rate(r.avg_rating, "sm") : ""}
      <div class="b"><div class="t">${esc(r.name)}</div><div class="r">${esc([r.city, r.region, r.country].filter(Boolean).join(", "))}</div>
      <div class="k">${r.coffee_count} coffee${r.coffee_count === 1 ? "" : "s"} · ${r.bag_count} bag${r.bag_count === 1 ? "" : "s"}</div></div></a>`).join("")
      : `<div class="empty">No roasters yet.</div>`;
  };
  const load = async () => { rows = (await api("roasters?sort=" + S.rsort)).roasters; draw(); };
  $("#rq").oninput = draw;
  $("#rsort").onchange = (e) => { S.rsort = e.target.value; load(); };
  await load();
}
async function roasterView(id) {
  const r = await api("roasters/" + id);
  const loc = [r.city, r.region, r.country].filter(Boolean).join(", ");
  view.innerHTML = `
    <div class="page-h"><a class="crumb" href="#/roasters">← Roasters</a>
      <div class="spread"><h1>${esc(r.name)}</h1>${ADMIN ? `<a class="btn" href="#/roaster/${id}/edit">Edit</a>` : ""}</div>
      <div class="muted">${esc(loc)}${r.website ? ` · <a href="${esc(r.website)}" target="_blank" rel="noopener">${esc(r.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>` : ""}</div>
      ${r.notes ? `<p>${esc(r.notes)}</p>` : ""}</div>
    ${r.places.length ? `<div class="chips" style="margin-bottom:6px">${r.places.map((p) => `<span class="chip">${esc([p.shop, p.city].filter(Boolean).join(", ") || "somewhere")}${p.n > 1 ? " ×" + p.n : ""}</span>`).join("")}</div>` : ""}
    <div class="section-h"><h2>Coffees</h2><span class="muted small">${r.coffees.length}</span></div>
    <div class="grid">${r.coffees.map(coffeeCard).join("") || `<div class="empty">No coffees.</div>`}</div>
    <footer class="foot"></footer>`;
}
async function roasterEditView(id) {
  const [r, f] = await Promise.all([api("roasters/" + id), facets()]);
  view.innerHTML = `
    <div class="page-h"><a class="crumb" href="#/roaster/${id}">← ${esc(r.name)}</a><h1>Edit roaster</h1></div>
    <form class="form" id="ef" autocomplete="off"><fieldset><legend>Roaster</legend>
      ${fld("Name", "name", r.name, "required")}
      <div class="f3">${fld("City", "city", r.city)}${fld("State / region", "region", r.region)}${fld("Country", "country", r.country)}</div>
      ${fld("Website", "website", r.website, `inputmode="url"`)}
      <label class="f"><span>Notes</span><textarea name="notes">${esc(r.notes || "")}</textarea></label></fieldset>
      <div class="actions sticky-actions"><button class="primary big" type="submit">Save</button><a class="btn" href="#/roaster/${id}">Cancel</a></div>
      <fieldset><legend>Duplicate?</legend><div class="hint">Merge moves this roaster's coffees into the other one (same-named coffees are combined).</div>
        <div class="row"><select id="into" style="flex:1"><option value="">Merge into…</option>${f.roasters.filter((x) => x.id !== id).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select><button type="button" id="merge">Merge</button></div>
        ${r.coffees.length ? "" : `<div class="row" style="margin-top:8px"><button type="button" class="danger" id="del">Delete roaster</button></div>`}</fieldset>
    </form>`;
  const fm = $("#ef");
  fm.onsubmit = async (e) => {
    e.preventDefault();
    try { await api("roasters/" + id, { method: "PUT", body: Object.fromEntries(new FormData(fm)) }); S.facets = null; toast("Saved"); location.hash = `#/roaster/${id}`; }
    catch (err) { toast(err.message); }
  };
  $("#merge").onclick = async () => {
    const into = +$("#into").value; if (!into) return toast("Pick a roaster to merge into");
    if (!confirm(`Merge ${r.name} into the roaster you picked?`)) return;
    await api(`roasters/${id}/merge`, { method: "POST", body: { into } }); S.facets = null; location.hash = `#/roaster/${into}`;
  };
  $("#del") && ($("#del").onclick = async () => { await api("roasters/" + id, { method: "DELETE" }); S.facets = null; location.hash = "#/roasters"; });
}

/* ---------- map ---------- */
function loadLeaflet() {
  if (window.L) return Promise.resolve();
  return new Promise((res, rej) => {
    const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "/static/leaflet/leaflet.css"; document.head.appendChild(css);
    const s = document.createElement("script"); s.src = "/static/leaflet/leaflet.js"; s.onload = res; s.onerror = () => rej(new Error("Couldn't load the map library")); document.head.appendChild(s);
  });
}
async function mapView() {
  view.innerHTML = `<div class="spread" style="margin-top:16px"><h2>Where I've bought coffee</h2><span class="muted small" id="mcount"></span></div><div id="map"></div>`;
  const [d] = await Promise.all([api("map"), loadLeaflet()]);
  $("#mcount").textContent = `${d.points.length} bag${d.points.length === 1 ? "" : "s"} with a location`;
  const map = L.map("map", { scrollWheelZoom: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(map);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--green-accent").trim();
  const pts = [];
  for (const p of d.points) {
    pts.push([p.lat, p.lng]);
    L.circleMarker([p.lat, p.lng], { radius: 8, color: "#fff", weight: 2, fillColor: accent, fillOpacity: 0.9 }).addTo(map)
      .bindPopup(`${p.photo ? `<img src="/photos/${esc(p.photo)}" alt="">` : ""}<strong><a href="#/bag/${p.id}">${esc(p.coffee_name)}</a></strong><br>
        ${esc(p.roaster_name)}<br><span style="opacity:.75">${[esc([p.shop, p.city].filter(Boolean).join(", ")), fmtDay(p.purchased_on), p.best != null ? `best ${n1(p.best)}/10` : ""].filter(Boolean).join(" · ")}</span>`);
  }
  if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 12 }); else map.setView([37.98, -122.03], 4);
}

/* ---------- stats ---------- */
async function statsView() {
  const [s] = await Promise.all([api("stats"), features()]);
  const t = s.totals;
  const tile = (v, l) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  view.innerHTML = `
    <div class="tiles">
      ${tile(t.bags, "bags bought")}${tile(t.coffees, "different coffees")}${tile(t.roasters, "roasters")}
      ${tile(t.roaster_cities, "roaster cities")}${tile(t.origins, "origin countries")}${tile(t.kg, "kg of beans")}
      ${tile(t.reviews, "reviews")}${tile(t.avg_rating != null ? n1(t.avg_rating) : "–", "average rating")}
    </div>
    <div class="cols">
      <div><div class="section-h"><h2>By brew method</h2><span class="muted small">avg · reviews</span></div>${barsHTML(s.by_method, { label: (r) => esc(r.name) })}</div>
      <div><div class="section-h"><h2>By process</h2><span class="muted small">avg · reviews</span></div>${barsHTML(s.by_process)}</div>
      <div><div class="section-h"><h2>By origin</h2><span class="muted small">avg · reviews</span></div>${barsHTML(s.by_origin.filter((o) => o.n))}</div>
      <div><div class="section-h"><h2>By roast level</h2><span class="muted small">avg · reviews</span></div>${barsHTML(s.by_roast)}</div>
      <div><div class="section-h"><h2>Days off roast</h2><span class="muted small">when brewed · avg</span></div>${barsHTML(s.by_rest, { label: (r) => esc(r.name) })}</div>
      <div><div class="section-h"><h2>Where I bought</h2><span class="muted small">bags</span></div>
        ${barsHTML(s.places.map((p) => ({ ...p, name: [p.city, p.country && !/^(united states|usa|us)$/i.test(p.country) ? p.country : null].filter(Boolean).join(", ") })), { max: Math.max(1, ...s.places.map((p) => p.n)), value: (r) => r.n, fmt: (r) => String(r.n), label: (r) => esc(r.name), title: (r) => `${r.name}: ${r.n} bags` })}</div>
    </div>
    <div class="section-h"><h2>Top coffees</h2></div>
    <div class="grid">${s.top_coffees.map(coffeeCard).join("") || `<div class="muted">Rate a few brews first.</div>`}</div>
    <div class="cols">
      <div><div class="section-h"><h2>Roasters</h2><span class="muted small">bags · avg</span></div>
        <table class="table"><tbody>${s.top_roasters.map((r) => `<tr><td><a href="#/roaster/${r.id}">${esc(r.name)}</a> <span class="muted small">${esc(r.city || "")}</span></td><td class="num">${r.bags}</td><td class="num">${r.avg != null ? n1(r.avg) : "–"}</td></tr>`).join("")}</tbody></table></div>
      ${ADMIN ? `<div><div class="section-h"><h2>Grind settings that worked</h2><span class="muted small">avg · n</span></div>
        <table class="table"><thead><tr><th>Method</th><th>Grind</th><th class="num">Avg</th><th class="num">n</th></tr></thead><tbody>
        ${(s.grind || []).map((g) => `<tr><td>${esc(g.method)}</td><td>${esc(g.grind)}${g.grinder && g.grinder !== S.features?.default_grinder ? ` <span class="muted small">${esc(g.grinder)}</span>` : ""}</td><td class="num">${n1(g.avg)}</td><td class="num">${g.n}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No grind settings logged yet.</td></tr>`}
        </tbody></table>
        ${(s.spend || []).length ? `<div class="section-h"><h2>Spend</h2></div><table class="table"><tbody>${s.spend.map((x) => `<tr><td>${esc(x.currency)}</td><td class="num">${money(x.total, x.currency)}</td><td class="num">${x.bags} bag${x.bags === 1 ? "" : "s"}</td><td class="num">${x.per_lb ? money(x.per_lb, x.currency) + "/lb" : ""}</td></tr>`).join("")}</tbody></table>` : ""}</div>` : ""}
    </div>
    ${s.flavors.length ? `<div class="section-h"><h2>Tasting notes I keep writing</h2></div><div class="chips">${s.flavors.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : ""}
    <footer class="foot"></footer>`;
}

/* ---------- more ---------- */
async function moreView() {
  const [f, ft, me] = await Promise.all([facets(true), features(), api("me")]);
  view.innerHTML = `
    <div class="page-h"><h1>More</h1><div class="muted small">Signed in as ${esc(me.email === "lan" ? "a LAN editor" : me.email)}</div></div>
    <div class="form">
      <fieldset><legend>Brew methods</legend><div class="hint">In the order they're offered on a bag. The equipment pre-fills a method's first review; after that, its last review is used. Removing a method hides it; existing reviews keep it.</div>
        <datalist id="grinders">${f.grinders.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
        <datalist id="brewers">${(f.brewers || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
        <div id="mlist" style="display:grid;gap:10px"></div>
        <div class="actions"><button type="button" id="addm">＋ Add method</button><button class="primary" id="savem">Save methods</button></div></fieldset>
      <fieldset><legend>Download</legend><div class="actions"><a class="btn" href="/admin/api/export.csv">CSV (one row per review)</a><a class="btn" href="/admin/api/export.json">Everything as JSON</a></div></fieldset>
      <fieldset><legend>Features</legend>
        <div class="row">${ft.label_reading ? icon("check") : icon("off")} Label reading with Claude ${ft.label_reading ? "" : "<span class='hint'>— set ANTHROPIC_API_KEY in /etc/conf.d/roasted</span>"}</div>
        <div class="row">${ft.public_view ? icon("check") + " Public catalog at /" : icon("off") + " Public catalog is off (PUBLIC_VIEW=0)"}</div>
        <div class="row">${ft.mcp_private ? icon("check") : icon("off")} MCP for Claude: <code>${esc(location.origin)}/mcp/&lt;MCP_TOKEN&gt;</code> <span class="hint">(token in /etc/conf.d/roasted)</span></div>
        <div class="hint">Default grinder: ${esc(ft.default_grinder)} · currency: ${esc(ft.default_currency)}</div></fieldset>
      <div class="actions"><a class="btn" href="/" target="_blank">Open the public view</a></div>
    </div>`;
  let rows = (await api("methods")).methods;
  const read = () => $$(".mrow").map((el) => Object.fromEntries($$("input", el).map((i) => [i.name, i.value])));
  const draw = () => {
    $("#mlist").innerHTML = rows.map((m, i) => `<div class="mrow card-box" style="display:grid;gap:8px;padding:10px 12px">
      <div class="row"><input name="name" value="${esc(m.name)}" placeholder="Method" style="flex:1;font-weight:600">
        <button type="button" data-up="${i}" title="Move up" ${i ? "" : "disabled"}>↑</button><button type="button" data-rm="${i}" title="Remove" aria-label="Remove method">${icon("x")}</button></div>
      <div class="f2"><input name="grinder" list="grinders" value="${esc(m.grinder || "")}" placeholder="Grinder">
        <input name="grind" value="${esc(m.grind || "")}" placeholder="Grind setting"></div>
      <div class="f2"><input name="brewer" list="brewers" value="${esc(m.brewer || "")}" placeholder="Brewer / machine">
        <input name="recipe" value="${esc(m.recipe || "")}" placeholder="Technique, e.g. Gold Cup mode"></div></div>`).join("");
    $$("[data-up]").forEach((x) => x.onclick = () => { rows = read(); const i = +x.dataset.up; [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; draw(); });
    $$("[data-rm]").forEach((x) => x.onclick = () => { rows = read(); rows.splice(+x.dataset.rm, 1); draw(); });
  };
  draw();
  $("#addm").onclick = () => { rows = read(); rows.push({ name: "" }); draw(); $$(".mrow input[name=name]").pop().focus(); };
  $("#savem").onclick = async () => {
    try { rows = (await api("methods", { method: "PUT", body: { methods: read() } })).methods; draw(); S.facets = null; toast("Methods saved"); }
    catch (e) { toast(e.message); }
  };
}

/* ---------- boot ---------- */
(async () => {
  if (!ADMIN) { try { S.editor = (await api("/api/me")).editor; } catch { /* offline */ } }
  route();
})();
