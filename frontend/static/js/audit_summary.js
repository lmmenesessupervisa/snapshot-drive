// /audit — vista "Resumen general": tabla plana sortable + filterable.
// Reusa el data ya cargado por audit_tree.js (no hace fetch propio).

const SUMMARY_STATE = {
  rows: [],
  sortBy: "fecha",
  sortDir: "desc",     // "asc" | "desc"
  // Filtros multi-select por columna. Set vacío = "todos".
  filterCliente: new Set(),
  filterProyecto: new Set(),
  filterEstado: new Set(),
  // Popover abierto actualmente
  openPopover: null,
};

// ---------- estado derivado ----------
// Reciente <7d (verde) · Stale 7-30d (ámbar) · Crítico >30d o sin data (rojo).
function deriveEstado(lastTs) {
  const days = window.ageDays(lastTs);
  if (days == null) return "no_data";
  if (days < 7) return "ok";
  if (days < 30) return "stale";
  return "critico";
}

function estadoLabel(s) {
  return ({ ok: "Reciente", stale: "Stale", critico: "Crítico", no_data: "Sin datos" })[s] || s;
}
function estadoChip(s) {
  const cls = ({ ok: "chip-emerald", stale: "chip-amber", critico: "chip-rose", no_data: "chip-slate" })[s] || "chip-slate";
  return `<span class="chip ${cls}">${estadoLabel(s)}</span>`;
}

// ---------- aplanar tree → filas ----------
function flattenRows(data) {
  if (!data || !data.proyectos) return [];
  const rows = [];
  for (const p of data.proyectos) {
    const sizeProyecto = p.size || 0;
    for (const r of p.regions || []) {
      for (const c of r.clients || []) {
        rows.push({
          cliente: c.label,
          proyecto: p.name,
          entorno: r.entorno,
          pais: r.pais,
          estado: deriveEstado(c.last_ts),
          fecha: c.last_ts || "",
          size_cli: c.size || 0,
          size_pro: sizeProyecto,
          files: c.files || 0,
        });
      }
    }
  }
  return rows;
}

// ---------- KPIs ----------
function renderSummaryKpis(rows) {
  const total = rows.length;
  const ok    = rows.filter(r => r.estado === "ok").length;
  const stale = rows.filter(r => r.estado === "stale").length;
  const crit  = rows.filter(r => r.estado === "critico" || r.estado === "no_data").length;
  document.getElementById("ks-rows").textContent  = total;
  document.getElementById("ks-ok").textContent    = ok;
  document.getElementById("ks-stale").textContent = stale;
  document.getElementById("ks-crit").textContent  = crit;
}

// ---------- filtros + sort ----------
function applyFilters(rows) {
  return rows.filter(r => {
    if (SUMMARY_STATE.filterCliente.size && !SUMMARY_STATE.filterCliente.has(r.cliente)) return false;
    if (SUMMARY_STATE.filterProyecto.size && !SUMMARY_STATE.filterProyecto.has(r.proyecto)) return false;
    if (SUMMARY_STATE.filterEstado.size && !SUMMARY_STATE.filterEstado.has(r.estado)) return false;
    return true;
  });
}

function applySort(rows) {
  const { sortBy, sortDir } = SUMMARY_STATE;
  const dir = sortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    let x = a[sortBy], y = b[sortBy];
    if (x == null) x = "";
    if (y == null) y = "";
    if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
    return String(x).localeCompare(String(y), "es", { numeric: true }) * dir;
  });
}

// ---------- popovers de filtro por columna ----------
function uniqueValues(col) {
  const s = new Set();
  for (const r of SUMMARY_STATE.rows) s.add(r[col]);
  return [...s].sort((a, b) => String(a).localeCompare(String(b), "es", { numeric: true }));
}

function popoverHtml(col) {
  const stateMap = {
    cliente:  SUMMARY_STATE.filterCliente,
    proyecto: SUMMARY_STATE.filterProyecto,
    estado:   SUMMARY_STATE.filterEstado,
  };
  const sel = stateMap[col];
  const values = col === "estado"
    ? ["ok", "stale", "critico", "no_data"]
    : uniqueValues(col);
  const items = values.map(v => {
    const checked = sel.has(v) ? "checked" : "";
    const label = col === "estado" ? estadoLabel(v) : v;
    return `<label class="summary-pop-item">
      <input type="checkbox" data-value="${v}" ${checked}>
      <span>${label}</span>
    </label>`;
  }).join("");
  return `
    <div class="summary-pop">
      <div class="summary-pop-header">
        <span>Filtrar ${col}</span>
        <button class="summary-pop-clear" data-col="${col}">Limpiar</button>
      </div>
      <div class="summary-pop-body">${items}</div>
    </div>
  `;
}

function closePopover() {
  if (SUMMARY_STATE.openPopover) {
    SUMMARY_STATE.openPopover.remove();
    SUMMARY_STATE.openPopover = null;
  }
}

function openPopover(th, col) {
  closePopover();
  th.insertAdjacentHTML("beforeend", popoverHtml(col));
  const pop = th.querySelector(".summary-pop");
  SUMMARY_STATE.openPopover = pop;
  // Stop propagation: clicks dentro del popover no cierran.
  pop.addEventListener("click", (e) => e.stopPropagation());
  pop.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener("change", () => {
      const stateMap = {
        cliente: SUMMARY_STATE.filterCliente,
        proyecto: SUMMARY_STATE.filterProyecto,
        estado: SUMMARY_STATE.filterEstado,
      };
      const set = stateMap[col];
      if (cb.checked) set.add(cb.dataset.value);
      else set.delete(cb.dataset.value);
      renderSummaryRows();
    });
  });
  pop.querySelector(".summary-pop-clear").addEventListener("click", () => {
    const stateMap = {
      cliente: SUMMARY_STATE.filterCliente,
      proyecto: SUMMARY_STATE.filterProyecto,
      estado: SUMMARY_STATE.filterEstado,
    };
    stateMap[col].clear();
    closePopover();
    renderSummaryHeaders();
    renderSummaryRows();
  });
}

// Cierra popover al click fuera
document.addEventListener("click", (e) => {
  if (!e.target.closest(".summary-th-icon") && !e.target.closest(".summary-pop")) {
    closePopover();
  }
});

// ---------- render ----------
function renderSummaryHeaders() {
  const ths = document.querySelectorAll(".summary-table thead th[data-col]");
  ths.forEach(th => {
    const col = th.dataset.col;
    const sortable = true;  // todas sorteables
    const filterable = ["cliente", "proyecto", "estado"].includes(col);
    const isSorted = SUMMARY_STATE.sortBy === col;
    const dirArrow = isSorted ? (SUMMARY_STATE.sortDir === "asc" ? "▲" : "▼") : "";
    const stateMap = {
      cliente: SUMMARY_STATE.filterCliente,
      proyecto: SUMMARY_STATE.filterProyecto,
      estado: SUMMARY_STATE.filterEstado,
    };
    const filterCount = stateMap[col]?.size || 0;
    const filterIcon = filterable
      ? `<button class="summary-th-icon" data-col="${col}" title="Filtrar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
          ${filterCount ? `<span class="summary-th-badge">${filterCount}</span>` : ""}
        </button>`
      : "";
    const labelText = th.querySelector(".summary-th-label")?.textContent || th.textContent.trim();
    const labelMap = {
      cliente: "Cliente", proyecto: "Proyecto", estado: "Estado",
      fecha: "Último backup", size_cli: "Peso total cliente", size_pro: "Peso total proyecto",
    };
    th.innerHTML = `
      <span class="summary-th-sort" data-col="${col}">
        <span class="summary-th-label">${labelMap[col] || labelText}</span>
        <span class="summary-th-arrow">${dirArrow}</span>
      </span>
      ${filterIcon}
    `;
  });
  // Wire up sort + filter triggers
  document.querySelectorAll(".summary-th-sort").forEach(el => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const col = el.dataset.col;
      if (SUMMARY_STATE.sortBy === col) {
        SUMMARY_STATE.sortDir = SUMMARY_STATE.sortDir === "asc" ? "desc" : "asc";
      } else {
        SUMMARY_STATE.sortBy = col;
        SUMMARY_STATE.sortDir = "asc";
      }
      renderSummaryHeaders();
      renderSummaryRows();
    });
  });
  document.querySelectorAll(".summary-th-icon").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const col = btn.dataset.col;
      const th = btn.parentElement;
      // Toggle: si ya hay popover en esta th, cerrar; si no, abrir.
      if (SUMMARY_STATE.openPopover && th.contains(SUMMARY_STATE.openPopover)) {
        closePopover();
      } else {
        openPopover(th, col);
      }
    });
  });
}

function renderSummaryRows() {
  const tbody = document.getElementById("summary-rows");
  const empty = document.getElementById("summary-empty");
  if (!tbody) return;
  let rows = applyFilters(SUMMARY_STATE.rows);
  rows = applySort(rows);
  renderSummaryKpis(rows);
  if (!rows.length) {
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="mono font-medium">${r.cliente}</td>
      <td><span class="chip chip-violet">${r.proyecto}</span></td>
      <td>${estadoChip(r.estado)}</td>
      <td class="mono text-xs">${window.fmtTsShort(r.fecha)}</td>
      <td class="mono text-right">${window.fmtBytes(r.size_cli)}</td>
      <td class="mono text-right text-[var(--muted)]">${window.fmtBytes(r.size_pro)}</td>
    </tr>
  `).join("");
}

function renderSummaryView() {
  const data = window.getAuditTreeData ? window.getAuditTreeData() : null;
  if (!data) {
    document.getElementById("summary-rows").innerHTML =
      `<tr><td colspan="6" class="text-center text-[var(--muted)] py-10">Cargando…</td></tr>`;
    return;
  }
  SUMMARY_STATE.rows = flattenRows(data);
  renderSummaryHeaders();
  renderSummaryRows();
}

// Hook que llama audit_tree.js cuando hay data fresca.
window.onAuditTreeData = () => {
  if (document.getElementById("view-summary") &&
      !document.getElementById("view-summary").classList.contains("hidden")) {
    renderSummaryView();
  } else {
    // pre-compute rows aunque la tab no esté visible, así el switch es instantáneo.
    const data = window.getAuditTreeData ? window.getAuditTreeData() : null;
    if (data) SUMMARY_STATE.rows = flattenRows(data);
  }
};

window.renderSummaryView = renderSummaryView;
