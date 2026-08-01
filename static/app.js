"use strict";

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const content = () => $("#content");

// ---- helpers ---------------------------------------------------------------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtBytes(n) {
  n = Number(n) || 0; const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function setDot(ok) { $("#conn-dot").className = "dot " + (ok ? "ok" : "bad"); }
let toastTimer;
function toast(msg, kind = "ok") {
  const t = $("#toast"); t.textContent = msg; t.className = `toast ${kind}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}
function loading(el) { el.innerHTML = '<div class="loader">Загрузка…</div>'; }

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-CSRF-Token": CSRF } };
  if (form) { opts.body = form; }
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("auth"); }
  let data = {}; try { data = await res.json(); } catch (_) {}
  if (!res.ok || data.ok === false) { setDot(false); throw new Error(data.error || data.detail || `HTTP ${res.status}`); }
  setDot(true); return data;
}

function pageHeader(d) {
  let h = `<div class="panel-head"><h2>${esc(d.title || "")}</h2>`;
  h += `<button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>`;
  if (d.danger) h += `<div class="alert warn">⚠️ ${esc(d.danger)}</div>`;
  if (d.reboot_note) h += `<div class="alert info">ℹ️ Изменения вступят в силу после перезагрузки роутера.</div>`;
  return h;
}

// ---- routing / sidebar -----------------------------------------------------
let MENU = [];
let active = null;

async function loadMenu() {
  try {
    const { data } = await api("/api/menu");
    MENU = data;
    const sb = $("#sidebar"); sb.innerHTML = "";
    for (const sec of data) {
      const g = document.createElement("div"); g.className = "nav-group";
      g.innerHTML = `<div class="nav-title">${esc(sec.section)}</div>`;
      for (const p of sec.pages) {
        const a = document.createElement("a");
        a.className = "nav-item"; a.dataset.id = p.id; a.textContent = p.title;
        if (p.danger) a.innerHTML += ' <span class="dot-warn" title="осторожно">●</span>';
        a.addEventListener("click", () => selectPage(p));
        g.appendChild(a);
      }
      sb.appendChild(g);
    }
    if (RAW_ENABLED) {
      const g = document.createElement("div"); g.className = "nav-group";
      g.innerHTML = `<div class="nav-title">Экспертное</div>`;
      const a = document.createElement("a");
      a.className = "nav-item"; a.dataset.id = "__raw"; a.textContent = "Сырой запрос";
      a.addEventListener("click", () => selectPage({ id: "__raw", title: "Сырой запрос", kind: "raw" }));
      g.appendChild(a); sb.appendChild(g);
    }
    const first = data[0]?.pages[0];
    if (first) selectPage(first);
  } catch (e) { $("#sidebar").innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
}

function selectPage(meta) {
  active = meta;
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.id === meta.id));
  $("#sidebar").classList.remove("open");
  route(meta);
}

async function route(meta, page = 1) {
  const el = content(); loading(el);
  try {
    if (meta.kind === "raw") return renderRaw(el);
    if (meta.kind === "custom") return CUSTOM[meta.handler](el, meta, page);
    const { data } = await api(`/api/page/${meta.id}?page=${encodeURIComponent(page)}`);
    if (data.kind === "form") return renderForm(el, data);
    if (data.kind === "list") return renderList(el, data);
    if (data.kind === "readonly") return renderReadonly(el, data);
    if (data.kind === "special") return SPECIAL[data.handler] ? SPECIAL[data.handler](el, data) : renderUnknown(el, data);
    renderUnknown(el, data);
  } catch (e) { el.innerHTML = pageHeader(meta) + `<div class="alert error">${esc(e.message)}</div>`; bindRefresh(meta, page); }
}
function bindRefresh(meta, page = 1) { const b = $("#pg-refresh"); if (b) b.addEventListener("click", () => route(meta, page)); }

// ---- generic form ----------------------------------------------------------
function inputHTML(f) {
  const v = f.value;
  const cv = (x) => esc(x);
  if (f.type === "static") return `<div class="ro-value">${cv(v)}</div>`;
  if (f.type === "hidden") return `<input type="hidden" name="${f.name}" value="${cv(v)}">`;
  if (f.type === "select") {
    const opts = f.options.map(o => `<option value="${cv(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("");
    return `<select name="${f.name}">${opts}</select>`;
  }
  if (f.type === "radio") {
    return `<div class="radio-row">` + f.options.map(o =>
      `<label class="radio"><input type="radio" name="${f.name}" value="${cv(o.value)}" ${String(o.value) === String(v) ? "checked" : ""}>${esc(o.label)}</label>`).join("") + `</div>`;
  }
  if (f.type === "checkbox") {
    const on = String(v) === f.checked_value || v === 1 || v === true;
    return `<label class="switch"><input type="checkbox" name="${f.name}" ${on ? "checked" : ""}> ${esc(f.help || "вкл")}</label>`;
  }
  const itype = f.type === "number" ? "number" : (f.type === "password" ? "password" : "text");
  return `<input type="${itype}" name="${f.name}" value="${cv(v)}" ${f.readonly ? "readonly" : ""}>`;
}

function fieldRow(f) {
  if (f.type === "hidden") return inputHTML(f);
  return `<div class="field-row" data-field="${f.name}">
    <label>${esc(f.label)}${f.optional ? ' <span class="muted small">(необяз.)</span>' : ""}</label>
    ${inputHTML(f)}${f.help && f.type !== "checkbox" ? `<div class="muted small">${esc(f.help)}</div>` : ""}</div>`;
}

function collectValues(scope, fields) {
  const vals = {};
  for (const f of fields) {
    if (f.type === "static" || f.type === "button") continue;
    if (f.type === "checkbox") { const i = scope.querySelector(`[name="${f.name}"]`); vals[f.name] = i ? i.checked : false; }
    else if (f.type === "radio") { const i = scope.querySelector(`[name="${f.name}"]:checked`); if (i) vals[f.name] = i.value; }
    else { const i = scope.querySelector(`[name="${f.name}"]`); if (i) vals[f.name] = i.value; }
  }
  return vals;
}

function applyShowIf(scope, fields) {
  const cur = (name) => {
    const r = scope.querySelector(`[name="${name}"]:checked`); if (r) return r.value;
    const i = scope.querySelector(`[name="${name}"]`); return i ? i.value : null;
  };
  for (const f of fields) {
    if (!f.show_if) continue;
    const row = scope.querySelector(`.field-row[data-field="${f.name}"]`);
    if (row) row.style.display = String(cur(f.show_if[0])) === String(f.show_if[1]) ? "" : "none";
  }
}

function renderForm(el, d) {
  el.innerHTML = pageHeader(d) +
    `<div class="subcard"><div class="form-grid" id="frm">${d.fields.map(fieldRow).join("")}</div>
     <div class="row-actions"><button class="btn btn-primary" id="frm-save">Сохранить</button></div></div>`;
  const frm = $("#frm");
  bindRefresh(d);
  applyShowIf(frm, d.fields);
  frm.addEventListener("change", () => applyShowIf(frm, d.fields));
  $("#frm-save").addEventListener("click", async () => {
    try { await api(`/api/page/${d.id}/save`, { method: "POST", body: collectValues(frm, d.fields) });
      toast("Сохранено"); route(active); } catch (e) { toast(e.message, "err"); }
  });
}

// ---- generic list ----------------------------------------------------------
function cellHTML(c) {
  if (c.kind === "status" || c.kind === "bool") {
    const on = c.value === 1 || c.value === "1" || c.value === true;
    return `<span class="badge ${on ? "on" : "off"}">${on ? "вкл" : "выкл"}</span>`;
  }
  return esc(c.value);
}
function paginationHTML(page, hasMore, attr) {
  page = Number(page) || 1;
  if (page <= 1 && !hasMore) return "";
  return `<div class="pagination">
    <button class="btn btn-sm" ${page <= 1 ? "disabled" : ""} ${attr}="${page - 1}">← Назад</button>
    <span class="muted">Страница ${page}</span>
    <button class="btn btn-sm" ${!hasMore ? "disabled" : ""} ${attr}="${page + 1}">Вперёд →</button>
  </div>`;
}
function portExpressionCount(value) {
  const expression = String(value || "").replace(/[–—]/g, "-").replace(/\s/g, "");
  if (!expression) return 0;
  const ports = new Set();
  for (const part of expression.split(",")) {
    const match = part.match(/^(\d+)(?:-(\d+))?$/);
    if (!match) return 0;
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (end < start) return 0;
    for (let port = start; port <= end; port++) ports.add(port);
  }
  return ports.size;
}
function renderList(el, d) {
  let head = d.columns.map(c => `<th>${esc(c.label)}</th>`).join("");
  let rows = d.rows.map(r => `<tr>${r.cells.map(cellHTML).map(x => `<td>${x}</td>`).join("")}
      <td><button class="btn btn-danger btn-sm" data-del="${r.id}">Удалить</button></td></tr>`).join("");
  if (!rows) rows = `<tr><td colspan="${d.columns.length + 1}" class="muted">Записей нет</td></tr>`;
  let html = pageHeader(d) + `<div class="table-scroll"><table><thead><tr>${head}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  html += paginationHTML(d.page, d.has_more, "data-list-page");
  if (d.do_all && d.do_all.length) {
    const map = { EnAll: "Включить все", DisAll: "Выключить все", DelAll: "Удалить все" };
    html += `<div class="row-actions">` + d.do_all.map(a =>
      `<button class="btn ${a === "DelAll" ? "btn-danger" : ""}" data-all="${a}">${map[a] || a}</button>`).join("") + `</div>`;
  }
  if (d.can_add) {
    const rangeNote = d.id === "SpecialAppRpm"
      ? `<div class="alert info">Диапазон будет добавлен как отдельное правило для каждого входящего порта.</div>` : "";
    html += `<div class="subcard"><h3>Добавить запись</h3>${rangeNote}
      <div class="form-grid" id="add">${d.add_fields.map(fieldRow).join("")}</div>
      <div class="row-actions"><button class="btn btn-primary" id="add-btn">Добавить</button></div></div>`;
  }
  el.innerHTML = html;
  bindRefresh(active, d.page);
  $$('[data-list-page]:not([disabled])').forEach(b => b.addEventListener("click", () => route(active, Number(b.dataset.listPage))));
  $$("[data-del]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("Удалить запись?")) return;
    try { await api(`/api/page/${d.id}/list/delete`, { method: "POST", body: { id: Number(b.dataset.del), page: d.page } });
      toast("Удалено"); route(active, d.page); } catch (e) { toast(e.message, "err"); }
  }));
  $$("[data-all]").forEach(b => b.addEventListener("click", async () => {
    if (b.dataset.all === "DelAll" && !confirm("Удалить ВСЕ записи?")) return;
    try { await api(`/api/page/${d.id}/list/doall`, { method: "POST", body: { action: b.dataset.all, page: d.page } });
      toast("Готово"); route(active, d.page); } catch (e) { toast(e.message, "err"); }
  }));
  if (d.can_add) {
    const add = $("#add"); applyShowIf(add, d.add_fields);
    if (d.id === "SpecialAppRpm") {
      const triggerPort = add.querySelector('[name="trPort"]');
      const incomingPorts = add.querySelector('[name="inPort"]');
      const state = add.querySelector('[name="State"]');
      if (triggerPort) { triggerPort.min = "1"; triggerPort.max = "65535"; }
      if (incomingPorts) incomingPorts.maxLength = 64;
      if (state) state.value = "1";
    }
    add.addEventListener("change", () => applyShowIf(add, d.add_fields));
    const addButton = $("#add-btn");
    addButton.addEventListener("click", async () => {
      const values = collectValues(add, d.add_fields);
      const count = d.id === "SpecialAppRpm" ? portExpressionCount(values.inPort) : 1;
      if (count > 64) { toast("За один раз можно добавить не более 64 портов", "err"); return; }
      if (count > 1 && !confirm(`Будет создано ${count} отдельных правил. Продолжить?`)) return;
      addButton.disabled = true;
      try { const result = await api(`/api/page/${d.id}/list/add`, { method: "POST", body: { values, page: d.page } });
        toast(result.added > 1 ? `Добавлено правил: ${result.added}` : "Добавлено"); route(active, d.page); } catch (e) { toast(e.message, "err"); }
      finally { addButton.disabled = false; }
    });
  }
}

// ---- generic readonly ------------------------------------------------------
function renderReadonly(el, d) {
  if (d.rows) { renderList(el, { ...d, do_all: [], can_add: false }); return; }
  let html = pageHeader(d);
  for (const [name, arr] of Object.entries(d.arrays || {})) {
    const items = (arr || []).filter((x, i) => !(i >= arr.length - 2 && x === 0));
    html += `<div class="subcard"><h3>${esc(name)}</h3><pre class="raw-out">${esc(items.join("\n"))}</pre></div>`;
  }
  el.innerHTML = html; bindRefresh(d);
}

function renderUnknown(el, d) {
  el.innerHTML = pageHeader(d) + `<div class="alert info">Страница типа «${esc(d.kind)}/${esc(d.handler || "")}» пока без спец-вида.</div>
    <pre class="raw-out">${esc(JSON.stringify(d, null, 2))}</pre>`;
  bindRefresh(d);
}

// ---- special components ----------------------------------------------------
const SPECIAL = {
  wps(el, d) {
    const st = (d.state.wpsInf) || [];
    const disp = (d.extra.readonly_display || []).map(r => {
      let v = st[r.src[1]];
      if (r.map) v = r.map[String(v)] ?? v;
      else if (r.kind === "bool") v = (v === 1 || v === "1") ? "да" : "нет";
      return `<div class="kv"><span>${esc(r.label)}</span><span>${esc(v)}</span></div>`;
    }).join("");
    const acts = (d.extra.special_actions || []).filter(a => {
      if (!a.visible_if) return true;
      const [arr, idx, val] = a.visible_if; return String((d.state[arr] || [])[idx]) === String(val);
    });
    el.innerHTML = pageHeader(d) +
      `<div class="cards"><div class="card"><h3>Состояние WPS</h3>${disp}</div></div>
       <div class="subcard"><h3>Действия</h3><div class="row-actions">` +
      acts.map((a, i) => `<button class="btn" data-wps="${i}">${esc(a.label)}</button>`).join("") + `</div></div>`;
    bindRefresh(d);
    $$("[data-wps]").forEach(b => b.addEventListener("click", async () => {
      const a = acts[Number(b.dataset.wps)];
      const q = a.url.split("?")[1] || ""; const params = {};
      q.split("&").forEach(kv => { const [k, ...r] = kv.split("="); if (k) params[k] = decodeURIComponent((r.join("=") || "").replace(/\+/g, " ")); });
      try { await api("/api/special/wps", { method: "POST", body: { params } }); toast("Выполнено"); route(active); }
      catch (e) { toast(e.message, "err"); }
    }));
  },

  diagnostic(el, d) {
    el.innerHTML = pageHeader(d) +
      `<div class="subcard"><div class="form-grid" id="diag">${d.fields.map(fieldRow).join("")}</div>
       <div class="row-actions"><button class="btn btn-primary" id="diag-run">Запустить</button></div>
       <pre id="diag-out" class="raw-out" style="margin-top:14px">—</pre></div>`;
    const diag = $("#diag"); bindRefresh(d);
    // дефолты
    const set = (n, v) => { const i = diag.querySelector(`[name="${n}"]`); if (i && !i.value) i.value = v; };
    set("sendNum", 4); set("pSize", 64); set("overTime", 800); set("trHops", 20);
    applyShowIf(diag, d.fields); diag.addEventListener("change", () => applyShowIf(diag, d.fields));
    $("#diag-run").addEventListener("click", async () => {
      const vals = collectValues(diag, d.fields);
      if (!vals.pingAddr) { toast("Укажи адрес", "err"); return; }
      $("#diag-out").textContent = "Выполняется…";
      try { const r = await api("/api/special/diagnostic", { method: "POST", body: vals });
        $("#diag-out").textContent = r.output || "(пусто)"; } catch (e) { $("#diag-out").textContent = "Ошибка: " + e.message; }
    });
  },

  "file-upload"(el, d) {
    const inf = d.state.softUpInf || [];
    el.innerHTML = pageHeader(d) +
      `<div class="cards"><div class="card"><h3>Текущая прошивка</h3>
        <div class="kv"><span>Версия ПО</span><span>${esc(inf[0] || "")}</span></div>
        <div class="kv"><span>Версия железа</span><span>${esc(inf[1] || "")}</span></div></div></div>
       <div class="subcard"><h3>Обновление прошивки</h3>
        <div class="alert error">⚠️ Неправильный файл или обрыв связи могут превратить роутер в «кирпич».
          Используй только официальную прошивку для WR740N v4. Не выключай питание во время прошивки.</div>
        <input type="file" id="fw-file" accept=".bin">
        <div class="row-actions"><button class="btn btn-danger" id="fw-go">Прошить</button></div></div>`;
    bindRefresh(d);
    $("#fw-go").addEventListener("click", async () => {
      const f = $("#fw-file").files[0];
      if (!f) { toast("Выбери .bin файл", "err"); return; }
      if (!confirm("Прошить роутер этим файлом? Не прерывай процесс!")) return;
      const fd = new FormData(); fd.append("file", f);
      toast("Загрузка прошивки…");
      try { await api("/api/special/firmware", { method: "POST", form: fd }); toast("Прошивка загружена, роутер перезагрузится"); }
      catch (e) { toast(e.message, "err"); }
    });
  },

  "backup-restore"(el, d) {
    const locked = ((d.state.bakNRestroreInf || [])[0]) ? true : false;
    el.innerHTML = pageHeader(d) +
      `<div class="subcard"><h3>Резервная копия</h3>
        <p class="muted">Скачать текущую конфигурацию роутера в файл config.bin.</p>
        <a class="btn btn-primary" href="/api/special/backup">Скачать бэкап</a></div>
       <div class="subcard"><h3>Восстановление</h3>
        <div class="alert warn">⚠️ Полностью перезапишет все настройки роутера. Файл только от этой модели.</div>
        <input type="file" id="rs-file" accept=".bin" ${locked ? "disabled" : ""}>
        <div class="row-actions"><button class="btn btn-danger" id="rs-go" ${locked ? "disabled" : ""}>Восстановить</button></div>
        ${locked ? '<div class="muted small">Восстановление недоступно (доступ не из LAN).</div>' : ""}</div>`;
    bindRefresh(d);
    const go = $("#rs-go");
    if (go) go.addEventListener("click", async () => {
      const f = $("#rs-file").files[0];
      if (!f) { toast("Выбери файл бэкапа", "err"); return; }
      if (!confirm("Восстановить настройки из файла? Текущие настройки будут перезаписаны.")) return;
      const fd = new FormData(); fd.append("file", f); toast("Загрузка…");
      try { await api("/api/special/restore", { method: "POST", form: fd }); toast("Конфигурация восстановлена, роутер перезагрузится"); }
      catch (e) { toast(e.message, "err"); }
    });
  },

  "factory-reset"(el, d) {
    el.innerHTML = pageHeader(d) +
      `<div class="subcard"><div class="alert error">⚠️ Полный сброс к заводским настройкам.
        Роутер вернётся на 192.168.0.1, логин/пароль admin/admin, Wi-Fi и все настройки сотрутся.
        Панель потеряет связь с роутером.</div>
        <div class="field-row"><label>Для подтверждения введи <code>RESET</code></label>
          <input type="text" id="fr-confirm" placeholder="RESET"></div>
        <div class="row-actions"><button class="btn btn-danger" id="fr-go">Сбросить к заводским</button></div></div>`;
    bindRefresh(d);
    $("#fr-go").addEventListener("click", async () => {
      if ($("#fr-confirm").value !== "RESET") { toast("Введи RESET для подтверждения", "err"); return; }
      if (!confirm("Точно сбросить роутер к заводским настройкам?")) return;
      try { await api("/api/special/factory-reset", { method: "POST", body: { confirm: "RESET" } });
        toast("Сброс запущен"); } catch (e) { toast(e.message, "err"); }
    });
  },

  "password-change"(el, d) {
    el.innerHTML = pageHeader(d) +
      `<div class="subcard"><div class="alert warn">⚠️ Меняет логин/пароль самого роутера. Сервер сразу обновит их у себя,
        но не забудь обновить ROUTER_USERNAME/ROUTER_PASSWORD в env, иначе после рестарта панель потеряет доступ.</div>
        <div class="form-grid">
          <div class="field-row"><label>Текущий логин</label><input id="pw-ou" placeholder="(по умолчанию из настроек)"></div>
          <div class="field-row"><label>Текущий пароль</label><input id="pw-op" type="password" placeholder="(по умолчанию из настроек)"></div>
          <div class="field-row"><label>Новый логин</label><input id="pw-nu"></div>
          <div class="field-row"><label>Новый пароль</label><input id="pw-np" type="password"></div>
        </div><div class="row-actions"><button class="btn btn-primary" id="pw-go">Сменить</button></div></div>`;
    bindRefresh(d);
    $("#pw-go").addEventListener("click", async () => {
      const nu = $("#pw-nu").value.trim(), np = $("#pw-np").value;
      if (!nu || !np) { toast("Заполни новый логин и пароль", "err"); return; }
      if (!confirm("Сменить логин/пароль роутера?")) return;
      try { const r = await api("/api/special/password-change", { method: "POST", body: {
          new_user: nu, new_pass: np, old_user: $("#pw-ou").value.trim(), old_pass: $("#pw-op").value } });
        toast(r.note || "Готово"); } catch (e) { toast(e.message, "err"); }
    });
  },

  wizard(el, d) {
    el.innerHTML = pageHeader(d) +
      `<div class="subcard"><div class="alert info">Мастер быстрой настройки не нужен в этой панели —
        все шаги доступны в разделах слева: «Сеть → WAN» (интернет) и «Беспроводной режим» (Wi-Fi).</div></div>`;
    bindRefresh(d);
  },
};

// ---- custom (родные красивые) компоненты ----------------------------------
const CUSTOM = {
  async overview(el) {
    loading(el);
    try {
      const { data: d } = await api("/api/status");
      const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${esc(v)}</span></div>`;
      el.innerHTML = `<div class="panel-head"><h2>Состояние</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <div class="cards">
          <div class="card"><h3>Система</h3>${kv("Прошивка", d.firmware)}${kv("Железо", d.hardware)}${kv("Аптайм", d.uptime_human)}</div>
          <div class="card"><h3>WAN (интернет)</h3>${kv("Тип", d.wan.type)}${kv("IP", d.wan.ip)}${kv("Шлюз", d.wan.gateway)}${kv("DNS", d.wan.dns)}${kv("Онлайн", d.wan.online_time)}${kv("Статус", d.wan.connected ? "🟢 подключено" : "🔴 нет связи")}</div>
          <div class="card"><h3>LAN</h3>${kv("IP", d.lan.ip)}${kv("Маска", d.lan.mask)}${kv("MAC", d.lan.mac)}</div>
          <div class="card"><h3>Wi-Fi</h3>${kv("Радио", d.wlan.enabled ? "🟢 вкл" : "🔴 выкл")}${kv("SSID", d.wlan.ssid)}${kv("Канал", d.wlan.channel)}${kv("Режим", d.wlan.mode)}</div>
          <div class="card"><h3>Трафик</h3>${kv("Принято", fmtBytes(d.traffic.bytes_recv))}${kv("Отправлено", fmtBytes(d.traffic.bytes_sent))}${kv("Пакетов ↓", d.traffic.pkts_recv)}${kv("Пакетов ↑", d.traffic.pkts_sent)}</div>
        </div>`;
      bindRefresh(active);
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async stations(el) {
    loading(el);
    try {
      const { data: d } = await api("/api/devices");
      let rows = d.wlan_stations.map(s => `<tr><td class="mono">${esc(s.mac)}</td><td>${esc(s.status)}</td><td>${esc(s.rx)}</td><td>${esc(s.tx)}</td></tr>`).join("");
      if (!rows) rows = '<tr><td colspan="4" class="muted">Нет беспроводных клиентов</td></tr>';
      el.innerHTML = `<div class="panel-head"><h2>Статистика Wi-Fi</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <table><thead><tr><th>MAC</th><th>Статус</th><th>Принято</th><th>Отправлено</th></tr></thead><tbody>${rows}</tbody></table>`;
      bindRefresh(active);
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async "dhcp-clients"(el) {
    loading(el);
    try {
      const { data: d } = await api("/api/devices");
      const wifi = new Set(d.wlan_stations.map(s => String(s.mac).toUpperCase()));
      let rows = d.dhcp_clients.map(c => `<tr><td>${esc(c.name)}</td><td class="mono">${esc(c.ip)}</td><td class="mono">${esc(c.mac)}</td><td>${esc(c.lease)}</td><td>${wifi.has(String(c.mac).toUpperCase()) ? "📶 Wi-Fi" : "🔌 LAN"}</td></tr>`).join("");
      if (!rows) rows = '<tr><td colspan="5" class="muted">Нет активных клиентов</td></tr>';
      el.innerHTML = `<div class="panel-head"><h2>Список клиентов DHCP</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <table><thead><tr><th>Имя</th><th>IP</th><th>MAC</th><th>Аренда</th><th>Подключение</th></tr></thead><tbody>${rows}</tbody></table>`;
      bindRefresh(active);
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async portforward(el, _meta, page = 1) {
    loading(el);
    try {
      const { data } = await api(`/api/portforward?page=${encodeURIComponent(page)}`);
      const list = data.items || [];
      let rows = list.map(v => `<tr><td>${esc(v.service_port)}</td><td>${esc(v.internal_port)}</td><td class="mono">${esc(v.ip)}</td><td>${esc(v.protocol)}</td>
        <td><span class="badge ${v.enabled ? "on" : "off"}">${v.enabled ? "вкл" : "выкл"}</span></td>
        <td><button class="btn btn-danger btn-sm" data-del="${v.id}">Удалить</button></td></tr>`).join("");
      if (!rows) rows = '<tr><td colspan="6" class="muted">Правил нет</td></tr>';
      el.innerHTML = `<div class="panel-head"><h2>Виртуальные серверы (проброс портов)</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <div class="table-scroll"><table><thead><tr><th>Внешний порт</th><th>Внутр. порт</th><th>IP</th><th>Протокол</th><th>Статус</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
        ${paginationHTML(data.page, data.has_more, "data-pf-page")}
        <div class="subcard"><h3>Добавить правило</h3>
          <div class="alert info">Диапазон будет добавлен как отдельное правило для каждого порта.</div><div class="form-grid">
          <div class="field-row"><label>Внешний порт или диапазон</label><input id="pf-ext" placeholder="8080 или 8000-8010"></div>
          <div class="field-row"><label>Внутренний порт или диапазон</label><input id="pf-int" placeholder="как внешний"></div>
          <div class="field-row"><label>IP устройства</label><input id="pf-ip" placeholder="192.168.0.100"></div>
          <div class="field-row"><label>Протокол</label><select id="pf-proto"><option value="1">ALL</option><option value="2">TCP</option><option value="3">UDP</option></select></div>
        </div><div class="row-actions"><button class="btn btn-primary" id="pf-add">Добавить</button></div></div>
        <div class="row-actions"><button class="btn" data-pfall="EnAll">Включить все</button><button class="btn" data-pfall="DisAll">Выключить все</button><button class="btn btn-danger" data-pfall="DelAll">Удалить все</button></div>`;
      bindRefresh(active, data.page);
      $$('[data-pf-page]:not([disabled])').forEach(b => b.addEventListener("click", () => CUSTOM.portforward(el, active, Number(b.dataset.pfPage))));
      $$("[data-del]").forEach(b => b.addEventListener("click", async () => {
        if (!confirm("Удалить правило?")) return;
        try { await api("/api/portforward/delete", { method: "POST", body: { id: Number(b.dataset.del), page: data.page } }); toast("Удалено"); CUSTOM.portforward(el, active, data.page); } catch (e) { toast(e.message, "err"); }
      }));
      $("#pf-add").addEventListener("click", async () => {
        const ext = $("#pf-ext").value.trim(), ip = $("#pf-ip").value.trim();
        if (!ext || !ip) { toast("Заполни внешний порт и IP", "err"); return; }
        const count = portExpressionCount(ext);
        if (count > 64) { toast("За один раз можно добавить не более 64 портов", "err"); return; }
        if (count > 1 && !confirm(`Будет создано ${count} отдельных правил. Продолжить?`)) return;
        const button = $("#pf-add"); button.disabled = true;
        try { const result = await api("/api/portforward/add", { method: "POST", body: { ext_port: ext, int_port: $("#pf-int").value.trim() || ext, ip, protocol: +$("#pf-proto").value, enabled: true, page: data.page } });
          toast(result.added > 1 ? `Добавлено правил: ${result.added}` : "Добавлено"); CUSTOM.portforward(el, active, data.page); } catch (e) { toast(e.message, "err"); }
        finally { button.disabled = false; }
      });
      $$("[data-pfall]").forEach(b => b.addEventListener("click", async () => {
        if (b.dataset.pfall === "DelAll" && !confirm("Удалить ВСЕ правила?")) return;
        try { await api("/api/portforward/all", { method: "POST", body: { action: b.dataset.pfall, page: data.page } }); toast("Готово"); CUSTOM.portforward(el, active, data.page); } catch (e) { toast(e.message, "err"); }
      }));
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async dhcp(el) {
    loading(el);
    try {
      const { data: d } = await api("/api/dhcp");
      const f = (id, l, v) => `<div class="field-row"><label>${l}</label><input id="${id}" value="${esc(v)}"></div>`;
      el.innerHTML = `<div class="panel-head"><h2>Настройки DHCP</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <div class="subcard"><label class="switch" style="margin-bottom:12px"><input type="checkbox" id="dh-en" ${d.enabled ? "checked" : ""}> DHCP-сервер включён</label>
        <div class="form-grid">${f("dh-start", "Начальный IP", d.start_ip)}${f("dh-end", "Конечный IP", d.end_ip)}${f("dh-lease", "Аренда (мин)", d.lease)}
          ${f("dh-gw", "Шлюз", d.gateway)}${f("dh-dns1", "DNS 1", d.dns1)}${f("dh-dns2", "DNS 2", d.dns2)}${f("dh-domain", "Домен", d.domain)}</div>
        <div class="row-actions"><button class="btn btn-primary" id="dh-save">Сохранить</button></div></div>`;
      bindRefresh(active);
      $("#dh-save").addEventListener("click", async () => {
        try { await api("/api/dhcp/save", { method: "POST", body: { enabled: $("#dh-en").checked, start_ip: $("#dh-start").value, end_ip: $("#dh-end").value, lease: $("#dh-lease").value, gateway: $("#dh-gw").value, domain: $("#dh-domain").value, dns1: $("#dh-dns1").value, dns2: $("#dh-dns2").value } });
          toast("Сохранено"); } catch (e) { toast(e.message, "err"); }
      });
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async wifi(el) {
    loading(el);
    try {
      const { data: d } = await api("/api/wlan");
      const n = d.network, s = d.security;
      const chan = (cur) => { let o = `<option value="15" ${cur == 15 ? "selected" : ""}>Авто</option>`; for (let i = 1; i <= 13; i++) o += `<option value="${i}" ${cur == i ? "selected" : ""}>${i}</option>`; return o; };
      const modes = { 1: "11b only", 2: "11g only", 3: "11n only", 4: "11bg mixed", 5: "11bgn mixed" };
      el.innerHTML = `<div class="panel-head"><h2>Wi-Fi</h2><button class="btn btn-ghost" id="pg-refresh">↻ Обновить</button></div>
        <div class="subcard"><h3>Сеть</h3><div class="form-grid">
          <div class="field-row"><label>SSID (имя сети)</label><input id="wf-ssid" value="${esc(n.ssid)}"></div>
          <div class="field-row"><label>Канал</label><select id="wf-channel">${chan(n.channel)}</select></div>
          <div class="field-row"><label>Режим</label><select id="wf-mode">${Object.entries(modes).map(([v, t]) => `<option value="${v}" ${n.mode == v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        </div><div class="row-actions">
          <label class="switch"><input type="checkbox" id="wf-radio" ${n.radio_on ? "checked" : ""}> Радио включено</label>
          <label class="switch"><input type="checkbox" id="wf-bcast" ${n.ssid_broadcast ? "checked" : ""}> Вещать SSID</label>
        </div><div class="row-actions"><button class="btn btn-primary" id="wf-save">Сохранить сеть</button></div></div>
        <div class="subcard"><h3>Безопасность</h3><div class="kv"><span>Тип защиты</span><span>${esc(s.sec_type_name)}</span></div>
          <div class="form-grid" style="margin-top:10px"><div class="field-row"><label>Пароль Wi-Fi (8–63)</label><input id="wf-pass" value="${esc(s.psk_password)}"></div></div>
          <div class="row-actions"><button class="btn btn-primary" id="wf-pass-save">Сменить пароль</button></div></div>`;
      bindRefresh(active);
      $("#wf-save").addEventListener("click", async () => {
        try { await api("/api/wlan/save", { method: "POST", body: { ssid: $("#wf-ssid").value, channel: $("#wf-channel").value, mode: $("#wf-mode").value, radio_on: $("#wf-radio").checked, ssid_broadcast: $("#wf-bcast").checked } });
          toast("Сохранено"); } catch (e) { toast(e.message, "err"); }
      });
      $("#wf-pass-save").addEventListener("click", async () => {
        const p = $("#wf-pass").value; if (p.length < 8 || p.length > 63) { toast("Пароль 8–63 символа", "err"); return; }
        if (!confirm("Сменить пароль Wi-Fi?")) return;
        try { await api("/api/wlan/password", { method: "POST", body: { password: p } }); toast("Пароль изменён"); } catch (e) { toast(e.message, "err"); }
      });
    } catch (e) { el.innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
  },

  async reboot(el) {
    el.innerHTML = `<div class="panel-head"><h2>Перезагрузка</h2></div>
      <div class="subcard"><p class="muted">Роутер перезагрузится, связь пропадёт на ~30–60 секунд.</p>
      <button class="btn btn-danger" id="rb-go">Перезагрузить роутер</button></div>`;
    $("#rb-go").addEventListener("click", async () => {
      if (!confirm("Перезагрузить роутер?")) return;
      try { await api("/api/reboot", { method: "POST" }); toast("Команда отправлена"); } catch (e) { toast(e.message, "err"); }
    });
  },
};

// ---- raw console -----------------------------------------------------------
function renderRaw(el) {
  el.innerHTML = `<div class="panel-head"><h2>Сырой запрос к роутеру</h2></div>
    <div class="subcard"><p class="muted">GET к <code>/userRpm/*</code> — полный доступ к веб-морде роутера.</p>
      <div class="form-grid"><div class="field-row"><label>Путь</label><input id="raw-path" value="/userRpm/StatusRpm.htm"></div></div>
      <div class="field-row"><label>Параметры (ключ=значение, по строке)</label><textarea id="raw-params" rows="3"></textarea></div>
      <div class="row-actions"><button class="btn btn-primary" id="raw-send">Отправить</button></div>
      <pre id="raw-out" class="raw-out" style="margin-top:12px"></pre></div>`;
  $("#raw-send").addEventListener("click", async () => {
    const params = {}; $("#raw-params").value.split(/\r?\n/).forEach(l => { l = l.trim(); const i = l.indexOf("="); if (i > 0) params[l.slice(0, i).trim()] = l.slice(i + 1).trim(); });
    $("#raw-out").textContent = "Запрос…";
    try { const { data } = await api("/api/raw", { method: "POST", body: { path: $("#raw-path").value.trim(), params } });
      $("#raw-out").textContent = `HTTP ${data.status}\n\n${data.body}`; } catch (e) { $("#raw-out").textContent = "Ошибка: " + e.message; }
  });
}

// ---- init ------------------------------------------------------------------
$("#burger").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
loadMenu();
