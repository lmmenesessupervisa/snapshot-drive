document.getElementById("new-client")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await apiFetch("/api/admin/clients", {
    method: "POST",
    body: JSON.stringify({
      proyecto: fd.get("proyecto"),
      organizacion: fd.get("organizacion") || null,
    }),
  });
  if (r.ok) location.reload();
  else alert("Error: " + (await r.text()));
});
