// Settings · vinculación Google Drive (OAuth Device Flow + fallback paste)
const $ = (id) => document.getElementById(id);

function renderStatus(s) {
  const ok = (v) => v ? '<span class="text-emerald-400">sí</span>' : '<span class="text-rose-400">no</span>';
  $('drive-status').innerHTML = `
    <div class="grid grid-cols-2 gap-3">
      <div><div class="text-xs text-slate-400">Vinculado</div><div>${ok(s.linked)}</div></div>
      <div><div class="text-xs text-slate-400">Alcanzable</div><div>${ok(s.reachable)}</div></div>
      <div><div class="text-xs text-slate-400">Remote rclone</div><div>${s.remote ?? '—'}</div></div>
      <div><div class="text-xs text-slate-400">Target actual</div>
        <div>${ s.target === 'shared'
                 ? 'Unidad compartida <code class="text-slate-300">' + (s.team_drive || '?') + '</code>'
                 : s.target === 'personal' ? 'Mi unidad personal'
                 : '— sin configurar —' }</div>
      </div>
    </div>`;
  if (s.linked) {
    const r = document.querySelector(`input[name="target"][value="${s.target === 'shared' ? 'shared' : 'personal'}"]`);
    if (r) r.checked = true;
    toggleSharedWrap();
  }
}

async function loadStatus() {
  try { renderStatus(await API.get('/drive/status')); }
  catch (e) { toast(e.message, 'error'); }
}

async function loadSharedDrives() {
  const sel = $('shared-select');
  sel.innerHTML = '<option value="">— cargando —</option>';
  try {
    const list = await API.get('/drive/shared');
    if (!list.length) {
      sel.innerHTML = '<option value="">(no se encontraron unidades compartidas)</option>';
      return;
    }
    sel.innerHTML = list.map(d => `<option value="${d.id}">${d.name} — ${d.id}</option>`).join('');
  } catch (e) {
    sel.innerHTML = `<option value="">error: ${e.message}</option>`;
  }
}

function toggleSharedWrap() {
  const shared = document.querySelector('input[name="target"]:checked').value === 'shared';
  $('shared-wrap').classList.toggle('hidden', !shared);
  if (shared && $('shared-select').options.length <= 1) loadSharedDrives();
}
document.querySelectorAll('input[name="target"]').forEach(r => r.addEventListener('change', toggleSharedWrap));

// ---------- OAuth Device Flow ----------
let oauthState = null; // { deviceCode, interval, expiresAt, timer }

function openOauthModal() { $('oauth-modal').classList.replace('hidden', 'flex'); }
function closeOauthModal() {
  $('oauth-modal').classList.replace('flex', 'hidden');
  if (oauthState && oauthState.timer) clearTimeout(oauthState.timer);
  oauthState = null;
}

async function pollOauth() {
  if (!oauthState) return;
  if (Date.now() > oauthState.expiresAt) {
    $('oauth-hint').textContent = 'El código expiró. Cierra e intenta de nuevo.';
    $('oauth-hint').className = 'text-xs text-rose-400';
    return;
  }
  try {
    const resp = await fetch('/api/drive/oauth/device/poll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_code: oauthState.deviceCode }),
    });
    const body = await resp.json();
    if (resp.status === 202) {
      if (body.data && body.data.slow_down) oauthState.interval += 5;
      oauthState.timer = setTimeout(pollOauth, oauthState.interval * 1000);
      return;
    }
    if (!body.ok) {
      $('oauth-hint').textContent = body.error || 'Error desconocido';
      $('oauth-hint').className = 'text-xs text-rose-400';
      return;
    }
    // éxito
    toast('Cuenta vinculada', 'success');
    closeOauthModal();
    await loadStatus();
  } catch (e) {
    $('oauth-hint').textContent = `Error de red: ${e.message}. Reintentando…`;
    oauthState.timer = setTimeout(pollOauth, oauthState.interval * 1000);
  }
}

$('btn-connect').onclick = async () => {
  try {
    toast('Solicitando código a Google…');
    const data = await API.post('/drive/oauth/device/start', {});
    $('oauth-code').textContent = data.user_code;
    $('oauth-url').textContent = data.verification_url;
    $('oauth-url').href = data.verification_url;
    $('oauth-hint').textContent = 'Esperando autorización…';
    $('oauth-hint').className = 'text-xs text-slate-500';
    oauthState = {
      deviceCode: data.device_code,
      interval: data.interval || 5,
      expiresAt: Date.now() + (data.expires_in || 1800) * 1000,
      timer: null,
    };
    openOauthModal();
    oauthState.timer = setTimeout(pollOauth, oauthState.interval * 1000);
  } catch (e) { toast(e.message, 'error'); }
};

$('oauth-close').onclick = closeOauthModal;
$('oauth-copy').onclick = async () => {
  const ok = await copyText($('oauth-code').textContent);
  toast(ok ? 'Código copiado' : 'No se pudo copiar — selecciona manualmente',
        ok ? 'success' : 'error');
};

// Fallback: token pegado a mano
$('btn-link').onclick = async () => {
  const token = $('token').value.trim();
  if (!token) return toast('Pega el token JSON primero', 'error');
  try {
    toast('Vinculando…');
    await API.post('/drive/link', { token });
    toast('Cuenta vinculada', 'success');
    $('token').value = '';
    await loadStatus();
  } catch (e) { toast(e.message, 'error'); }
};

$('btn-unlink').onclick = async () => {
  if (!confirm('¿Eliminar la vinculación con Google Drive?')) return;
  try {
    await API.post('/drive/unlink');
    toast('Desvinculado', 'success');
    loadStatus();
  } catch (e) { toast(e.message, 'error'); }
};

$('btn-apply-target').onclick = async () => {
  const kind = document.querySelector('input[name="target"]:checked').value;
  const body = { type: kind };
  if (kind === 'shared') {
    const id = $('shared-select').value;
    if (!id) return toast('Selecciona una unidad compartida', 'error');
    body.id = id;
  }
  try {
    toast('Aplicando…');
    await API.post('/drive/target', body);
    toast('Destino actualizado', 'success');
    loadStatus();
  } catch (e) { toast(e.message, 'error'); }
};

$('refresh-status').onclick     = loadStatus;
$('btn-refresh-shared').onclick = loadSharedDrives;

// =============================================================
// Programación (timers systemd)
// =============================================================
const UNIT_LABELS = {
  create: { title: 'Crear snapshot',   desc: 'Cuándo se generan los backups automáticos.' },
  prune:  { title: 'Aplicar retención', desc: 'Cuándo se borran snapshots viejos según la política.' },
};
const WEEKDAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const WEEKDAY_ES = { Mon:'Lunes', Tue:'Martes', Wed:'Miércoles', Thu:'Jueves', Fri:'Viernes', Sat:'Sábado', Sun:'Domingo' };

function fmtNextRun(s) {
  if (!s) return '—';
  // systemctl show devuelve cosas como "Wed 2026-04-15 17:02:21 UTC"
  return s;
}

function unitCardHtml(u) {
  const meta = UNIT_LABELS[u.unit] || { title: u.unit, desc: '' };
  return `
  <div class="rounded-xl border border-[var(--border)] bg-black/20 p-4" data-unit="${u.unit}">
    <div class="flex items-start justify-between gap-3 mb-3">
      <div>
        <div class="font-semibold text-slate-100 flex items-center gap-2">
          ${meta.title}
          <span class="chip ${u.enabled ? 'chip-emerald' : 'chip-slate'}">${u.enabled ? 'activo' : 'pausado'}</span>
        </div>
        <div class="text-[11px] text-[var(--muted)]">${meta.desc}</div>
      </div>
      <label class="flex items-center gap-2 text-xs text-[var(--muted)] cursor-pointer">
        <input type="checkbox" data-role="enabled" ${u.enabled ? 'checked' : ''}>
        habilitado
      </label>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
      <label class="block md:col-span-1">
        <span class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Frecuencia</span>
        <select class="input mt-1" data-role="kind">
          <option value="hourly"  ${u.kind==='hourly'?'selected':''}>Cada hora</option>
          <option value="daily"   ${u.kind==='daily'?'selected':''}>Diario</option>
          <option value="weekly"  ${u.kind==='weekly'?'selected':''}>Semanal</option>
          <option value="monthly" ${u.kind==='monthly'?'selected':''}>Mensual</option>
          <option value="custom"  ${u.kind==='custom'?'selected':''}>Personalizado</option>
        </select>
      </label>
      <label class="block md:col-span-1" data-show-on="daily,weekly,monthly">
        <span class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Hora</span>
        <input type="time" class="input mt-1 mono" data-role="time" value="${u.time || '03:00'}">
      </label>
      <label class="block md:col-span-1" data-show-on="weekly">
        <span class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Día de la semana</span>
        <select class="input mt-1" data-role="weekday">
          ${WEEKDAYS.map(d => `<option value="${d}" ${u.weekday===d?'selected':''}>${WEEKDAY_ES[d]}</option>`).join('')}
        </select>
      </label>
      <label class="block md:col-span-1" data-show-on="monthly">
        <span class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Día del mes</span>
        <input type="number" min="1" max="31" class="input mt-1 mono" data-role="day" value="${u.day || 1}">
      </label>
      <label class="block md:col-span-4" data-show-on="custom">
        <span class="text-[11px] uppercase tracking-wider text-[var(--muted)]">Expresión OnCalendar (systemd)</span>
        <input type="text" class="input mt-1 mono" data-role="oncalendar" placeholder="*-*-* 03:00:00"
               value="${u.kind==='custom' ? (u.oncalendar||'') : ''}">
        <p class="mt-1 text-[11px] text-[var(--muted-2)]">Ej: <code>Mon,Thu *-*-* 02:30:00</code> · <code>*-*-01 04:00:00</code> · <code>hourly</code>.</p>
      </label>
    </div>

    <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted)]">
      <div>
        <span class="text-[var(--muted-2)]">Próxima ejecución:</span>
        <code class="mono text-slate-200" data-role="next">${fmtNextRun(u.next_run)}</code>
        <span class="ml-3 text-[var(--muted-2)]">OnCalendar:</span>
        <code class="mono text-slate-300" data-role="oncal-now">${u.oncalendar || '—'}</code>
      </div>
      <button class="btn-primary" data-role="save">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        Guardar
      </button>
    </div>
  </div>`;
}

function applyKindVisibility(card) {
  const kind = card.querySelector('[data-role="kind"]').value;
  card.querySelectorAll('[data-show-on]').forEach(el => {
    const show = el.dataset.showOn.split(',').includes(kind);
    el.classList.toggle('hidden', !show);
  });
}

function bindUnitCard(card) {
  applyKindVisibility(card);
  card.querySelector('[data-role="kind"]').addEventListener('change', () => applyKindVisibility(card));
  card.querySelector('[data-role="save"]').addEventListener('click', async () => {
    const unit = card.dataset.unit;
    const get = (r) => card.querySelector(`[data-role="${r}"]`);
    const payload = {
      kind:       get('kind').value,
      time:       get('time').value,
      weekday:    get('weekday').value,
      day:        parseInt(get('day').value || '1', 10),
      oncalendar: get('oncalendar') ? get('oncalendar').value : '',
      enabled:    get('enabled').checked,
    };
    try {
      await API.post('/schedule/' + unit, payload);
      toast(`Programación de "${unit}" actualizada`, 'success');
      // Re-render completo para refrescar el chip activo/pausado y la
      // próxima ejecución tras el daemon-reload.
      loadSchedules();
    } catch (e) { toast(e.message, 'error'); }
  });
}

async function loadSchedules() {
  const wrap = $('schedule-units');
  wrap.innerHTML = '<div class="text-[var(--muted)] text-xs">Cargando…</div>';
  try {
    const data = await API.get('/schedule');
    if (!data.units?.length) {
      wrap.innerHTML = '<div class="text-[var(--muted)] text-xs">No hay timers configurados.</div>';
      return;
    }
    wrap.innerHTML = data.units.map(unitCardHtml).join('');
    wrap.querySelectorAll('[data-unit]').forEach(bindUnitCard);
  } catch (e) {
    wrap.innerHTML = `<div class="text-rose-400 text-xs">${e.message}</div>`;
  }
}

$('refresh-schedule').onclick = loadSchedules;

// =============================================================
// Retención
// =============================================================
async function loadRetention() {
  try {
    const r = await API.get('/retention');
    $('ret-daily').value   = r.keep_daily   ?? 7;
    $('ret-weekly').value  = r.keep_weekly  ?? 4;
    $('ret-monthly').value = r.keep_monthly ?? 6;
    $('ret-yearly').value  = r.keep_yearly  ?? 2;
  } catch (e) { toast(e.message, 'error'); }
}

$('btn-save-retention').onclick = async () => {
  const payload = {
    keep_daily:   parseInt($('ret-daily').value   || '0', 10),
    keep_weekly:  parseInt($('ret-weekly').value  || '0', 10),
    keep_monthly: parseInt($('ret-monthly').value || '0', 10),
    keep_yearly:  parseInt($('ret-yearly').value  || '0', 10),
  };
  if (Object.values(payload).every(v => v === 0)) {
    if (!confirm('Vas a poner toda la retención a 0. Esto borrará TODOS los snapshots al ejecutar prune. ¿Continuar?')) return;
  }
  try {
    const r = await API.post('/retention', payload);
    toast('Retención actualizada', 'success');
    $('ret-daily').value   = r.keep_daily;
    $('ret-weekly').value  = r.keep_weekly;
    $('ret-monthly').value = r.keep_monthly;
    $('ret-yearly').value  = r.keep_yearly;
  } catch (e) { toast(e.message, 'error'); }
};

$('refresh-retention').onclick = loadRetention;

// =============================================================
// Rutas a respaldar + carpeta Drive
// =============================================================
async function loadConfig() {
  try {
    const r = await API.get('/config');
    $('cfg-paths').value = (r.backup_paths_list || []).join('\n');
    $('cfg-remote-path').value = r.rclone_remote_path || '';
    $('cfg-remote-effective').textContent = r.rclone_remote_path_effective || '—';
    // Si el valor guardado usa ${HOSTNAME}, mostramos hint explicando la expansión
    if ((r.rclone_remote_path || '').includes('${HOSTNAME}')) {
      $('cfg-hostname-hint').innerHTML =
        ` — <code class="mono">\${HOSTNAME}</code> se reemplaza por <code class="mono">${r.hostname}</code>`;
    } else {
      $('cfg-hostname-hint').textContent = '';
    }
  } catch (e) { toast(e.message, 'error'); }
}

$('btn-save-config').onclick = async () => {
  const rawPaths = $('cfg-paths').value.trim();
  const remote   = $('cfg-remote-path').value.trim();
  if (!rawPaths) return toast('Añade al menos una ruta', 'error');
  if (!remote)   return toast('La carpeta de Drive no puede estar vacía', 'error');

  // Split por líneas/espacios para mandar array (es más limpio que string)
  const paths = rawPaths.split(/\s+/).filter(Boolean);

  const prevEffective = $('cfg-remote-effective').textContent.trim();
  if (remote !== prevEffective && prevEffective && prevEffective !== '—') {
    if (!confirm(
      `Vas a cambiar la carpeta de Drive:\n\n  ${prevEffective}  →  ${remote}\n\n` +
      `restic creará un repositorio NUEVO en la ruta nueva. Los snapshots ` +
      `existentes en "${prevEffective}" seguirán en tu Drive pero el panel no ` +
      `los verá hasta que restaures el valor anterior. ¿Continuar?`
    )) return;
  }

  try {
    await API.post('/config', { backup_paths: paths, rclone_remote_path: remote });
    toast('Configuración guardada', 'success');
    loadConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('refresh-config').onclick = loadConfig;

// =============================================================
// Backup mensual (archive cold-storage)
// =============================================================
const ARCH_STATE = { cfg: null };

function populateSelect(el, values, selected) {
  el.innerHTML = values.map(v => `<option value="${v}" ${v===selected?'selected':''}>${v}</option>`).join('');
}

function refreshArchivePathPreview() {
  const p = $('arch-proyecto').value;
  const e = $('arch-entorno').value;
  const c = $('arch-pais').value;
  const n = $('arch-nombre').value.trim() || (ARCH_STATE.cfg?.hostname || '<hostname>');
  if (!p || !e || !c) {
    $('arch-path-preview').textContent = '(configura los campos de arriba)';
    return;
  }
  const ext = (ARCH_STATE.cfg?.password_set) ? 'tar.zst.enc' : 'tar.zst';
  const now = new Date();
  const pad = (x,w=2) => String(x).padStart(w,'0');
  const ts = `${now.getUTCFullYear()}${pad(now.getUTCMonth()+1)}${pad(now.getUTCDate())}_${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;
  $('arch-path-preview').textContent =
    `${p}/${e}/${c}/os/linux/${n}/${now.getUTCFullYear()}/${pad(now.getUTCMonth()+1)}/${pad(now.getUTCDate())}/servidor_${n}_${ts}.${ext}`;
}

function renderPasswordState(cfg) {
  const el = $('arch-pwd-state');
  if (cfg.password_set) {
    el.textContent = 'configurada (encriptación activa)';
    el.className = 'font-mono ml-1 text-emerald-300';
  } else {
    el.textContent = 'sin configurar (archivos subidos SIN encriptación)';
    el.className = 'font-mono ml-1 text-amber-300';
  }
}

async function loadArchiveConfig() {
  try {
    const cfg = await API.get('/archive/config');
    ARCH_STATE.cfg = cfg;
    populateSelect($('arch-proyecto'), [''].concat(cfg.valid_proyectos), cfg.proyecto);
    populateSelect($('arch-entorno'),  [''].concat(cfg.valid_entornos),  cfg.entorno);
    populateSelect($('arch-pais'),     [''].concat(cfg.valid_paises),    cfg.pais);
    $('arch-nombre').value = (cfg.nombre && cfg.nombre !== cfg.hostname) ? cfg.nombre : '';
    $('arch-nombre').placeholder = `(por defecto: ${cfg.hostname})`;
    $('arch-keep').value = cfg.keep_months || 12;
    renderPasswordState(cfg);
    refreshArchivePathPreview();
  } catch (e) { toast(e.message, 'error'); }
}

['arch-proyecto','arch-entorno','arch-pais','arch-nombre'].forEach(id =>
  $(id).addEventListener('input', refreshArchivePathPreview)
);

$('btn-save-archive').onclick = async () => {
  const payload = {
    proyecto: $('arch-proyecto').value,
    entorno:  $('arch-entorno').value,
    pais:     $('arch-pais').value,
    nombre:   $('arch-nombre').value.trim(),
    keep_months: parseInt($('arch-keep').value || '12', 10),
  };
  try {
    toast('Guardando taxonomía…');
    await API.post('/archive/config', payload);
    toast('Taxonomía guardada', 'success');
    await loadArchiveConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('btn-save-archive-pwd').onclick = async () => {
  const pw = $('arch-pwd').value;
  const co = $('arch-pwd-confirm').value;
  if (!pw) return toast('La contraseña no puede estar vacía (usa "Quitar encriptación" si quieres desactivar)', 'error');
  if (pw !== co) return toast('La confirmación no coincide', 'error');
  if (pw.length < 8) return toast('Mínimo 8 caracteres', 'error');
  try {
    toast('Guardando contraseña…');
    await API.post('/archive/password', { password: pw, confirm: co });
    toast('Contraseña actualizada', 'success');
    $('arch-pwd').value = '';
    $('arch-pwd-confirm').value = '';
    await loadArchiveConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('btn-clear-archive-pwd').onclick = async () => {
  if (!confirm('Quitar la encriptación significa que los próximos archivos se subirán en CLARO a Drive. Los archivos viejos encriptados seguirán necesitando la password anterior para descifrarse. ¿Continuar?')) return;
  try {
    await fetch('/api/archive/password', { method: 'DELETE', credentials: 'same-origin' });
    toast('Encriptación desactivada', 'success');
    $('arch-pwd').value = '';
    $('arch-pwd-confirm').value = '';
    await loadArchiveConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('arch-pwd-show').addEventListener('change', (e) => {
  const t = e.target.checked ? 'text' : 'password';
  $('arch-pwd').type = t;
  $('arch-pwd-confirm').type = t;
});

$('refresh-archive').onclick = loadArchiveConfig;

loadStatus();
loadSchedules();
loadRetention();
loadConfig();
loadArchiveConfig();
