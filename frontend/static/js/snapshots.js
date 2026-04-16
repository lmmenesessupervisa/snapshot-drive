// Gestión de snapshots
function sourceBadge(src) {
  if (src === 'drive') return '<span class="chip chip-sky">Drive</span>';
  if (src === 'local') return '<span class="chip chip-amber">Local · pendiente</span>';
  return '<span class="text-[var(--muted-2)]">—</span>';
}

async function loadSnaps(fresh = false) {
  const tb = document.getElementById('snap-body');
  // Skeleton: 3 filas grises animadas — mejor que "Cargando…" plano.
  tb.innerHTML = Array.from({length: 3}).map(() =>
    `<tr class="animate-pulse">
       <td><div class="h-3 w-20 bg-slate-800/80 rounded"></div></td>
       <td><div class="h-4 w-16 bg-slate-800/80 rounded-full"></div></td>
       <td><div class="h-3 w-32 bg-slate-800/80 rounded"></div></td>
       <td><div class="h-3 w-24 bg-slate-800/80 rounded"></div></td>
       <td><div class="h-3 w-16 bg-slate-800/80 rounded"></div></td>
       <td><div class="h-3 w-40 bg-slate-800/80 rounded"></div></td>
       <td><div class="h-3 w-20 bg-slate-800/80 rounded ml-auto"></div></td>
     </tr>`
  ).join('') +
  `<tr><td colspan="7" class="px-5 py-3 text-center text-[var(--muted-2)] text-xs">
     Consultando snapshots en Drive…
   </td></tr>`;
  try {
    const list = await API.get('/snapshots' + (fresh ? '?fresh=1' : ''));
    if (!list.length) {
      tb.innerHTML = '<tr><td colspan="7" class="px-5 py-10 text-center text-[var(--muted)]">Aún no hay snapshots. Pulsa <b>Crear snapshot</b> para empezar.</td></tr>';
      return;
    }
    tb.innerHTML = list.map(s => {
      const id = (s.short_id || s.id || '').slice(0, 12);
      const full = s.id || '';
      const tags = (s.tags || []).map(t => `<span class="chip chip-slate">${t}</span>`).join(' ') || '<span class="text-[var(--muted-2)]">—</span>';
      const paths = (s.paths || []).join(', ');
      return `<tr>
        <td class="mono text-xs text-slate-200">${id}</td>
        <td>${sourceBadge(s.source)}</td>
        <td class="text-slate-300">${fmtDate(s.time)}</td>
        <td class="text-slate-300">${s.hostname || '—'}</td>
        <td>${tags}</td>
        <td class="text-[var(--muted)] truncate max-w-xs mono text-xs" title="${paths}">${paths}</td>
        <td class="text-right whitespace-nowrap space-x-1">
          <button class="btn-secondary !py-1.5 !px-3 text-xs" data-act="restore" data-id="${full}">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
            Restaurar
          </button>
          <button class="btn-danger !py-1.5 !px-3 text-xs" data-act="delete" data-id="${full}">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
            Eliminar
          </button>
        </td>
      </tr>`;
    }).join('');
    bindRowActions();
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="7" class="px-5 py-10 text-center text-rose-400">${e.message}</td></tr>`;
  }
}

function bindRowActions() {
  document.querySelectorAll('[data-act="delete"]').forEach(b => {
    b.onclick = async () => {
      const id = b.dataset.id;
      if (!confirm(`¿Eliminar snapshot ${id.slice(0,12)}? Esta acción no se puede deshacer.\n\nNota: el espacio se recupera al ejecutar "Aplicar retención" en el dashboard.`)) return;
      const end = busyStart('Eliminando snapshot…');
      try {
        await API.del('/snapshots/' + encodeURIComponent(id));
        toast('Snapshot eliminado', 'success');
        loadSnaps(true);
      } catch (e) {
        const msg = (e.message || '').toLowerCase();
        if (msg.includes('locked') || msg.includes('lock')) {
          toast('Repositorio bloqueado. Ve al dashboard y usa "Forzar desbloqueo".', 'error');
        } else {
          toast(e.message, 'error');
        }
      } finally { end(); }
    };
  });
  document.querySelectorAll('[data-act="restore"]').forEach(b => {
    b.onclick = () => openRestore(b.dataset.id);
  });
}

// ---- Modal restore ----
function openRestore(id) {
  document.getElementById('r-id').value = id;
  document.getElementById('r-target').value = '/var/lib/snapshot-v3/restore';
  document.getElementById('r-include').value = '';
  document.getElementById('modal-restore').classList.remove('hidden');
}
document.getElementById('r-cancel').onclick = () => document.getElementById('modal-restore').classList.add('hidden');
document.getElementById('r-go').onclick = async () => {
  const payload = {
    id:      document.getElementById('r-id').value,
    target:  document.getElementById('r-target').value,
    include: document.getElementById('r-include').value || undefined,
  };
  document.getElementById('modal-restore').classList.add('hidden');
  const end = busyStart('Restaurando snapshot…');
  try {
    await API.post('/restore', payload);
    toast('Restore OK', 'success');
  } catch (e) { toast(e.message, 'error'); }
  finally { end(); }
};

document.getElementById('btn-refresh').onclick = () => loadSnaps(true);
document.getElementById('btn-create').onclick  = async () => {
  const end = busyStart('Creando snapshot…');
  try {
    await API.post('/snapshots', { tag: 'manual' });
    toast('Snapshot creado', 'success');
    loadSnaps();
  } catch (e) {
    const msg = (e.message || '').toLowerCase();
    if (msg.includes('already locked')) {
      toast('Repositorio bloqueado. Ve al dashboard y usa "Forzar desbloqueo".', 'error');
    } else {
      toast(e.message, 'error');
    }
  } finally { end(); }
};

loadSnaps();
