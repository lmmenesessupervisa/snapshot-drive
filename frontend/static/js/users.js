// /users page — admin-only CRUD over /auth/users.
// Loaded with `defer`, so apiFetch (auth.js) is guaranteed to be ready.

async function load() {
  const tb = document.getElementById("rows");
  try {
    const r = await apiFetch("/auth/users");
    const j = await r.json();
    tb.innerHTML = "";
    if (!j.users || !j.users.length) {
      tb.innerHTML = '<tr><td colspan="7" class="text-center text-[var(--muted)]">sin usuarios</td></tr>';
      return;
    }
    j.users.forEach((u) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono">${u.email}</td>
        <td>${u.display_name}</td>
        <td><span class="chip ${u.role === 'admin' ? 'chip-violet' : u.role === 'operator' ? 'chip-sky' : 'chip-slate'}">${u.role}</span></td>
        <td>${u.mfa_enrolled ? '<span class="chip chip-emerald">enrolado</span>' : '<span class="chip chip-slate">no</span>'}</td>
        <td>${u.status === 'active' ? '<span class="chip chip-emerald">activo</span>' : '<span class="chip chip-rose">deshab.</span>'}</td>
        <td class="text-xs">${u.last_login_at || '—'}</td>
        <td class="text-right">
          <details class="row-actions relative inline-block">
            <summary class="btn-secondary text-xs cursor-pointer select-none">Acciones ▾</summary>
            <div class="row-actions-panel">
              <button data-id="${u.id}" data-act="reset-password" class="row-actions-item">Reset contraseña</button>
              <button data-id="${u.id}" data-act="revoke-sessions" class="row-actions-item">Cerrar sesiones</button>
              <button data-id="${u.id}" data-act="reset-mfa" class="row-actions-item">Reset MFA</button>
              <hr class="border-[var(--border)] my-1">
              <button data-id="${u.id}" data-act="${u.status === 'active' ? 'disable' : 'enable'}"
                      class="row-actions-item ${u.status === 'active' ? 'row-actions-item-danger' : ''}">
                ${u.status === 'active' ? 'Deshabilitar' : 'Habilitar'}
              </button>
            </div>
          </details>
        </td>`;
      tb.appendChild(tr);
    });
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="7" class="text-center text-rose-500">Error: ${e.message}</td></tr>`;
  }
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  const act = b.dataset.act;
  // Cerrar el menú abierto al ejecutar la acción
  const menu = b.closest("details.row-actions");
  if (menu) menu.removeAttribute("open");
  if (act === 'reset-password' && !confirm("¿Generar nueva contraseña?")) return;
  if (act === 'reset-mfa' && !confirm("¿Resetear MFA del usuario?")) return;
  if (act === 'revoke-sessions' && !confirm("¿Cerrar todas las sesiones?")) return;
  if (act === 'disable' && !confirm("¿Deshabilitar cuenta?")) return;
  const r = await apiFetch(`/auth/users/${id}/${act}`, { method: "POST" });
  const j = await r.json();
  if (j.temp_password) {
    alert(`Contraseña temporal:\n\n${j.temp_password}\n\nCopiala — no se vuelve a mostrar.`);
  }
  load();
});

document.getElementById("btn-create").addEventListener("click",
    () => document.getElementById("dlg-create").showModal());
document.getElementById("cancel-create").addEventListener("click",
    () => document.getElementById("dlg-create").close());

document.getElementById("form-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  if (!data.password) delete data.password;
  const r = await apiFetch("/auth/users", {
    method: "POST", body: JSON.stringify(data),
  });
  const j = await r.json();
  if (r.ok) {
    if (j.initial_password) {
      alert(`Usuario creado.\nContraseña inicial:\n\n${j.initial_password}\n\nCopiala — no se vuelve a mostrar.`);
    }
    document.getElementById("dlg-create").close();
    e.target.reset();
    load();
  } else {
    const p = document.getElementById("create-msg");
    p.textContent = j.error || "Error";
    p.classList.remove("hidden");
  }
});

// Cerrar cualquier menú "Acciones" abierto al hacer click fuera de él.
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.row-actions[open]").forEach((d) => {
    if (!d.contains(e.target)) d.removeAttribute("open");
  });
});

load();
