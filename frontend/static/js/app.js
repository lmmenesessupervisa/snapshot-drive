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

function toast(msg, kind = 'info') {
  const el = document.getElementById('toast');
  if (!el) return alert(msg);
  const palette = {
    info:    'bg-slate-800/90 text-slate-100 border border-slate-700',
    success: 'bg-emerald-600/90 text-white border border-emerald-400/40',
    error:   'bg-rose-600/90 text-white border border-rose-400/40',
  };
  el.className = 'px-4 py-2 rounded-lg text-sm font-medium ' + (palette[kind] || palette.info);
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
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
