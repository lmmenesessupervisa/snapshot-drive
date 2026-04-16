// Dashboard: KPIs + acciones rápidas + jobs
// Estado interno: si hay operación en curso, pausamos polling para no saturar.
let _busyActive = false;
// Último estado conocido de "Drive alcanzable". Lo mantenemos porque el
// auto-refresh rápido no pingea Drive (devuelve null) y sería confuso
// mostrar "—" durante 2 minutos entre checks reales.
let _lastDriveReachable = null;
let _lastDeepCheck = 0;
async function loadStatus(opts = {}) {
  const qs = [];
  if (opts.deep)  qs.push('deep=1');
  if (opts.fresh) qs.push('fresh=1');
  const path = '/status' + (qs.length ? '?' + qs.join('&') : '');
  try {
    const s = await API.get(path);
    const driveUnknown = s.drive?.unknown === true;
    const driveCount = driveUnknown ? null : (s.drive?.snapshot_count ?? 0);
    const localCount = s.local?.snapshot_count ?? 0;
    const pending    = s.pending_reconcile ?? localCount;

    const driveCountEl = document.getElementById('kpi-drive-count');
    if (driveUnknown) {
      driveCountEl.textContent = '—';
      driveCountEl.title = 'Pulsa "Refrescar" para consultar Drive';
    } else {
      driveCountEl.textContent = driveCount;
      driveCountEl.title = '';
    }
    const pendEl = document.getElementById('kpi-pending');
    pendEl.textContent = pending;
    pendEl.className = 'mt-2 text-3xl font-semibold ' + (pending > 0 ? 'text-amber-400' : 'text-emerald-400');

    // Si el backend devolvió un valor real (true/false), es el nuevo estado
    // conocido. Si devolvió null (modo rápido), preservamos el anterior.
    if (s.drive_reachable === true || s.drive_reachable === false) {
      _lastDriveReachable = s.drive_reachable;
      _lastDeepCheck = Date.now();
    }
    const effectiveReachable = s.drive_reachable ?? _lastDriveReachable;
    const stale = s.drive_reachable === null;
    const staleTitle = stale && _lastDeepCheck
      ? `Último chequeo real: hace ${Math.round((Date.now() - _lastDeepCheck) / 1000)}s`
      : '';

    const okEl = document.getElementById('kpi-drive-ok');
    if (!s.drive_linked) {
      okEl.textContent = 'sin vincular';
      okEl.className = 'mt-2 text-3xl font-semibold text-slate-400';
      okEl.title = '';
    } else if (effectiveReachable === true) {
      okEl.textContent = 'sí';
      okEl.className = 'mt-2 text-3xl font-semibold text-emerald-400';
      okEl.title = staleTitle;
    } else if (effectiveReachable === false) {
      okEl.textContent = 'no';
      okEl.className = 'mt-2 text-3xl font-semibold text-rose-400';
      okEl.title = staleTitle;
    } else {
      okEl.textContent = '—';
      okEl.className = 'mt-2 text-3xl font-semibold text-slate-400';
      okEl.title = 'Aún no comprobado';
    }

    document.getElementById('kpi-size').textContent = fmtBytes(s.local?.size_bytes ?? 0);

    const lastDrive = s.drive?.last_snapshot;
    const lastLocal = s.local?.last_snapshot;
    const lastAny   = s.last_snapshot || lastDrive || lastLocal;
    document.getElementById('last-when').textContent = fmtDate(lastAny);
    document.getElementById('last-detail').innerHTML =
      `<div class="grid grid-cols-2 gap-4">
         <div><div class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Host</div><div class="mono mt-0.5">${s.host ?? '—'}</div></div>
         <div><div class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Último en Drive</div><div class="mono mt-0.5">${fmtDate(lastDrive)}</div></div>
         <div><div class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Último en local</div><div class="mono mt-0.5">${fmtDate(lastLocal)}</div></div>
         <div><div class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Destino Drive</div>
           <div class="mt-0.5">${ s.drive_target === 'shared' ? 'Unidad compartida <code class="mono text-brand-400">' + (s.drive_team_drive || '?') + '</code>'
                 : s.drive_target === 'personal' ? 'Mi unidad personal'
                 : '<span class="text-[var(--muted-2)]">— no configurado —</span>' }</div></div>
       </div>
       ${ pending > 0 ? `<div class="mt-5 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex items-start gap-3">
          <svg class="w-5 h-5 flex-none text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
          <div>Hay <b>${pending}</b> snapshot(s) en buffer local. Usa <b>Reconciliar ahora</b> (o espera al timer automático) para subirlos a Drive y liberar disco.</div>
       </div>` : '' }`;
  } catch (e) {
    toast('Status: ' + e.message, 'error');
  }
}

async function loadJobs() {
  try {
    const rows = await API.get('/jobs?limit=20');
    const tb = document.getElementById('jobs-body');
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="6" class="px-5 py-8 text-center text-[var(--muted)]">Sin jobs todavía.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => {
      const chip =
        r.status === 'ok'      ? 'chip chip-emerald'
      : r.status === 'running' ? 'chip chip-amber'
      :                          'chip chip-rose';
      return `<tr>
        <td class="mono text-xs text-slate-300">${r.id}</td>
        <td class="text-slate-200">${r.kind}</td>
        <td><span class="${chip}">${r.status}</span></td>
        <td class="mono text-slate-300">${r.rc ?? '—'}</td>
        <td class="text-[var(--muted)] mono text-xs">${r.started_at ?? ''}</td>
        <td class="text-[var(--muted)] mono text-xs">${r.finished_at ?? ''}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    toast('Jobs: ' + e.message, 'error');
  }
}

async function runEstimate() {
  const modal = document.getElementById('modal-estimate');
  const body  = document.getElementById('estimate-body');
  body.innerHTML = '<div class="text-[var(--muted)]">Calculando… (dry-run contra Drive puede tardar ~30s).</div>';
  modal.classList.remove('hidden');
  try {
    const d = await API.get('/estimate?fresh=1');
    const rawHtml = d.raw_bytes != null ? fmtBytes(d.raw_bytes) : '—';
    const upHtml  = d.estimated_upload_bytes != null
      ? `<span class="text-emerald-400 font-semibold">${fmtBytes(d.estimated_upload_bytes)}</span>`
      : `<span class="text-amber-400">primer snapshot · subirá ~tamaño bruto (comprimido)</span>`;
    const files = d.estimated_changed_files != null
      ? `${d.estimated_changed_files} / ${d.total_files_processed ?? '—'}`
      : '—';
    const repo = d.repo_queried
      ? `<code class="mono text-brand-400">${d.repo_queried}</code>`
      : '<span class="text-[var(--muted-2)]">sin repo — solo tamaño bruto</span>';
    body.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="text-[var(--muted)]">Tamaño bruto (en disco)</span>
        <span class="mono">${rawHtml}</span>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-[var(--muted)]">A subir a Drive (estimado)</span>
        <span class="mono">${upHtml}</span>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-[var(--muted)]">Archivos nuevos/modificados</span>
        <span class="mono">${files}</span>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-[var(--muted)]">Repo consultado</span>
        <span>${repo}</span>
      </div>
      <div class="mt-3 p-3 rounded-lg bg-slate-800/60 border border-[var(--border)] text-[11px] text-[var(--muted)]">
        Rutas: <code class="mono">${(d.paths || []).join(' ')}</code>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="text-rose-400">${e.message}</div>`;
  }
}
document.getElementById('est-close').onclick =
  () => document.getElementById('modal-estimate').classList.add('hidden');

async function runAction(act) {
  if (act === 'estimate') return runEstimate();
  const map = {
    create:          { path: '/snapshots',     method: 'post', body: { tag: 'manual' }, label: 'Crear snapshot', busy: 'Creando snapshot…' },
    reconcile:       { path: '/reconcile',     method: 'post', label: 'Reconciliación', busy: 'Subiendo pendientes a Drive y liberando disco…' },
    check:           { path: '/check',         method: 'post', label: 'Verificación',   busy: 'Verificando integridad del repositorio…' },
    prune:           { path: '/prune',         method: 'post', label: 'Retención',      busy: 'Aplicando política de retención…' },
    'cleanup-local': { path: '/cleanup-local', method: 'post', label: 'Limpieza buffer local', busy: 'Liberando paquetes huérfanos del repo local…' },
    unlock:          { path: '/unlock',        method: 'post', label: 'Desbloqueo',     busy: 'Liberando locks del repositorio…' },
  };
  const cfg = map[act];
  if (!cfg) return;
  if (act === 'prune' && !confirm('¿Aplicar retención y prune? Operación irreversible.')) return;
  if (act === 'unlock' && !confirm('Forzar desbloqueo del repositorio. Úsalo SOLO si estás seguro de que no hay otra operación corriendo. ¿Continuar?')) return;

  const btns = document.querySelectorAll('[data-act]');
  btns.forEach(b => b.disabled = true);
  _busyActive = true;
  const end = busyStart(cfg.busy);
  try {
    const res = await API[cfg.method](cfg.path, cfg.body);
    // cleanup-local: interpretar el stdout para decirle al usuario si
    // de verdad liberó algo o si debe reconciliar primero.
    if (act === 'cleanup-local') {
      const out = (res && res.stdout) || '';
      // restic imprime "this removes: N blobs / X B"
      const m = out.match(/this removes:\s+(\d+)\s+blobs\s*\/\s*([\d.]+\s*[KMGT]?i?B|0\s*B)/i);
      const removed = m ? `${m[2]} (${m[1]} blobs)` : null;
      const kept = out.match(/finding data that is still in use for (\d+) snapshots?/i);
      const keptN = kept ? parseInt(kept[1], 10) : 0;
      if (!removed || /^\s*0\s*(B|blobs)/i.test(removed)) {
        if (keptN > 0) {
          toast(`Limpieza: 0 B liberados. Hay ${keptN} snapshot(s) vivos ocupando el buffer. Para vaciarlo: "Reconciliar ahora" (sube a Drive) o elimina los snapshots.`, 'info');
        } else {
          toast('Limpieza: 0 B liberados. El buffer ya estaba limpio.', 'info');
        }
      } else {
        toast(`Limpieza: liberados ${removed}`, 'success');
      }
    } else {
      toast(`${cfg.label}: OK`, 'success');
    }
    loadStatus({ deep: true, fresh: true }); loadJobs();
  } catch (e) {
    // Detectar repo bloqueado y sugerir desbloqueo
    const msg = (e.message || '').toLowerCase();
    if (msg.includes('already locked') || msg.includes('repository is already locked')) {
      toast(`${cfg.label}: repositorio bloqueado. Usa "Forzar desbloqueo" y reintenta.`, 'error');
    } else {
      toast(`${cfg.label}: ${e.message}`, 'error');
    }
  } finally {
    end();
    btns.forEach(b => b.disabled = false);
    _busyActive = false;
  }
}

document.querySelectorAll('[data-act]').forEach(b =>
  b.addEventListener('click', () => runAction(b.dataset.act))
);
document.getElementById('refresh-jobs').addEventListener('click', loadJobs);

// Cargar las rutas configuradas para mostrarlas en la tarjeta "Crear snapshot".
// Evita que quede el texto hardcodeado cuando el usuario cambia BACKUP_PATHS
// desde Ajustes.
(async () => {
  try {
    const cfg = await API.get('/config');
    const el = document.getElementById('create-paths-hint');
    if (el) el.textContent = (cfg.backup_paths_list || []).join(' ') || '—';
  } catch { /* silencioso: la acción sigue funcionando con el texto por defecto */ }
})();

// Carga inicial: una pasada deep (con cache 60s invalida al crear/reconciliar)
// para mostrar conteos reales de Drive la primera vez.
loadStatus({ deep: true });
loadJobs();

// Auto-refresh: evita disparar si hay operación activa o si la pestaña no
// está visible. Cada 30s hace un check "fast" (barato, no pingea Drive);
// si han pasado >2 min desde el último chequeo real de Drive, usamos
// "deep" para refrescar de verdad el estado de alcanzabilidad.
setInterval(() => {
  if (_busyActive) return;
  if (document.hidden) return;
  const needsDeep = !_lastDeepCheck || (Date.now() - _lastDeepCheck) > 120000;
  loadStatus(needsDeep ? { deep: true } : {});
}, 30000);
setInterval(() => {
  if (_busyActive) return;
  if (document.hidden) return;
  loadJobs();
}, 15000);

// Botón manual "refrescar" (si existiera en tu base.html) o atajos:
// Shift+click en cualquier acción deshabilitada → fuerza deep
window.refreshDeep = () => loadStatus({ deep: true, fresh: true });
