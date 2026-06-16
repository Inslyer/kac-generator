// База-менеджер: импорт списка из Excel, ручное добавление/правка позиций, поддержание базы.
// Общая машинерия (helpers, рендер позиций с поиском/заменой, lightbox, poll) — в common.js.

const importFiles = [];
wireDrop("dropImport", "importInput", importFiles, "importFiles", [".xlsx", ".xlsm", ".xls"]);

/* ---------- импорт из Excel ---------- */
$("importBtn").onclick = async () => {
  if (!importFiles.length) return alert("Добавьте .xlsx со списком позиций");
  const fd = new FormData();
  fd.append("file", importFiles[0]);
  fd.append("discipline", $("discipline").value);
  $("importBtn").disabled = true;
  $("importProgress").style.display = "block";
  $("importHint").textContent = "";
  try {
    const r = await fetch(engineUrl() + "/base/import", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    $("importHint").textContent = `позиций в файле: ${d.positions}`;
    pollJob(d.job_id, {
      bar: "importBar", step: "importStep",
      onDone: () => { $("importBtn").disabled = false; $("importStep").textContent = "Готово ✓"; loadBaseStatus(); },
      onError: (j) => { $("importBtn").disabled = false; $("importStep").textContent = "Ошибка: " + (j.error || ""); },
    });
  } catch (e) {
    $("importBtn").disabled = false; $("importStep").textContent = "Ошибка: " + e.message;
  }
};

/* ---------- рабочая область позиций ---------- */
$("addManualBtn").onclick = () => { addPosition(); renderPositions(); };
$("bulkBtn").onclick = () => bulkSearch("bulkBtn");

// «Найти и добавить»: создаёт позицию из полей и сразу ищет (searchPosition сам сохраняет в базу)
$("addSearchBtn").onclick = async () => {
  const name = $("addName").value.trim();
  if (!name) return alert("Укажите наименование");
  positions.push(normalizePos({ number: positions.length + 1, name, type_mark: $("addMark").value.trim(), _open: true }));
  renderPositions();
  $("addName").value = ""; $("addMark").value = "";
  await searchPosition(positions.length - 1);
  loadBaseStatus();
};

// «Сохранить всё в базу»: upsert отредактированных блоков (без повторного поиска)
$("saveAllBtn").onclick = async () => {
  const withOffers = positions.filter((p) => p.offers.length);
  if (!withOffers.length) return alert("Нет позиций с ценами для сохранения");
  try {
    const r = await fetch(engineUrl() + "/base/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ positions: withOffers.map((p) => ({
        number: p.number, name: p.name, type_mark: p.type_mark, unit: p.unit,
        qty: p.qty, discipline: p.discipline, offers: p.offers,
      })) }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    alert(`Сохранено в базу: ${d.saved} позиций.`);
    loadBaseStatus();
  } catch (e) { alert("Не сохранено: " + e.message); }
};

/* ---------- база цен ---------- */
$("baseReloadBtn").onclick = loadBaseStatus;
$("baseRefreshBtn").onclick = async () => {
  if (!confirm("Переискать цены по ВСЕЙ базе? Может занять время.")) return;
  $("baseRefreshBtn").disabled = true; $("baseProgress").style.display = "block";
  try {
    const r = await fetch(engineUrl() + "/base/refresh", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    pollJob((await r.json()).job_id, {
      bar: "baseBar", step: "baseStep",
      onDone: () => { $("baseRefreshBtn").disabled = false; $("baseStep").textContent = "Готово ✓"; loadBaseStatus(); },
      onError: (j) => { $("baseRefreshBtn").disabled = false; $("baseStep").textContent = "Ошибка: " + (j.error || ""); },
    });
  } catch (e) { $("baseRefreshBtn").disabled = false; $("baseStep").textContent = "Ошибка: " + e.message; }
};

async function loadBaseStatus() {
  try {
    const d = await (await fetch(engineUrl() + "/base")).json();
    const when = d.last_refresh ? d.last_refresh.split("-").reverse().join(".") : "ещё не обновлялась";
    const mb = (d.disk_bytes || 0) / 1048576;
    const size = mb >= 1 ? mb.toFixed(1) + " МБ" : Math.round((d.disk_bytes || 0) / 1024) + " КБ";
    $("baseStatus").textContent = `база: ${d.count} позиций · ${size} · обновлено ${when}`;
    window._baseEntries = d.entries || [];
    renderBaseList();
  } catch { $("baseStatus").textContent = "база: движок недоступен"; }
}
function renderBaseList() {
  const items = window._baseEntries || [];
  $("baseList").innerHTML = items.length
    ? items.map((e) => `<div class="brow">
        <span style="flex:1">${esc(e.name)} ${e.type_mark ? `<span class="muted">(${esc(e.type_mark)})</span>` : ""}</span>
        <span class="muted" style="width:80px;text-align:right">${e.offers_count} цен</span>
        <span class="muted" style="width:110px;text-align:right">${e.max_price ? Math.round(e.max_price) + " ₽" : ""}</span>
        <a href="#" class="link baseedit" data-key="${esc(e.key)}" title="загрузить в рабочую область для правки">✏</a>
        <a href="#" class="link basedel" data-key="${esc(e.key)}" title="удалить из базы">✕</a></div>`).join("")
    : `<div class="hint">база пуста</div>`;
}
document.addEventListener("click", async (e) => {
  if (e.target.classList.contains("basedel")) {
    e.preventDefault();
    if (!confirm("Удалить позицию из базы?")) return;
    await fetch(engineUrl() + "/base/" + encodeURIComponent(e.target.dataset.key), { method: "DELETE" });
    loadBaseStatus();
  } else if (e.target.classList.contains("baseedit")) {
    e.preventDefault();
    const entry = (window._baseEntries || []).find((x) => x.key === e.target.dataset.key);
    if (entry) loadEntryToWorkspace(entry);
  }
});

// Загружает блок из базы в рабочую область для правки (офферы тянем через /base/fill)
async function loadEntryToWorkspace(entry) {
  try {
    const r = await fetch(engineUrl() + "/base/fill", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ positions: [{
        number: positions.length + 1, name: entry.name, type_mark: entry.type_mark,
        unit: entry.unit, discipline: entry.discipline, offers: [],
      }] }),
    });
    const d = await r.json();
    const res = d.results[0];
    const p = normalizePos({
      number: positions.length + 1, name: res.name, type_mark: res.type_mark,
      unit: res.unit, discipline: res.discipline,
      offers: res.offers, _candidates: res.offers,
    });
    p._open = true; p._searched = true; p._found = res.offers.length;
    p._target = window._engineHealth?.min_sources ?? 5;
    positions.push(p);
    renderPositions();
    window.scrollTo(0, $("posList").offsetTop - 80);
  } catch (e) { alert("Не удалось загрузить блок: " + e.message); }
}

loadBaseStatus();
