"use strict";
// داشبورد لقطات أمازون مصر: بيقرا deals.json وhistory.json اللي البوت بيكتبهم كل تشغيلة

const $ = (id) => document.getElementById(id);
const LOCALE = "ar-EG-u-nu-latn"; // عربي بأرقام إنجليزي زي رسايل تليجرام
const money = (n) => `${Math.round(n).toLocaleString("en-US")} ج.م`;
const SELLERS = { amazon: "أمازون نفسه", fba: "شحن أمازون", third: "بايع خارجي" };
const state = { deals: [], history: null, updated: null };

function ago(iso) {
  const sec = (new Date(iso) - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat("ar", { numeric: "auto" });
  for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (Math.abs(sec) >= size) return rtf.format(Math.round(sec / size), unit);
  }
  return "دلوقتي";
}

function when(iso) {
  return new Intl.DateTimeFormat(LOCALE, {
    timeZone: "Africa/Cairo", day: "numeric", month: "long", hour: "numeric", minute: "2-digit",
  }).format(new Date(iso));
}

// ===== الفلاتر (محفوظة في اللينك عشان تتشارك) =====
function readFilters() {
  return {
    cat: $("f-cat").value,
    score: Number($("f-score").value),
    price: Number($("f-price").value) || 0,
    sort: $("f-sort").value,
  };
}

function syncUrl(f) {
  const q = new URLSearchParams();
  if (f.cat) q.set("cat", f.cat);
  if (f.score) q.set("score", f.score);
  if (f.price) q.set("price", f.price);
  if (f.sort !== "score") q.set("sort", f.sort);
  history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
}

function restoreFilters() {
  const q = new URLSearchParams(location.search);
  if (q.has("score")) $("f-score").value = q.get("score");
  if (q.has("price")) $("f-price").value = q.get("price");
  if (q.has("sort")) $("f-sort").value = q.get("sort");
  return q.get("cat") || "";
}

function fillCategories(selected) {
  const counts = {};
  for (const d of state.deals) counts[d.category] = (counts[d.category] || 0) + 1;
  const sel = $("f-cat");
  for (const name of Object.keys(counts).sort((a, b) => a.localeCompare(b, "ar"))) {
    const opt = new Option(`${name} (${counts[name]})`, name);
    sel.add(opt);
  }
  if (counts[selected]) sel.value = selected;
}

// ===== الكروت =====
function scoreClass(s) { return s >= 75 ? "hi" : s >= 55 ? "mid" : "lo"; }

const EVENT_FLAGS = { fake: "🚨 خصم وهمي", rise: "⚠️ سعره اترفع" };

function card(d) {
  const node = $("card-tpl").content.firstElementChild.cloneNode(true);
  if (d.glitch) node.classList.add("glitch");
  const img = node.querySelector("img");
  if (d.image) img.src = d.image; else img.remove();
  const score = node.querySelector(".score");
  score.textContent = `${d.label} · ${d.score}`;
  score.classList.add(scoreClass(d.score));
  node.querySelector(".glitch-tag").hidden = !d.glitch;
  node.querySelector(".title").textContent = d.title;
  node.querySelector(".title").title = d.title;
  node.querySelector(".now").textContent = money(d.price);
  const was = node.querySelector(".was");
  if (d.list && d.list > d.price) was.textContent = money(d.list); else was.remove();
  const off = node.querySelector(".off");
  if (d.discount) off.textContent = `خصم ${d.discount}%`; else off.remove();

  const facts = [];
  if (d.rating) facts.push(`⭐ ${d.rating}${d.reviews ? ` (${d.reviews.toLocaleString("en-US")})` : ""}`);
  if (d.seller) facts.push(`🏪 ${SELLERS[d.seller] || d.seller}`);
  if (d.coupon) facts.push("🎟️ بكوبون");
  node.querySelector(".facts").textContent = facts.join(" · ");

  const real = node.querySelector(".real");
  if (d.verified) {
    real.textContent = `🎯 الخصم الحقيقي ${d.real}% من أعلى سعر اتسجل`;
    real.classList.add("ok");
  } else {
    real.textContent = "⚠️ لسه مفيش تاريخ سعر كفاية نتأكد بيه من الخصم";
    real.classList.add("warn");
  }
  // 🛍️ الموسم: الخصم الوهمي بعلامة كبيرة، والسعر اللي اترفع قبل الموسم بتحذير
  const flag = EVENT_FLAGS[d.event_flag];
  if (flag) {
    node.classList.add(`event-${d.event_flag}`);
    const tag = node.querySelector(".event-tag");
    tag.textContent = flag;
    tag.hidden = false;
    if (d.event_flag === "fake") {
      real.textContent = "🎯 مفيش خصم حقيقي عن أسعار ما قبل الموسم";
      real.classList.remove("ok");
      real.classList.add("warn");
    }
  }
  if (d.event_note) {
    const note = node.querySelector(".event-note");
    note.textContent = d.event_note;
    note.classList.add(d.event_flag === "fake" ? "fake" : "rise");
    note.hidden = false;
  }
  node.querySelector(".when").textContent = `${d.category} · اتلقت ${ago(d.found)}`;
  node.querySelector(".buy").href = d.url;
  node.querySelector(".kb").href = d.kanbkam;

  const btn = node.querySelector(".chart-btn");
  const box = node.querySelector(".chart");
  btn.addEventListener("click", async () => {
    const open = box.hidden;
    box.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open && !box.childElementCount) await drawChart(box, d);
  });
  return node;
}

function render() {
  const f = readFilters();
  $("f-score-out").textContent = f.score;
  syncUrl(f);
  let list = state.deals.filter((d) =>
    (!f.cat || d.category === f.cat) && d.score >= f.score && (!f.price || d.price <= f.price));
  const by = {
    score: (a, b) => b.score - a.score || b.real - a.real,
    discount: (a, b) => b.real - a.real || b.score - a.score,
    newest: (a, b) => b.found.localeCompare(a.found),
  }[f.sort];
  list = list.sort(by);
  const grid = $("grid");
  grid.replaceChildren(...list.map(card));
  const upd = state.updated ? ` · آخر تحديث ${when(state.updated)} (${ago(state.updated)})` : "";
  $("status").textContent = state.deals.length
    ? `${list.length} لقطة من ${state.deals.length}${upd}`
    : `لسه مفيش لقطات دلوقتي، البوت بيدوّر كل 3 ساعات${upd}`;
}

// ===== رسم تاريخ السعر (SVG من غير مكتبات) =====
async function drawChart(box, d) {
  if (!state.history) {
    box.textContent = "بنحمّل تاريخ السعر…";
    try {
      state.history = await (await fetch("history.json", { cache: "no-cache" })).json();
    } catch (e) {
      box.textContent = "مقدرناش نحمّل تاريخ السعر، جرّب تاني.";
      return;
    }
  }
  const pts = (state.history[d.asin] || []).map(([t, p]) => [new Date(t).getTime(), p]);
  box.replaceChildren();
  if (pts.length < 2) {
    const p = document.createElement("p");
    p.textContent = pts.length
      ? `لسه بنجمع تاريخ السعر: شفناه مرة واحدة بـ ${money(pts[0][1])} (${when(new Date(pts[0][0]).toISOString())}).`
      : "لسه مفيش تاريخ سعر للمنتج ده.";
    box.append(p);
    return;
  }
  const W = 320, H = 140, L = 8, R = 56, T = 12, B = 22;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  if (d.atl) ys.push(d.atl);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 *= 0.95; y1 *= 1.05; }
  const X = (x) => L + ((x - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `تاريخ سعر ${d.title}` });
  const line = pts.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  svg.append(el("polygon", { class: "area", points: `${X(x0)},${H - B} ${line} ${X(x1)},${H - B}` }));
  svg.append(el("polyline", { class: "line", points: line }));
  if (d.atl) {
    svg.append(el("line", { class: "low", x1: L, x2: W - R, y1: Y(d.atl), y2: Y(d.atl) }));
    // جوه الرسمة فوق الخط، عشان ميخبطش في أرقام المحور
    svg.append(el("text", { class: "low-label", x: L + 2, y: Y(d.atl) - 4 },
      `أقل سعر ${Math.round(d.atl).toLocaleString("en-US")}`));
  }
  for (const p of pts) svg.append(el("circle", { class: "dot", cx: X(p[0]), cy: Y(p[1]), r: 2.5 }));
  svg.append(el("text", { class: "axis", x: W - R + 4, y: T + 4 }, Math.round(y1).toLocaleString("en-US")));
  svg.append(el("text", { class: "axis", x: W - R + 4, y: H - B }, Math.round(y0).toLocaleString("en-US")));
  const short = (t) => new Intl.DateTimeFormat(LOCALE, { timeZone: "Africa/Cairo", day: "numeric", month: "short" }).format(t);
  svg.append(el("text", { class: "axis", x: L, y: H - 6 }, short(x0)));
  svg.append(el("text", { class: "axis", x: W - R, y: H - 6, "text-anchor": "end" }, short(x1)));
  box.append(svg);
}

// ===== الثيم =====
function setupTheme() {
  $("theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
  });
}

async function main() {
  setupTheme();
  const cat = restoreFilters();
  for (const id of ["f-cat", "f-score", "f-price", "f-sort"]) $(id).addEventListener("input", render);
  try {
    const data = await (await fetch("deals.json", { cache: "no-cache" })).json();
    state.deals = data.deals || [];
    state.updated = data.updated;
  } catch (e) {
    $("status").textContent = "مقدرناش نحمّل اللقطات دلوقتي، جرّب تعمل refresh.";
    return;
  }
  fillCategories(cat);
  render();
}

main();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
