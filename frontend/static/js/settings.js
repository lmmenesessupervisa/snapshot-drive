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

// --- DB backups (sub-E) ---
async function loadDbConfig() {
  try {
    const c = await API.get('/db-archive/config');
    $('db-targets').value = c.targets || '';
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

$('btn-save-db').onclick = async () => {
  const body = {
    targets: $('db-targets').value.trim(),
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
    toast('Configuración DB guardada', 'success');
    $('db-pg-pwd').value = ''; $('db-pg-pwd-clear').checked = false;
    $('db-mysql-pwd').value = ''; $('db-mysql-pwd-clear').checked = false;
    $('db-mongo-uri').value = ''; $('db-mongo-uri-clear').checked = false;
    loadDbConfig();
  } catch (e) { toast(e.message, 'error'); }
};

// --- Crypto / age (sub-F) ---
async function loadCryptoConfig() {
  try {
    const c = await API.get('/crypto/config');
    $('crypto-recipients').value = c.recipients || '';
    const chip = $('crypto-mode');
    const mode = c.active_mode || 'none';
    chip.textContent = ({ age: 'age (público/privado)', openssl: 'openssl + password', none: 'sin cifrado' })[mode] || mode;
    chip.className = 'chip ' + ({ age: 'chip-emerald', openssl: 'chip-amber', none: 'chip-slate' })[mode];
  } catch (e) { toast(e.message, 'error'); }
}

$('btn-save-crypto').onclick = async () => {
  try {
    await API.post('/crypto/config', { recipients: $('crypto-recipients').value });
    toast('Recipients guardados', 'success');
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
  toast('Pública agregada al campo. Pulsá Guardar recipients para persistir.', 'info');
  $('dlg-keypair').close();
};

// --- boot ---
loadStatus();
loadConfig();
loadArchiveConfig();
loadDbConfig();
loadCryptoConfig();
