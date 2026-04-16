async function loadLogs() {
  const n = document.getElementById('lines').value;
  const box = document.getElementById('log-box');
  box.textContent = 'Cargando…';
  try {
    const d = await API.get('/logs?lines=' + encodeURIComponent(n));
    const pretty = (d.lines || '').split('\n').map(line => {
      try {
        const j = JSON.parse(line);
        const color =
          j.level === 'ERROR' ? 'color:#fb7185'
        : j.level === 'WARN'  ? 'color:#fbbf24'
        :                       'color:#94a3b8';
        return `<span style="${color}">[${j.ts ?? ''}] ${j.level ?? ''}</span> ${j.msg ?? ''}`;
      } catch { return line; }
    }).join('\n');
    box.innerHTML = pretty || '<em class="text-slate-500">sin datos</em>';
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = 'Error: ' + e.message;
  }
}
document.getElementById('refresh').onclick = loadLogs;
document.getElementById('lines').onchange   = loadLogs;
loadLogs();
setInterval(loadLogs, 8000);
