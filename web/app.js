// Сметчик: спецификация → распознать → взять блоки из базы → собрать Том ТКП + КАЦ.
// Общая машинерия (helpers, рендер позиций, lightbox, poll) — в common.js.

const specFiles = [];
const letterFiles = [];
if ($("fixDate")) $("fixDate").valueAsDate = new Date();

wireDrop("dropSpec", "specInput", specFiles, "specFiles", [".pdf", ".docx", ".xlsx", ".xlsm", ".xls"]);
wireDrop("dropLetter", "letterInput", letterFiles, "letterFiles", [".pdf"]);

/* ---------- шаг 1 → 2: распознать + заполнить из базы ---------- */
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
    await fillFromBase();
  } catch (e) {
    alert("Не удалось распознать: " + e.message + "\nМожно заполнить вручную.");
  } finally {
    $("parseBtn").disabled = false; $("parseBtn").textContent = "Распознать и взять из базы →";
  }
};

$("manualBtn").onclick = () => { if (!positions.length) addPosition(); renderPositions(); };
$("addPosBtn").onclick = () => { addPosition(); renderPositions(); };
$("refillBtn").onclick = fillFromBase;

// Заполняет позиции готовыми блоками из базы (без онлайн-поиска), помечает «нет в базе».
async function fillFromBase() {
  if (!positions.length) return;
  $("baseSummary").textContent = "запрос к базе…";
  try {
    const r = await fetch(engineUrl() + "/base/fill", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ positions: positions.map((p) => ({
        number: p.number, name: p.name, type_mark: p.type_mark, unit: p.unit,
        qty: p.qty, discipline: p.discipline, offers: [],
      })) }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    d.results.forEach((res, i) => {
      // уникальные/вручную заполненные не перетираем, если у них уже есть офферы из писем
      if (positions[i].is_unique) { positions[i]._in_base = undefined; return; }
      positions[i].offers = (res.offers || []).map((o) => ({ ...o, _busy: false }));
      positions[i]._in_base = res.in_base;
    });
    renderPositions();
    $("baseSummary").textContent = `из базы: ${d.found} из ${d.total} позиций`;
  } catch (e) {
    $("baseSummary").textContent = "база недоступна: " + e.message;
  }
}

/* ---------- шаг 3: сборка (без ТСН, без онлайн-поиска) ---------- */
$("buildBtn").onclick = async () => {
  if (!$("objectName").value.trim()) return alert("Укажите наименование объекта (шаг 1)");
  if (!positions.length) return alert("Нет позиций");
  const body = {
    object_name: $("objectName").value.trim(),
    fix_date: $("fixDate").value ? $("fixDate").value.split("-").reverse().join(".") : "",
    use_tsn: false,
    skip_search: true,
    positions: positions.map((p) => ({
      number: p.number, name: p.name, full_name: p.full_name, type_mark: p.type_mark,
      unit: p.unit, qty: p.qty, is_unique: p.is_unique, discipline: p.discipline,
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
    const jobId = (await r.json()).job_id;
    pollJob(jobId, {
      bar: "barFill", step: "stepText", log: "logBox",
      onDone: (j) => {
        setStatus("done", "Готово ✓");
        $("downloads").innerHTML = (j.outputs || []).map((k) =>
          `<a href="${engineUrl()}/jobs/${jobId}/download/${k}">⬇ ${LABELS[k] || k}</a>`).join("");
        $("buildBtn").disabled = false;
      },
      onError: (j) => { setStatus("error", "Ошибка: " + (j.error || "")); $("buildBtn").disabled = false; },
    });
  } catch (e) {
    setStatus("error", "Ошибка: " + e.message); $("buildBtn").disabled = false;
  }
};

const LABELS = { kac: "КАЦ (Excel)", tkp: "Том ТКП (PDF)", tsn: "ТСН-2001 (Excel)" };
function setStatus(cls, text) { const el = $("statusText"); el.className = "status " + cls; el.textContent = text; }
