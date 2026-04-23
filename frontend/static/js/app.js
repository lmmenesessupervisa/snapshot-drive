// Cliente compartido: fetch + toasts + helpers
const API = {
  async req(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch('/api' + path, opts);
    let j = {};
    try { j = await r.json(); } catch { /* ignore */ }
    if (!r.ok || j.ok === false) {
      const msg = j.error || ('HTTP ' + r.status);
      throw new Error(msg + (j.detail ? ' — ' + j.detail.slice(0, 200) : ''));
    }
    return j.data;
  },
  get:  (p)     => API.req('GET', p),
  post: (p, b)  => API.req('POST', p, b || {}),
  del:  (p)     => API.req('DELETE', p),
};

// Nota: usa inline styles en lugar de clases Tailwind para evitar que los
// overrides del tema claro dejen el texto ilegible (text-slate-100 se
// remapea a foreground oscuro y chocaba con el fondo oscuro del toast).
function toast(msg, kind = 'info') {
  const el = document.getElementById('toast');
  if (!el) return alert(msg);
  const palette = {
    info:    { bg: '#0f172a', fg: '#ffffff' },  // slate-900
    success: { bg: '#059669', fg: '#ffffff' },  // emerald-600
    error:   { bg: '#dc2626', fg: '#ffffff' },  // red-600
    warn:    { bg: '#b45309', fg: '#ffffff' },  // amber-700
  };
  const p = palette[kind] || palette.info;
  el.className = 'px-4 py-2 rounded-lg text-sm font-medium';
  el.style.background = p.bg;
  el.style.color = p.fg;
  el.style.border = '1px solid rgba(0,0,0,.15)';
  el.style.boxShadow = '0 4px 16px rgba(15,23,42,.18)';
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(el._toastTimer);
  el._toastTimer = setTimeout(() => el.classList.add('hidden'), 4000);
}

function fmtBytes(n) {
  if (!n || isNaN(n)) return '0 B';
  const u = ['B','KB','MB','GB','TB','PB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + ' ' + u[i];
}

function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return s; }
}

// Copia texto al portapapeles con fallback para contextos no seguros
// (el panel corre sobre http://<IP-LAN>, donde navigator.clipboard falla).
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* cae al fallback */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '-1000px';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

// ---------- Overlay de operación en curso ----------
// Usar: const end = busyStart('Creando snapshot…'); try { … } finally { end(); }
let _busyTimer = null;
function busyStart(label) {
  const ov = document.getElementById('busy-overlay');
  if (!ov) return () => {};
  document.getElementById('busy-label').textContent = label || 'Trabajando…';
  document.getElementById('busy-elapsed').textContent = '0s';
  ov.classList.remove('hidden');
  ov.classList.add('flex');
  const started = Date.now();
  if (_busyTimer) clearInterval(_busyTimer);
  _busyTimer = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    const mm = Math.floor(s / 60), ss = s % 60;
    document.getElementById('busy-elapsed').textContent =
      mm ? `${mm}m ${ss}s` : `${s}s`;
  }, 1000);
  return () => {
    if (_busyTimer) { clearInterval(_busyTimer); _busyTimer = null; }
    ov.classList.add('hidden');
    ov.classList.remove('flex');
  };
}

// ---------- Cache por sessionStorage con TTL ----------
// Objetivo: navegar entre vistas NO debe disparar fetchs al backend si los
// datos son recientes. Cada vista llama a `cachedFetch(key, endpoint, ttlMs)`,
// que muestra los datos cacheados (si hay) al instante y en paralelo
// refresca en background para actualizar el storage. Sobre page reload, el
// cache desaparece (sessionStorage persiste solo mientras dura la pestaña).
const Cache = {
  get(key, ttlMs) {
    try {
      const raw = sessionStorage.getItem('cache:' + key);
      if (!raw) return null;
      const { ts, data } = JSON.parse(raw);
      if (ttlMs != null && Date.now() - ts > ttlMs) return null;
      return data;
    } catch { return null; }
  },
  set(key, data) {
    try {
      sessionStorage.setItem('cache:' + key, JSON.stringify({ ts: Date.now(), data }));
    } catch { /* quota lleno — OK */ }
  },
  invalidate(prefix) {
    Object.keys(sessionStorage)
      .filter(k => k.startsWith('cache:' + prefix))
      .forEach(k => sessionStorage.removeItem(k));
  },
};

// Muestra datos cacheados instantáneo + refresca en background.
//   key:       string identificador ('archive:summary')
//   endpoint:  '/archive/summary'
//   ttlMs:     ventana en que consideramos el cache 'fresco'
//   onData:    (data, isStale) => void — se llama primero con cache (si hay),
//              luego con los datos frescos cuando llegan.
async function cachedFetch(key, endpoint, ttlMs, onData) {
  const cached = Cache.get(key, null);   // ignora TTL para mostrar algo al instante
  if (cached) onData(cached, /*stale=*/true);

  const fresh = Cache.get(key, ttlMs);   // ¿sigue dentro del TTL? → no hace falta refetch
  if (fresh && cached) return fresh;

  try {
    const data = await API.get(endpoint);
    Cache.set(key, data);
    onData(data, false);
    return data;
  } catch (e) {
    if (!cached) throw e;
    console.warn('cachedFetch:', endpoint, e.message);
    return cached;
  }
}

// Auto-refresh consciente de visibilidad: pausa cuando la pestaña está oculta.
function autoRefresh(fn, intervalMs) {
  let t = null;
  const start = () => { if (!t && !document.hidden) t = setInterval(fn, intervalMs); };
  const stop  = () => { if (t) { clearInterval(t); t = null; } };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else { fn(); start(); }     // al volver, refresca inmediatamente
  });
  start();
  return stop;
}

// Indicador de salud del API en sidebar
(async () => {
  const el  = document.getElementById('api-status');
  const dot = document.getElementById('api-dot');
  if (!el) return;
  try {
    await API.get('/health');
    el.textContent = 'online';
    el.className = 'text-emerald-400';
    if (dot) dot.className = 'api-dot ok';
  } catch {
    el.textContent = 'offline';
    el.className = 'text-rose-400';
    if (dot) dot.className = 'api-dot down';
  }
})();
