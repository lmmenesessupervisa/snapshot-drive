// Settings — vinculación Drive + rutas + archive mensual.

const $ = (id) => document.getElementById(id);

// ---------- Drive status ----------
function renderStatus(s) {
  const ok = (v) => v ? '<span class="text-emerald-400">sí</span>'
                      : '<span class="text-rose-400">no</span>';
  $('drive-status').innerHTML = `
    <div class="grid grid-cols-2 gap-3">
      <div><div class="text-xs text-[var(--muted)]">Vinculado</div><div>${ok(s.linked)}</div></div>
      <div><div class="text-xs text-[var(--muted)]">Alcanzable</div><div>${ok(s.reachable)}</div></div>
      <div><div class="text-xs text-[var(--muted)]">Remote rclone</div><div class="mono">${s.remote ?? '—'}</div></div>
      <div><div class="text-xs text-[var(--muted)]">Target actual</div>
        <div>${ s.target === 'shared'
                 ? 'Unidad compartida <code class="mono">' + (s.team_drive || '?') + '</code>'
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
let oauthState = null;

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
    const resp = await apiFetch('/api/drive/oauth/device/poll', {
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
    $('oauth-hint').className = 'text-xs text-[var(--muted)]';
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

// ---------- Rutas a respaldar (BACKUP_PATHS) ----------
async function loadConfig() {
  try {
    const r = await API.get('/config');
    $('cfg-paths').value = (r.backup_paths_list || []).join('\n');
  } catch (e) { toast(e.message, 'error'); }
}

$('btn-save-config').onclick = async () => {
  const rawPaths = $('cfg-paths').value.trim();
  if (!rawPaths) return toast('Añade al menos una ruta', 'error');
  const paths = rawPaths.split(/\s+/).filter(Boolean);
  try {
    await API.post('/config', { backup_paths: paths });
    toast('Rutas guardadas', 'success');
    loadConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('refresh-config').onclick = loadConfig;

// ---------- Backup mensual (archive) ----------
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
  // El sufijo depende del cifrado activo (age > openssl > none).
  const cryptoMode = (ARCH_STATE.cryptoMode) || 'none';
  const ext = ({ age: 'tar.zst.age', openssl: 'tar.zst.enc', none: 'tar.zst' })[cryptoMode];
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
    el.className = 'font-mono ml-1';
    el.style.color = 'var(--success)';
  } else {
    el.textContent = 'sin configurar (archivos subidos SIN encriptación)';
    el.className = 'font-mono ml-1';
    el.style.color = 'var(--warn)';
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
  if (!confirm('Quitar la encriptación significa que los próximos archivos se subirán en CLARO a Drive. Los archivos viejos encriptados seguirán necesitando la password anterior. ¿Continuar?')) return;
  try {
    await apiFetch('/api/archive/password', { method: 'DELETE', credentials: 'same-origin' });
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

// --- DB backups (sub-E) — distribuye DB_BACKUP_TARGETS en toggles por engine ---
function parseTargets(raw) {
  const groups = { postgres: [], mysql: [], mongo: [] };
  for (const tok of (raw || '').split(/\s+/).filter(Boolean)) {
    const [eng, db] = tok.split(':', 2);
    if (groups[eng] && db) groups[eng].push(db);
  }
  return groups;
}

function buildTargets(pgList, myList, mongoList) {
  const mk = (eng, list) => list.filter(Boolean).map(db => `${eng}:${db}`);
  return [...mk('postgres', pgList), ...mk('mysql', myList), ...mk('mongo', mongoList)].join(' ');
}

function dbsFromTextarea(id) {
  return $(id).value.split(/[\s\n,]+/).map(s => s.trim()).filter(Boolean);
}

function setEngineEnabled(engine, on, dbs) {
  $(`db-${engine}-on`).checked = !!on;
  $(`db-${engine}-fields`).classList.toggle('hidden', !on);
  if (dbs && dbs.length) $(`db-${engine}-dbs`).value = dbs.join('\n');
}

async function loadDbConfig() {
  try {
    const c = await API.get('/db-archive/config');
    const groups = parseTargets(c.targets);
    setEngineEnabled('pg', groups.postgres.length > 0, groups.postgres);
    setEngineEnabled('mysql', groups.mysql.length > 0, groups.mysql);
    setEngineEnabled('mongo', groups.mongo.length > 0, groups.mongo);
    $('db-pg-host').value = c.pg_host || '';
    $('db-pg-port').value = c.pg_port || '5432';
    $('db-pg-user').value = c.pg_user || '';
    $('db-pg-pwd-state').textContent = c.pg_password_set ? '(guardada)' : '(no configurada)';
    $('db-mysql-host').value = c.mysql_host || '';
    $('db-mysql-port').value = c.mysql_port || '3306';
    $('db-mysql-user').value = c.mysql_user || '';
    $('db-mysql-pwd-state').textContent = c.mysql_password_set ? '(guardada)' : '(no configurada)';
    $('db-mongo-uri-state').textContent = c.mongo_uri_set ? '(guardada)' : '(no configurada)';
  } catch (e) { toast(e.message, 'error'); }
}

['pg', 'mysql', 'mongo'].forEach(eng => {
  if ($(`db-${eng}-on`)) {
    $(`db-${eng}-on`).addEventListener('change', (e) => {
      $(`db-${eng}-fields`).classList.toggle('hidden', !e.target.checked);
    });
  }
});

$('btn-save-db').onclick = async () => {
  const pgDbs    = $('db-pg-on').checked    ? dbsFromTextarea('db-pg-dbs')    : [];
  const mysqlDbs = $('db-mysql-on').checked ? dbsFromTextarea('db-mysql-dbs') : [];
  const mongoDbs = $('db-mongo-on').checked ? dbsFromTextarea('db-mongo-dbs') : [];
  const targets = buildTargets(pgDbs, mysqlDbs, mongoDbs);
  const body = {
    targets,
    pg_host: $('db-pg-host').value.trim(),
    pg_port: $('db-pg-port').value.trim(),
    pg_user: $('db-pg-user').value.trim(),
    pg_password: $('db-pg-pwd').value,
    clear_pg_password: $('db-pg-pwd-clear').checked,
    mysql_host: $('db-mysql-host').value.trim(),
    mysql_port: $('db-mysql-port').value.trim(),
    mysql_user: $('db-mysql-user').value.trim(),
    mysql_password: $('db-mysql-pwd').value,
    clear_mysql_password: $('db-mysql-pwd-clear').checked,
    mongo_uri: $('db-mongo-uri').value.trim(),
    clear_mongo_uri: $('db-mongo-uri-clear').checked,
  };
  try {
    await API.post('/db-archive/config', body);
    toast(targets ? 'Configuración DB guardada — el timer @db-archive queda activo' : 'DB backups desactivados (sin engines)', 'success');
    $('db-pg-pwd').value = ''; $('db-pg-pwd-clear').checked = false;
    $('db-mysql-pwd').value = ''; $('db-mysql-pwd-clear').checked = false;
    $('db-mongo-uri').value = ''; $('db-mongo-uri-clear').checked = false;
    loadDbConfig();
    loadScheduler();
  } catch (e) { toast(e.message, 'error'); }
};

// --- Cifrado unificado (openssl + age) ---
function setCryptoMode(mode) {
  document.querySelectorAll('input[name="crypto-mode-sel"]').forEach(r => {
    r.checked = (r.value === mode);
  });
  $('crypto-age-block').classList.toggle('hidden', mode !== 'age');
  $('crypto-openssl-block').classList.toggle('hidden', mode !== 'openssl');
  $('crypto-none-block').classList.toggle('hidden', mode !== 'none');
}

document.querySelectorAll('input[name="crypto-mode-sel"]').forEach(r => {
  r.addEventListener('change', (e) => setCryptoMode(e.target.value));
});

async function loadCryptoConfig() {
  try {
    const c = await API.get('/crypto/config');
    $('crypto-recipients').value = c.recipients || '';
    const chip = $('crypto-mode');
    const mode = c.active_mode || 'none';
    chip.textContent = ({ age: 'age (clave pública/privada)', openssl: 'password (openssl)', none: 'sin cifrado' })[mode] || mode;
    chip.className = 'chip ml-1 ' + ({ age: 'chip-emerald', openssl: 'chip-amber', none: 'chip-slate' })[mode];
    setCryptoMode(mode);
    // Reflejar password openssl state
    $('arch-pwd-state').textContent = c.openssl_password_set ? '(configurada)' : '(no configurada)';
    // Pasar el modo al preview de la ruta para que el sufijo sea exacto.
    ARCH_STATE.cryptoMode = mode;
    refreshArchivePathPreview();
  } catch (e) { toast(e.message, 'error'); }
}

$('btn-save-crypto').onclick = async () => {
  try {
    if (!$('crypto-recipients').value.trim()) {
      if (!confirm('¿Activar age sin recipients? Sin recipients age no cifra (cae a openssl o sin cifrado).')) return;
    }
    await API.post('/crypto/config', { recipients: $('crypto-recipients').value });
    toast('Cifrado age activado', 'success');
    loadCryptoConfig();
  } catch (e) { toast(e.message, 'error'); }
};

$('btn-keygen').onclick = async () => {
  if (!confirm('¿Generar un nuevo keypair age? El privado se mostrará una sola vez.')) return;
  try {
    const r = await API.post('/crypto/keygen', {});
    $('kp-pub').value = r.public;
    $('kp-priv').value = r.private;
    $('dlg-keypair').showModal();
  } catch (e) { toast(e.message, 'error'); }
};

$('kp-close').onclick = () => $('dlg-keypair').close();
$('kp-append').onclick = () => {
  const cur = $('crypto-recipients').value.trim();
  const pub = $('kp-pub').value.trim();
  if (!pub) return;
  $('crypto-recipients').value = (cur ? cur + ' ' : '') + pub;
  toast('Pública agregada. Pulsá Activar cifrado age para persistir.', 'info');
  $('dlg-keypair').close();
};

// --- Scheduler de timers ---
const SCHED_LABELS = {
  archive:      { name: 'Mensual del sistema', desc: 'Archive cold-storage de las rutas configuradas (.tar.zst). Default: día 1 de cada mes a las 02:00 UTC.' },
  'db-archive': { name: 'Bases de datos',      desc: 'Dump de cada engine activado. Default: todos los días a las 03:00 UTC. Solo se ejecuta si hay engines configurados arriba.' },
  reconcile:    { name: 'Reconcile',           desc: 'Sube al Drive lo que quedó local en buffer (cuando rclone falla). Default: semanal.' },
  prune:        { name: 'Retención (prune)',   desc: 'Borra archives más viejos que ARCHIVE_KEEP_MONTHS. Default: deshabilitado.' },
  create:       { name: 'Snapshot incremental local (legacy)', desc: 'restic create — solo si usás snapshots incrementales en disco local.' },
};

function schedRowHtml(s) {
  const meta = SCHED_LABELS[s.unit] || { name: s.unit, desc: '' };
  const next = s.next_run ? `próximo: <span class="mono">${s.next_run}</span>` : 'sin próximo run';
  return `
    <div class="sched-row" data-unit="${s.unit}">
      <div>
        <div class="sched-row-name">${meta.name}</div>
        <div class="sched-row-desc">${meta.desc}</div>
        <div class="sched-row-fields">
          <label class="block">
            <span class="text-[10px] uppercase tracking-wider text-[var(--muted)]">Frecuencia</span>
            <select class="input mt-1 sched-kind">
              <option value="daily">Diario</option>
              <option value="weekly">Semanal</option>
              <option value="monthly">Mensual</option>
              <option value="custom">Custom (OnCalendar)</option>
            </select>
          </label>
          <label class="block sched-time-wrap">
            <span class="text-[10px] uppercase tracking-wider text-[var(--muted)]">Hora (UTC, HH:MM)</span>
            <input type="time" class="input mt-1 mono sched-time">
          </label>
          <label class="block sched-weekday-wrap hidden">
            <span class="text-[10px] uppercase tracking-wider text-[var(--muted)]">Día (semanal)</span>
            <select class="input mt-1 sched-weekday">
              <option value="Mon">Lunes</option><option value="Tue">Martes</option>
              <option value="Wed">Miércoles</option><option value="Thu">Jueves</option>
              <option value="Fri">Viernes</option><option value="Sat">Sábado</option>
              <option value="Sun">Domingo</option>
            </select>
          </label>
          <label class="block sched-day-wrap hidden">
            <span class="text-[10px] uppercase tracking-wider text-[var(--muted)]">Día del mes</span>
            <input type="number" min="1" max="31" class="input mt-1 mono sched-day">
          </label>
          <label class="block sched-custom-wrap hidden">
            <span class="text-[10px] uppercase tracking-wider text-[var(--muted)]">OnCalendar</span>
            <input type="text" class="input mt-1 mono sched-oncalendar" placeholder="*-*-* 03:00:00">
          </label>
        </div>
      </div>
      <div class="sched-row-actions">
        <label class="inline-flex items-center gap-2 text-xs">
          <input type="checkbox" class="sched-enabled accent-[var(--primary)]"> Habilitado
        </label>
        <button class="btn-primary text-xs sched-save">Guardar</button>
        <div class="sched-next">${next}</div>
      </div>
    </div>
  `;
}

function setupSchedRow(row, s) {
  row.querySelector('.sched-kind').value = s.kind || 'monthly';
  row.querySelector('.sched-time').value = s.time || '03:00';
  row.querySelector('.sched-weekday').value = s.weekday || 'Mon';
  row.querySelector('.sched-day').value = s.day || 1;
  row.querySelector('.sched-oncalendar').value = s.oncalendar || '';
  row.querySelector('.sched-enabled').checked = !!s.enabled;
  applyKindVisibility(row);
}

function applyKindVisibility(row) {
  const kind = row.querySelector('.sched-kind').value;
  row.querySelector('.sched-weekday-wrap').classList.toggle('hidden', kind !== 'weekly');
  row.querySelector('.sched-day-wrap').classList.toggle('hidden', kind !== 'monthly');
  row.querySelector('.sched-time-wrap').classList.toggle('hidden', kind === 'custom');
  row.querySelector('.sched-custom-wrap').classList.toggle('hidden', kind !== 'custom');
}

async function loadScheduler() {
  if (!$('sched-rows')) return;
  try {
    const list = await API.get('/scheduler/list');
    // Mostrar solo los más relevantes para el operador.
    const order = ['archive', 'db-archive', 'reconcile', 'prune'];
    const items = list.filter(s => order.includes(s.unit))
                      .sort((a, b) => order.indexOf(a.unit) - order.indexOf(b.unit));
    $('sched-rows').innerHTML = items.map(schedRowHtml).join('');
    items.forEach(s => {
      const row = document.querySelector(`.sched-row[data-unit="${s.unit}"]`);
      setupSchedRow(row, s);
      row.querySelector('.sched-kind').addEventListener('change', () => applyKindVisibility(row));
      row.querySelector('.sched-save').addEventListener('click', () => saveSchedule(s.unit, row));
    });
  } catch (e) {
    $('sched-rows').innerHTML = `<div class="text-xs text-rose-500">Error cargando schedule: ${e.message}</div>`;
  }
}

async function saveSchedule(unit, row) {
  const body = {
    kind: row.querySelector('.sched-kind').value,
    time: row.querySelector('.sched-time').value,
    weekday: row.querySelector('.sched-weekday').value,
    day: parseInt(row.querySelector('.sched-day').value, 10) || 1,
    oncalendar: row.querySelector('.sched-oncalendar').value,
    enabled: row.querySelector('.sched-enabled').checked,
  };
  try {
    const sched = await API.post(`/scheduler/${unit}`, body);
    toast(`${unit}: ${sched.enabled ? 'activado' : 'desactivado'}`, 'success');
    loadScheduler();
  } catch (e) { toast(e.message, 'error'); }
}

// --- Alerts (sub-D) ---
async function loadAlertsConfig() {
  if (!$('btn-save-alerts')) return;  // no-central deploy: section absent
  try {
    // Use raw fetch — alerts endpoints live under /api/admin/alerts, not /api/.
    const r = await fetch('/api/admin/alerts/config', {
      headers: { 'X-CSRF-Token': API._csrf() },
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const c = j.data || {};
    $('al-hours').value = c.no_heartbeat_hours ?? 48;
    $('al-shrink').value = c.shrink_pct ?? 20;
    $('al-email').value = c.email || '';
    $('al-webhook-state').textContent = c.webhook_set ? '(guardado)' : '(no configurado)';
  } catch (e) { toast(e.message, 'error'); }
}

if ($('btn-save-alerts')) {
  $('btn-save-alerts').onclick = async () => {
    const body = {
      no_heartbeat_hours: parseInt($('al-hours').value, 10),
      shrink_pct: parseInt($('al-shrink').value, 10),
      email: $('al-email').value.trim(),
      clear_email: $('al-email-clear').checked,
      webhook: $('al-webhook').value.trim(),
      clear_webhook: $('al-webhook-clear').checked,
    };
    try {
      const r = await fetch('/api/admin/alerts/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': API._csrf() },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
      toast('Alertas guardadas', 'success');
      $('al-webhook').value = ''; $('al-webhook-clear').checked = false;
      $('al-email-clear').checked = false;
      loadAlertsConfig();
    } catch (e) { toast(e.message, 'error'); }
  };
}

// --- boot ---
loadStatus();
loadConfig();
loadArchiveConfig();
loadDbConfig();
loadCryptoConfig();
loadAlertsConfig();
loadScheduler();
