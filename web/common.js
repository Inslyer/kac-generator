// Общая машинерия для обеих страниц «Генрих» (Сметчик и База-менеджер).
// Поведение настраивается через window.POS = { showSearch, showTsn, baseBadge }.
window.POS = Object.assign({ showSearch: false, showTsn: false, baseBadge: false }, window.POS || {});

const $ = (id) => document.getElementById(id);
const engineUrl = () => ($("engine") ? $("engine").value.replace(/\/$/, "") : location.origin);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let positions = [];                 // модель таблицы позиций (общая)
const MOSCOW_RE = /москв|московск|мо\b|подмосков/i;
const isMoscow = (city) => !city || MOSCOW_RE.test(city);

// Если открыто прямо из движка — тот же origin (без блокировки http-localhost)
if ($("engine") && location.protocol === "http:" && /^(127\.0\.0\.1|localhost)/.test(location.host)) {
  $("engine").value = location.origin;
}

/* ---------- загрузка файлов ---------- */
const _stores = {};                 // listId → массив файлов
function wireDrop(dropId, inputId, store, listId, exts) {
  _stores[listId] = store;
  const drop = $(dropId), input = $(inputId);
  if (!drop || !input) return;
  drop.onclick = () => input.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over");
    addFiles([...e.dataTransfer.files], store, listId, exts); };
  input.onchange = () => addFiles([...input.files], store, listId, exts);
}
function addFiles(files, store, listId, exts) {
  for (const f of files)
    if (exts.some((x) => f.name.toLowerCase().endsWith(x))) store.push(f);
  renderFiles(store, listId);
}
function renderFiles(store, listId) {
  $(listId).innerHTML = store.map((f, i) =>
    `<li>📄 ${esc(f.name)} <a href="#" data-i="${i}" data-list="${listId}" class="rm link">✕</a></li>`).join("");
}
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("rm")) {
    e.preventDefault();
    const lid = e.target.dataset.list, store = _stores[lid];
    if (store) { store.splice(+e.target.dataset.i, 1); renderFiles(store, lid); }
  }
});

/* ---------- проверка движка ---------- */
async function checkEngine() {
  const el = $("engineStatus");
  try {
    const h = await (await fetch(engineUrl() + "/health")).json();
    const llmName = (h.llm_provider === "deepseek") ? "DeepSeek" : "Claude";
    const llmOk = (h.llm_key ?? h.anthropic_key);
    if (el) {
      el.innerHTML = `движок на связи · НДС ${h.vat_rate}% · ист.≥${h.min_sources} · `
        + (llmOk ? `${llmName} ✓` : `${llmName} ✗`) + " · "
        + (h.ocr_available ? "OCR ✓" : "OCR ✗") + " · "
        + (h.dadata_token ? "DaData ✓" : "DaData ✗");
      el.style.color = "var(--ok)";
    }
    if ($("reqHint")) $("reqHint").textContent = llmOk ? ""
      : `нужен ключ ${h.llm_provider === "deepseek" ? "DEEPSEEK_API_KEY" : "ANTHROPIC_API_KEY"}`;
    const bh = $("bulkHint");
    if (bh) bh.textContent = `самовывоз Москва/МО, ≥${h.min_sources} источников → 3 максимальные`;
    window._engineHealth = h;
  } catch {
    if (el) { el.textContent = "движок недоступен — запустите engine (start.command)"; el.style.color = "var(--err)"; }
    window._engineHealth = null;
  }
}
if ($("engine")) { $("engine").onchange = checkEngine; checkEngine(); }

/* ---------- модель позиций ---------- */
function normalizePos(p) {
  return {
    number: p.number, name: p.name || "", unit: p.unit || "шт", qty: p.qty || 0,
    is_unique: !!p.is_unique, discipline: p.discipline || ($("discipline") ? $("discipline").value : "ЭМ"),
    type_mark: p.type_mark || "", full_name: p.full_name || "",
    offers: (p.offers || []).map((o) => ({
      price_with_vat: o.price_with_vat || 0, org_name: o.org_name || "", inn: o.inn || "",
      kpp: o.kpp || "", url: o.url || "", city: o.city || "Москва",
      status: o.status || 2, from_letter: !!o.from_letter,
      screenshot_product: o.screenshot_product || "", screenshot_requisites: o.screenshot_requisites || "",
      _busy: false,
    })),
    tsn_base_price: p.tsn_base_price ?? null, tsn_coefficient: p.tsn_coefficient ?? null,
    _candidates: p._candidates || null, _in_base: p._in_base,
    _open: false, _searched: false, _found: 0, _target: 0,
  };
}
function addPosition() {
  positions.push(normalizePos({ number: positions.length + 1, name: "", _open: true }));
  positions[positions.length - 1]._open = true;
}

/* ---------- рендер таблицы позиций ---------- */
function renderPositions() {
  if ($("posCard")) $("posCard").style.display = positions.length ? "block" : "none";
  if ($("buildCard")) $("buildCard").style.display = positions.length ? "block" : "none";
  if ($("posCount")) $("posCount").textContent = positions.length ? `${positions.length} позиц.` : "";
  $("posList").innerHTML = positions.map(posHtml).join("");
  if (typeof onPositionsRendered === "function") onPositionsRendered();
}
function thumbsHtml(o, eng) {
  const cell = (name, label) => name
    ? `<img src="${eng}/cache/${esc(name)}" alt="${label}" title="${label}" class="zoom">`
    : `<span class="empty" title="нет скриншота"></span>`;
  return `<div class="thumbs">${cell(o.screenshot_product, "карточка")}${cell(o.screenshot_requisites, "реквизиты")}</div>`;
}
function posHtml(p, i) {
  const eng = engineUrl();
  const offers = p.offers.map((o, j) => {
    const badRegion = !o.from_letter && !isMoscow(o.city);
    return `
    <tr${badRegion ? ' style="background:rgba(210,153,34,.12)"' : ""}>
      <td><input type="number" step="0.01" value="${o.price_with_vat || ""}" data-p="${i}" data-o="${j}" data-f="price_with_vat" style="width:108px"></td>
      <td><input type="text" value="${esc(o.org_name)}" data-p="${i}" data-o="${j}" data-f="org_name"></td>
      <td><input type="text" value="${esc(o.inn)}" data-p="${i}" data-o="${j}" data-f="inn" style="width:102px"></td>
      <td><input type="text" value="${esc(o.city)}" data-p="${i}" data-o="${j}" data-f="city" style="width:96px" title="${badRegion ? "склад не в Москве/МО" : "город склада"}" placeholder="город"></td>
      <td><input type="text" value="${esc(o.url)}" data-p="${i}" data-o="${j}" data-f="url"></td>
      <td>${thumbsHtml(o, eng)}</td>
      <td><button class="ghost sm capture" data-p="${i}" data-o="${j}" title="снять скриншот по URL">${o._busy ? '<span class="spin"></span>' : "📷"}</button></td>
      ${window.POS.showSearch ? `<td><button class="ghost sm repl" data-p="${i}" data-o="${j}" title="заменить следующим кандидатом по цене">⤵</button></td>` : ""}
      <td><input type="checkbox" ${o.from_letter ? "checked" : ""} data-p="${i}" data-o="${j}" data-f="from_letter" title="из письма (не в ТКП)"></td>
      <td><a href="#" class="rmoffer link" data-p="${i}" data-o="${j}">✕</a></td>
    </tr>`;
  }).join("");
  const cols = window.POS.showSearch ? 10 : 9;
  const searchBtn = (window.POS.showSearch && !p.is_unique)
    ? `<button class="ghost sm search" data-p="${i}" title="найти цены онлайн">${p._searching ? '<span class="spin"></span> ищу…' : "🔎 найти цены"}</button>` : "";
  const badge = window.POS.showSearch && p._searched
    ? `<span class="pill" style="border-color:${p._found >= p._target ? "var(--ok)" : "var(--warn)"};color:${p._found >= p._target ? "var(--ok)" : "var(--warn)"}">найдено ${p._found}/${p._target}${p._found < p._target ? " ⚠" : ""}</span>` : "";
  const baseBadge = window.POS.baseBadge
    ? (p._in_base === false ? `<span class="badge-missing" title="позиции нет в базе цен">нет в базе</span>`
      : p._in_base === true ? `<span class="badge-base">из базы</span>` : "") : "";
  const tsn = window.POS.showTsn ? `
      <div class="tsn-row">
        <div><label>Базовая расценка ТСН-2001</label>
          <input type="number" step="0.01" value="${p.tsn_base_price ?? ""}" data-p="${i}" data-f="tsn_base_price" placeholder="из справочника если пусто"></div>
        <div><label>Коэффициент пересчёта</label>
          <input type="number" step="0.01" value="${p.tsn_coefficient ?? ""}" data-p="${i}" data-f="tsn_coefficient"></div>
      </div>` : "";
  return `
  <div class="pos ${p.is_unique ? "unique" : ""} ${p._in_base === false ? "missing" : ""}">
    <div class="pos-head">
      <span class="posnum" title="порядковый номер позиции">№${i + 1}</span>
      <input class="num" type="number" value="${p.number}" data-p="${i}" data-f="number" title="№ п.п. (графа А в КАЦ)">
      <input class="nm" type="text" value="${esc(p.name)}" data-p="${i}" data-f="name" placeholder="Наименование">
      <input class="u" type="text" value="${esc(p.unit)}" data-p="${i}" data-f="unit" title="ед.">
      <input class="q" type="number" step="0.001" value="${p.qty || ""}" data-p="${i}" data-f="qty" title="кол-во">
      <label class="muted" style="font-weight:500"><input type="checkbox" ${p.is_unique ? "checked" : ""} data-p="${i}" data-f="is_unique"> уник.</label>
      ${searchBtn}${badge}${baseBadge}
      <span class="toggle" data-toggle="${i}">${p._open ? "свернуть ▲" : "цены ▼ (" + p.offers.length + ")"}</span>
      <a href="#" class="rmpos link" data-p="${i}" title="удалить позицию">🗑</a>
    </div>
    <div class="pos-body ${p._open ? "open" : ""}">
      <table class="offers">
        <thead><tr><th>Цена с НДС</th><th>Поставщик</th><th>ИНН</th><th>Город склада</th><th>Ссылка</th><th>Скриншоты</th><th></th>${window.POS.showSearch ? "<th></th>" : ""}<th>письмо</th><th></th></tr></thead>
        <tbody>${offers || `<tr><td colspan="${cols}" class="muted">нет цен — добавьте или нажмите «найти цены»</td></tr>`}</tbody>
      </table>
      <button class="ghost sm addoffer" data-p="${i}" style="margin-top:8px">+ цена</button>
      ${tsn}
    </div>
  </div>`;
}

/* ---------- события таблицы ---------- */
function _bindPosList() {
  const list = $("posList");
  if (!list) return;
  list.addEventListener("input", (e) => {
    const t = e.target, pi = +t.dataset.p;
    if (t.dataset.p === undefined) return;
    const f = t.dataset.f;
    let val = t.type === "checkbox" ? t.checked : t.value;
    if (t.dataset.o !== undefined) {
      const oi = +t.dataset.o;
      if (["price_with_vat"].includes(f)) val = parseFloat(val) || 0;
      positions[pi].offers[oi][f] = val;
    } else if (f) {
      if (f === "qty") val = parseFloat(val) || 0;
      else if (f === "number") val = parseInt(val) || pi + 1;
      else if (["tsn_base_price", "tsn_coefficient"].includes(f)) val = val === "" ? null : parseFloat(val);
      positions[pi][f] = val;
      if (f === "is_unique") renderPositions();
    }
  });
  list.addEventListener("click", (e) => {
    const t = e.target.closest("[data-toggle],.addoffer,.rmoffer,.rmpos,.capture,.repl,.search,.zoom") || e.target;
    if (t.dataset.toggle !== undefined) { const i = +t.dataset.toggle; positions[i]._open = !positions[i]._open; renderPositions(); }
    else if (t.classList.contains("addoffer")) { positions[+t.dataset.p].offers.push({ price_with_vat: 0, org_name: "", inn: "", kpp: "", url: "", city: "Москва", status: 2, from_letter: false, screenshot_product: "", screenshot_requisites: "" }); renderPositions(); }
    else if (t.classList.contains("rmoffer")) { e.preventDefault(); positions[+t.dataset.p].offers.splice(+t.dataset.o, 1); renderPositions(); }
    else if (t.classList.contains("rmpos")) { e.preventDefault(); positions.splice(+t.dataset.p, 1); renderPositions(); }
    else if (t.classList.contains("capture")) { captureOffer(+t.dataset.p, +t.dataset.o); }
    else if (t.classList.contains("repl")) { replaceOffer(+t.dataset.p, +t.dataset.o); }
    else if (t.classList.contains("search")) { searchPosition(+t.dataset.p); }
    else if (t.classList.contains("zoom")) { openLightbox(t.src); }
  });
}

function replaceOffer(pi, oi) {
  const p = positions[pi];
  const cands = p._candidates || [];
  const usedUrls = new Set(p.offers.map((o) => o.url).filter(Boolean));
  const next = cands.filter((c) => c.url && !usedUrls.has(c.url))
    .sort((a, b) => (b.price_with_vat || 0) - (a.price_with_vat || 0))[0];
  if (!next) return alert("Нет других кандидатов для замены (запустите «найти цены»)");
  p.offers[oi] = { ...next, _busy: false };
  renderPositions();
}

async function captureOffer(pi, oi) {
  const o = positions[pi].offers[oi];
  if (!o.url) return alert("Укажите ссылку на карточку товара");
  o._busy = true; renderPositions();
  try {
    const r = await fetch(engineUrl() + "/capture", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: o.url, inn: o.inn }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    o.screenshot_product = d.screenshot_product || o.screenshot_product;
    o.screenshot_requisites = d.screenshot_requisites || o.screenshot_requisites;
    const ex = d.extracted || {}, rq = d.requisites || {};
    if (ex.price_with_vat && !o.price_with_vat) o.price_with_vat = ex.price_with_vat;
    if (ex.product_title) o.product_title = ex.product_title;
    if (rq.inn && !o.inn) o.inn = String(rq.inn);
    else if (ex.seller_inn && !o.inn) o.inn = String(ex.seller_inn);
    if (rq.name && !o.org_name) o.org_name = rq.name;
    if (rq.kpp && !o.kpp) o.kpp = rq.kpp;
    if (rq.city && (!o.city || o.city === "Москва")) o.city = rq.city;
  } catch (e) {
    alert("Не удалось снять скриншот: " + e.message);
  } finally {
    o._busy = false; renderPositions();
  }
}

async function searchPosition(pi) {
  const p = positions[pi];
  if (!p.name.trim()) return alert("Укажите наименование позиции");
  p._searching = true; renderPositions();
  try {
    const r = await fetch(engineUrl() + "/search_position", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: p.name, type_mark: p.type_mark, discipline: p.discipline }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    p.offers = (d.offers || []).map((o) => ({ ...o, _busy: false }));
    p._candidates = d.candidates || d.offers || [];
    p._searched = true; p._found = d.found ?? p.offers.length;
    p._target = d.target ?? window._engineHealth?.min_sources ?? 5;
    p._open = true;
    return d;
  } catch (e) {
    if (!window._bulk) alert("Поиск не удался: " + e.message);
    throw e;
  } finally {
    p._searching = false; renderPositions();
  }
}

async function bulkSearch(btnId) {
  const idx = positions.map((p, i) => (!p.is_unique ? i : -1)).filter((i) => i >= 0);
  if (!idx.length) return alert("Нет неуникальных позиций для поиска");
  window._bulk = true;
  const btn = $(btnId); if (btn) btn.disabled = true;
  let done = 0, fails = 0;
  for (const i of idx) {
    if (btn) btn.textContent = `🔎 Поиск ${done + 1}/${idx.length}…`;
    try { await searchPosition(i); } catch { fails++; }
    done++;
  }
  window._bulk = false;
  if (btn) { btn.disabled = false; btn.textContent = "🔎 Найти цены по всем позициям"; }
  if (fails) alert(`Поиск завершён. Не удалось: ${fails} из ${idx.length}.`);
}

/* ---------- lightbox ---------- */
function openLightbox(src) { const lb = $("lightbox"); if (lb) { $("lightboxImg").src = src; lb.classList.add("open"); } }
if ($("lightbox")) $("lightbox").addEventListener("click", () => $("lightbox").classList.remove("open"));

/* ---------- джоб-прогресс ---------- */
async function pollJob(jobId, { bar, step, log, onDone, onError }) {
  try {
    const j = await (await fetch(`${engineUrl()}/jobs/${jobId}`)).json();
    if (bar && $(bar)) $(bar).style.width = Math.round((j.progress || 0) * 100) + "%";
    if (step && $(step)) $(step).textContent = j.step || (j.log || []).slice(-1)[0] || "обработка…";
    if (log && $(log)) { $(log).textContent = (j.log || []).join("\n"); $(log).scrollTop = $(log).scrollHeight; }
    if (j.status === "done") { onDone && onDone(j); return; }
    if (j.status === "error") { onError && onError(j); return; }
    setTimeout(() => pollJob(jobId, { bar, step, log, onDone, onError }), 1500);
  } catch { onError && onError({ error: "Потеряна связь с движком" }); }
}

_bindPosList();
