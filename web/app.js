// UI: 1) параметры+файлы → 2) редактируемые позиции → 3) сборка.
const $ = (id) => document.getElementById(id);
const engineUrl = () => $("engine").value.replace(/\/$/, "");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const specFiles = [];
const letterFiles = [];
let positions = [];        // модель таблицы позиций

$("fixDate").valueAsDate = new Date();

/* ---------- загрузка файлов ---------- */
function wireDrop(dropId, inputId, store, listId) {
  const drop = $(dropId), input = $(inputId);
  drop.onclick = () => input.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over");
    addFiles([...e.dataTransfer.files], store, listId); };
  input.onchange = () => addFiles([...input.files], store, listId);
}
function addFiles(files, store, listId) {
  for (const f of files) if (f.type === "application/pdf") store.push(f);
  renderFiles(store, listId);
}
function renderFiles(store, listId) {
  $(listId).innerHTML = store.map((f, i) =>
    `<li>📄 ${esc(f.name)} <a href="#" data-i="${i}" data-list="${listId}" class="rm link">✕</a></li>`).join("");
}
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("rm")) {
    e.preventDefault();
    const store = e.target.dataset.list === "specFiles" ? specFiles : letterFiles;
    store.splice(+e.target.dataset.i, 1);
    renderFiles(store, e.target.dataset.list);
  }
});
wireDrop("dropSpec", "specInput", specFiles, "specFiles");
wireDrop("dropLetter", "letterInput", letterFiles, "letterFiles");

/* ---------- проверка движка ---------- */
async function checkEngine() {
  const el = $("engineStatus");
  try {
    const h = await (await fetch(engineUrl() + "/health")).json();
    el.innerHTML = `движок на связи · НДС ${h.vat_rate}% · ист.≥${h.min_sources} · `
      + (h.anthropic_key ? "Claude ✓" : "Claude ✗") + " · "
      + (h.dadata_token ? "DaData ✓" : "DaData ✗");
    el.style.color = "var(--ok)";
    $("reqHint").textContent = h.anthropic_key ? "" : "для распознавания нужен ANTHROPIC_API_KEY";
    window._engineHealth = h;
  } catch {
    el.textContent = "движок недоступен — запустите engine (run.cmd / run.sh)";
    el.style.color = "var(--err)";
    window._engineHealth = null;
  }
}
$("engine").onchange = checkEngine;
checkEngine();

/* ---------- шаг 1 → 2 ---------- */
$("parseBtn").onclick = async () => {
  if (!specFiles.length && !letterFiles.length)
    return alert("Добавьте файлы спецификации или письма");
  const fd = new FormData();
  fd.append("object_name", $("objectName").value.trim());
  fd.append("discipline", $("discipline").value);
  fd.append("unique_names", $("uniqueNames").value);
  specFiles.forEach((f) => fd.append("specs", f));
  letterFiles.forEach((f) => fd.append("letters", f));

  $("parseBtn").disabled = true; $("parseBtn").textContent = "Распознаём…";
  try {
    const r = await fetch(engineUrl() + "/parse", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    positions = data.positions.map(normalizePos);
    renderPositions();
  } catch (e) {
    alert("Не удалось распознать: " + e.message + "\nМожно заполнить вручную.");
  } finally {
    $("parseBtn").disabled = false; $("parseBtn").textContent = "Распознать из файлов →";
  }
};

$("manualBtn").onclick = () => {
  if (!positions.length) addPosition();
  renderPositions();
};
$("addPosBtn").onclick = () => { addPosition(); renderPositions(); };
$("bulkBtn").onclick = bulkSearch;

function normalizePos(p) {
  return {
    number: p.number, name: p.name || "", unit: p.unit || "шт", qty: p.qty || 0,
    is_unique: !!p.is_unique, discipline: p.discipline || $("discipline").value,
    type_mark: p.type_mark || "", full_name: p.full_name || "",
    offers: (p.offers || []).map((o) => ({
      price_with_vat: o.price_with_vat || 0, org_name: o.org_name || "", inn: o.inn || "",
      kpp: o.kpp || "", url: o.url || "", city: o.city || "Москва",
      status: o.status || 2, from_letter: !!o.from_letter,
      screenshot_product: o.screenshot_product || "", screenshot_requisites: o.screenshot_requisites || "",
      _busy: false,
    })),
    tsn_base_price: p.tsn_base_price ?? null, tsn_coefficient: p.tsn_coefficient ?? null,
    _open: false, _searched: false, _found: 0, _target: 0,
  };
}

const MOSCOW_RE = /москв|московск|мо\b|подмосков/i;
const isMoscow = (city) => !city || MOSCOW_RE.test(city);
function addPosition() {
  positions.push(normalizePos({ number: positions.length + 1, name: "", _open: true }));
  positions[positions.length - 1]._open = true;
}

/* ---------- рендер таблицы позиций ---------- */
function renderPositions() {
  $("posCard").style.display = positions.length ? "block" : "none";
  $("buildCard").style.display = positions.length ? "block" : "none";
  $("posCount").textContent = positions.length ? `${positions.length} позиц.` : "";
  $("posList").innerHTML = positions.map(posHtml).join("");
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
      <td><input type="text" value="${esc(o.city)}" data-p="${i}" data-o="${j}" data-f="city" style="width:96px" title="${badRegion ? "склад не в Москве/МО — цена не подходит" : "город склада"}" placeholder="город"></td>
      <td><input type="text" value="${esc(o.url)}" data-p="${i}" data-o="${j}" data-f="url"></td>
      <td>${thumbsHtml(o, eng)}</td>
      <td><button class="ghost sm capture" data-p="${i}" data-o="${j}" title="снять скриншот карточки + реквизитов по URL">${o._busy ? '<span class="spin"></span>' : "📷"}</button></td>
      <td><input type="checkbox" ${o.from_letter ? "checked" : ""} data-p="${i}" data-o="${j}" data-f="from_letter" title="из письма (не в ТКП)"></td>
      <td><a href="#" class="rmoffer link" data-p="${i}" data-o="${j}">✕</a></td>
    </tr>`;
  }).join("");

  const badge = p._searched
    ? `<span class="pill" style="border-color:${p._found >= p._target ? "var(--ok)" : "var(--warn)"};color:${p._found >= p._target ? "var(--ok)" : "var(--warn)"}">найдено ${p._found}/${p._target}${p._found < p._target ? " ⚠" : ""}</span>`
    : "";
  return `
  <div class="pos ${p.is_unique ? "unique" : ""}">
    <div class="pos-head">
      <input class="num" type="number" value="${p.number}" data-p="${i}" data-f="number" title="№">
      <input class="nm" type="text" value="${esc(p.name)}" data-p="${i}" data-f="name" placeholder="Наименование">
      <input class="u" type="text" value="${esc(p.unit)}" data-p="${i}" data-f="unit" title="ед.">
      <input class="q" type="number" step="0.001" value="${p.qty || ""}" data-p="${i}" data-f="qty" title="кол-во">
      <label class="muted" style="font-weight:500"><input type="checkbox" ${p.is_unique ? "checked" : ""} data-p="${i}" data-f="is_unique"> уник.</label>
      ${p.is_unique ? "" : `<button class="ghost sm search" data-p="${i}" title="найти цены онлайн (≥12 источников → 3 макс.)">${p._searching ? '<span class="spin"></span> ищу…' : "🔎 найти цены"}</button>`}
      ${badge}
      <span class="toggle" data-toggle="${i}">${p._open ? "свернуть ▲" : "цены ▼ (" + p.offers.length + ")"}</span>
      <a href="#" class="rmpos link" data-p="${i}" title="удалить позицию">🗑</a>
    </div>
    <div class="pos-body ${p._open ? "open" : ""}">
      <table class="offers">
        <thead><tr><th>Цена с НДС</th><th>Поставщик</th><th>ИНН</th><th>Город склада</th><th>Ссылка</th><th>Скриншоты</th><th></th><th>письмо</th><th></th></tr></thead>
        <tbody>${offers || `<tr><td colspan="9" class="muted">нет цен — добавьте или нажмите «найти цены»</td></tr>`}</tbody>
      </table>
      <button class="ghost sm addoffer" data-p="${i}" style="margin-top:8px">+ цена</button>
      <div class="tsn-row">
        <div><label>Базовая расценка ТСН-2001</label>
          <input type="number" step="0.01" value="${p.tsn_base_price ?? ""}" data-p="${i}" data-f="tsn_base_price" placeholder="по mos.ru если пусто"></div>
        <div><label>Коэффициент пересчёта</label>
          <input type="number" step="0.01" value="${p.tsn_coefficient ?? ""}" data-p="${i}" data-f="tsn_coefficient"></div>
      </div>
    </div>
  </div>`;
}

/* делегирование событий таблицы */
$("posList").addEventListener("input", (e) => {
  const t = e.target, pi = +t.dataset.p;
  if (t.dataset.p === undefined) return;
  const f = t.dataset.f;
  let val = t.type === "checkbox" ? t.checked : t.value;
  if (t.dataset.o !== undefined) {           // поле оффера
    const oi = +t.dataset.o;
    if (["price_with_vat"].includes(f)) val = parseFloat(val) || 0;
    positions[pi].offers[oi][f] = val;
  } else if (f) {                            // поле позиции
    if (f === "qty") val = parseFloat(val) || 0;
    else if (f === "number") val = parseInt(val) || pi + 1;
    else if (["tsn_base_price", "tsn_coefficient"].includes(f)) val = val === "" ? null : parseFloat(val);
    positions[pi][f] = val;
    if (f === "is_unique") renderPositions();
  }
});
$("posList").addEventListener("click", (e) => {
  const t = e.target.closest("[data-toggle],.addoffer,.rmoffer,.rmpos,.capture,.search,.zoom") || e.target;
  if (t.dataset.toggle !== undefined) { const i = +t.dataset.toggle; positions[i]._open = !positions[i]._open; renderPositions(); }
  else if (t.classList.contains("addoffer")) { positions[+t.dataset.p].offers.push({ price_with_vat: 0, org_name: "", inn: "", kpp: "", url: "", city: "Москва", status: 2, from_letter: false, screenshot_product: "", screenshot_requisites: "" }); renderPositions(); }
  else if (t.classList.contains("rmoffer")) { e.preventDefault(); positions[+t.dataset.p].offers.splice(+t.dataset.o, 1); renderPositions(); }
  else if (t.classList.contains("rmpos")) { e.preventDefault(); positions.splice(+t.dataset.p, 1); renderPositions(); }
  else if (t.classList.contains("capture")) { captureOffer(+t.dataset.p, +t.dataset.o); }
  else if (t.classList.contains("search")) { searchPosition(+t.dataset.p); }
  else if (t.classList.contains("zoom")) { openLightbox(t.src); }
});

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
    const ex = d.extracted || {};
    if (ex.price_with_vat && !o.price_with_vat) o.price_with_vat = ex.price_with_vat;
    if (ex.seller_inn && !o.inn) o.inn = String(ex.seller_inn);
    if (ex.product_title) o.product_title = ex.product_title;
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
    p._searched = true; p._found = d.found ?? p.offers.length; p._target = d.target ?? 12;
    p._open = true;
    return d;
  } catch (e) {
    if (!window._bulk) alert("Поиск не удался: " + e.message);
    throw e;
  } finally {
    p._searching = false; renderPositions();
  }
}

async function bulkSearch() {
  const idx = positions.map((p, i) => (!p.is_unique ? i : -1)).filter((i) => i >= 0);
  if (!idx.length) return alert("Нет неуникальных позиций для поиска");
  window._bulk = true;
  const btn = $("bulkBtn"); btn.disabled = true;
  let done = 0, fails = 0;
  for (const i of idx) {
    btn.textContent = `🔎 Поиск ${done + 1}/${idx.length}…`;
    try { await searchPosition(i); } catch { fails++; }
    done++;
  }
  window._bulk = false;
  btn.disabled = false; btn.textContent = "🔎 Найти цены по всем позициям";
  const weak = positions.filter((p) => p._searched && p._found < p._target).length;
  alert(`Поиск завершён: ${idx.length} позиц.` +
    (fails ? `, ошибок: ${fails}` : "") +
    (weak ? `\nМало источников (<цель) у ${weak} позиц. — проверьте вручную.` : ""));
}

function openLightbox(src) { $("lightboxImg").src = src; $("lightbox").classList.add("open"); }
$("lightbox").onclick = () => $("lightbox").classList.remove("open");

/* ---------- шаг 3: сборка ---------- */
$("buildBtn").onclick = async () => {
  if (!$("objectName").value.trim()) return alert("Укажите наименование объекта (шаг 1)");
  if (!positions.length) return alert("Нет позиций");
  const body = {
    object_name: $("objectName").value.trim(),
    fix_date: $("fixDate").value ? $("fixDate").value.split("-").reverse().join(".") : "",
    use_tsn: $("useTsn").checked,
    skip_search: !$("searchOnline").checked,
    positions: positions.map((p) => ({
      number: p.number, name: p.name, full_name: p.full_name, type_mark: p.type_mark,
      unit: p.unit, qty: p.qty, is_unique: p.is_unique, discipline: p.discipline,
      tsn_base_price: p.tsn_base_price, tsn_coefficient: p.tsn_coefficient,
      offers: p.offers,
    })),
  };
  $("buildBtn").disabled = true;
  $("progressWrap").style.display = "block";
  $("downloads").innerHTML = "";
  setStatus("running", "Запуск…");
  try {
    const r = await fetch(engineUrl() + "/build", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    poll((await r.json()).job_id);
  } catch (e) {
    setStatus("error", "Ошибка: " + e.message);
    $("buildBtn").disabled = false;
  }
};

const LABELS = { kac: "КАЦ (Excel)", tkp: "Том ТКП (PDF)", tsn: "ТСН-2001 (Excel)" };
async function poll(jobId) {
  try {
    const j = await (await fetch(`${engineUrl()}/jobs/${jobId}`)).json();
    $("barFill").style.width = Math.round((j.progress || 0) * 100) + "%";
    $("stepText").textContent = j.step || "";
    $("logBox").textContent = (j.log || []).join("\n");
    $("logBox").scrollTop = $("logBox").scrollHeight;
    if (j.status === "done") {
      setStatus("done", "Готово ✓");
      $("downloads").innerHTML = (j.outputs || []).map((k) =>
        `<a href="${engineUrl()}/jobs/${jobId}/download/${k}">⬇ ${LABELS[k] || k}</a>`).join("");
      $("buildBtn").disabled = false; return;
    }
    if (j.status === "error") {
      setStatus("error", "Ошибка пайплайна");
      $("logBox").textContent += "\n\n" + (j.error || "");
      $("buildBtn").disabled = false; return;
    }
    setStatus("running", "Обработка…");
    setTimeout(() => poll(jobId), 1500);
  } catch {
    setStatus("error", "Потеряна связь с движком");
    $("buildBtn").disabled = false;
  }
}
function setStatus(cls, text) { const el = $("statusText"); el.className = "status " + cls; el.textContent = text; }
