document.querySelectorAll(".ack-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const aid = btn.closest("tr").dataset.alertId;
    if (!confirm("¿Marcar esta alerta como resuelta? El admin debe haber verificado la causa raíz.")) return;
    const r = await apiFetch(`/api/admin/alerts/${aid}/acknowledge`, {method: "POST"});
    if (r.ok) location.reload();
    else alert("Error al hacer acknowledge");
  });
});
