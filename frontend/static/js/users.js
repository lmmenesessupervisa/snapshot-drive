// /users page — admin-only CRUD over /auth/users.
// Loaded with `defer`, so apiFetch (auth.js) is guaranteed to be ready.

let CURRENT_USER_ID = null;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function roleChip(role) {
  const cls = role === "admin" ? "chip-violet"
            : role === "operator" ? "chip-sky"
            : "chip-slate";
  return `<span class="chip ${cls}">${escapeHtml(role)}</span>`;
}

function mfaCell(u) {
  // Tres estados visibles: enrolado, no enrolado, off (mfa_disabled).
  if (u.mfa_disabled) {
    return '<span class="chip chip-slate" title="Login no pide TOTP ni enroll">MFA off</span>';
  }
  if (u.mfa_enrolled) return '<span class="chip chip-emerald">enrolado</span>';
  return '<span class="chip chip-slate">no</span>';
}

function statusChip(status) {
  return status === "active"
    ? '<span class="chip chip-emerald">activo</span>'
    : '<span class="chip chip-rose">deshab.</span>';
}

async function loadMe() {
  try {
    const r = await apiFetch("/auth/me");
    const j = await r.json();
    if (j && j.ok && typeof j.id === "number") CURRENT_USER_ID = j.id;
  } catch { /* ignore */ }
}

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
      // Stash usuario completo para que el handler "Editar" lo recupere
      // sin re-fetchear.
      tr.dataset.userId = u.id;
      tr.dataset.userJson = JSON.stringify(u);
      tr.innerHTML = `
        <td class="mono">${escapeHtml(u.email)}</td>
        <td>${escapeHtml(u.display_name)}</td>
        <td>${roleChip(u.role)}</td>
        <td>${mfaCell(u)}</td>
        <td>${statusChip(u.status)}</td>
        <td class="text-xs whitespace-nowrap">${escapeHtml(u.last_login_at || '—')}</td>
        <td class="text-right whitespace-nowrap">
          <details class="row-actions relative inline-block">
            <summary class="btn-secondary text-xs cursor-pointer select-none">Acciones ▾</summary>
            <div class="row-actions-panel">
              <button data-id="${u.id}" data-act="edit" class="row-actions-item">Editar</button>
              <hr class="border-[var(--border)] my-1">
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
    tb.innerHTML = `<tr><td colspan="7" class="text-center text-rose-500">Error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ---------- Edit dialog ----------
function openEditDialog(user) {
  const dlg = document.getElementById("dlg-edit");
  const form = document.getElementById("form-edit");
  form.elements.id.value = user.id;
  form.elements.email.value = user.email || "";
  form.elements.display_name.value = user.display_name || "";
  form.elements.role.value = user.role || "operator";
  form.elements.mfa_disabled.checked = !!user.mfa_disabled;

  const isSelf = (CURRENT_USER_ID !== null && CURRENT_USER_ID === user.id);
  form.elements.role.disabled = isSelf;
  form.elements.mfa_disabled.disabled = isSelf;
  document.getElementById("edit-role-self-hint").classList.toggle("hidden", !isSelf);

  document.getElementById("edit-msg").classList.add("hidden");
  dlg.showModal();
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  const act = b.dataset.act;
  const menu = b.closest("details.row-actions");
  if (menu) menu.removeAttribute("open");

  if (act === "edit") {
    const tr = b.closest("tr[data-user-id]");
    const user = JSON.parse(tr.dataset.userJson);
    openEditDialog(user);
    return;
  }

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

document.getElementById("cancel-edit").addEventListener("click",
    () => document.getElementById("dlg-edit").close());

document.getElementById("form-edit").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const id = form.elements.id.value;
  const isSelf = (CURRENT_USER_ID !== null && CURRENT_USER_ID === Number(id));
  const body = {
    email: form.elements.email.value.trim().toLowerCase(),
    display_name: form.elements.display_name.value.trim(),
  };
  if (!isSelf) {
    body.role = form.elements.role.value;
    body.mfa_disabled = form.elements.mfa_disabled.checked;
  }
  const r = await apiFetch(`/auth/users/${id}`, {
    method: "POST", body: JSON.stringify(body),
  });
  const j = await r.json();
  if (r.ok && j.ok) {
    document.getElementById("dlg-edit").close();
    load();
  } else {
    const p = document.getElementById("edit-msg");
    p.textContent = j.error || "Error";
    p.classList.remove("hidden");
  }
});

// ---------- Create dialog ----------
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

(async () => {
  await loadMe();
  load();
})();
