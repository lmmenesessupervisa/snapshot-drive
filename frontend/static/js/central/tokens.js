const cid = document.currentScript.dataset.clientId;

document.getElementById("new-token")?.addEventListener("submit", async e => {
  e.preventDefault();
  const label = e.target.label.value.trim();
  if (!label) return;
  const r = await apiFetch(`/api/admin/clients/${cid}/tokens`, {
    method: "POST",
    body: JSON.stringify({label}),
  });
  const j = await r.json();
  if (j.ok) {
    const out = document.getElementById("new-token-result");
    out.textContent = `TOKEN (guardalo ahora — no se vuelve a mostrar):\n\n${j.data.plaintext}`;
    out.classList.remove("hidden");
    setTimeout(() => location.reload(), 30000);
  } else {
    alert("Error: " + (j.error || "unknown"));
  }
});

document.querySelectorAll(".revoke-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const tid = btn.closest("tr").dataset.tokenId;
    if (!confirm("¿Revocar este token? Las instalaciones que lo usan dejarán de poder reportar.")) return;
    const r = await apiFetch(`/api/admin/tokens/${tid}`, {method: "DELETE"});
    if (r.ok) location.reload();
    else alert("Error revocando");
  });
});
