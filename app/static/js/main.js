/**
 * Leash — shared JS utilities and dashboard logic.
 */
window.Leash = (() => {

  // ── Toast helper ────────────────────────────────────────────────────────
  function toast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const id = `toast-${Date.now()}`;
    const colours = { success: 'bg-success', danger: 'bg-danger', warning: 'bg-warning text-dark', info: 'bg-info text-dark' };
    const bg = colours[type] || 'bg-secondary';
    container.insertAdjacentHTML('beforeend', `
      <div id="${id}" class="toast align-items-center text-white ${bg} border-0" role="alert">
        <div class="d-flex">
          <div class="toast-body">${message}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
      </div>
    `);
    const el = document.getElementById(id);
    const t = new bootstrap.Toast(el, { delay: 3500 });
    t.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  }

  // ── Source select change → set source ───────────────────────────────────
  async function setSource(receiverId, sourceName) {
    if (!sourceName) return;
    const resp = await fetch(`/api/receivers/${receiverId}/source`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_name: sourceName }),
    });
    const data = await resp.json();
    if (resp.ok && data.status === 200) {
      toast(`Source set: ${sourceName}`, 'success');
    } else {
      toast(`Failed to set source (HTTP ${data.status ?? resp.status})`, 'danger');
    }
  }

  // ── Poll a single receiver's status ─────────────────────────────────────
  async function pollReceiver(receiverId) {
    const resp = await fetch(`/api/receivers/${receiverId}/status`);
    if (!resp.ok) return null;
    const data = await resp.json();
    updateReceiverCard(data);
    return data;
  }

  // ── Update a receiver card in the grid ──────────────────────────────────
  function updateReceiverCard(data) {
    const card = document.querySelector(`.receiver-card[data-receiver-id="${data.id}"]`);
    if (!card) return;

    // Border colour
    card.classList.remove('border-success', 'border-danger', 'border-secondary');
    if (data.status === 'online')  card.classList.add('border-success');
    else if (data.status === 'offline') card.classList.add('border-danger');
    else card.classList.add('border-secondary');

    // Status dot
    const dot = card.querySelector('.status-dot');
    if (dot) {
      dot.className = `status-dot status-${data.status}`;
    }

    // Hostname
    const hn = card.querySelector('.hostname-display');
    if (hn) hn.textContent = data.hostname || '—';

    // Source select — update selection without wiping options
    const sel = card.querySelector('.source-select');
    if (sel && data.current_source) {
      // Add option if not present
      if (!Array.from(sel.options).some(o => o.value === data.current_source)) {
        const opt = new Option(data.current_source, data.current_source);
        sel.insertBefore(opt, sel.options[1]);
      }
      sel.value = data.current_source;
    }
  }

  // ── Rebuild source dropdowns after discovery ─────────────────────────────
  function refreshSourceDropdowns(sourceNames) {
    document.querySelectorAll('.source-select').forEach(sel => {
      const currentVal = sel.value;
      // Keep first placeholder and last Reboot options, replace middle
      while (sel.options.length > 1) sel.remove(1);
      sourceNames.forEach(name => {
        sel.add(new Option(name, name));
      });
      sel.add(new Option('⚡ Reboot Device', 'Reboot'));
      if (currentVal) sel.value = currentVal;
    });
  }

  // ── Bulk reload all receivers ────────────────────────────────────────────
  async function bulkReload() {
    const btn = document.getElementById('btn-bulk-reload');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Reloading…'; }

    document.querySelectorAll('.receiver-card').forEach(c => c.classList.add('reloading'));

    try {
      const resp = await fetch('/api/receivers/bulk-reload');
      if (!resp.ok) { toast('Bulk reload failed', 'danger'); return; }
      const receivers = await resp.json();
      receivers.forEach(updateReceiverCard);
      const online = receivers.filter(r => r.status === 'online').length;
      toast(`Reload complete — ${online}/${receivers.length} online`, 'info');
    } finally {
      document.querySelectorAll('.receiver-card').forEach(c => c.classList.remove('reloading'));
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Reload All'; }
    }
  }

  // ── Discover NDI sources ─────────────────────────────────────────────────
  async function discoverSources() {
    const btn = document.getElementById('btn-discover');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Discovering…'; }

    try {
      const resp = await fetch('/api/sources/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const data = await resp.json();
      if (!resp.ok) { toast(data.error || 'Discovery failed', 'danger'); return; }

      const names = (data.sources || []).map(s => s.name);
      refreshSourceDropdowns(names);
      if (window.LEASH) window.LEASH.sources = names;

      toast(`Discovery done — ${data.added?.length ?? 0} new, ${data.updated?.length ?? 0} updated`, 'success');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-radar me-1"></i>Discover Sources'; }
    }
  }

  // ── Reboot / restart helpers ─────────────────────────────────────────────
  async function rebootReceiver(receiverId) {
    if (!confirm('Reboot this device?')) return;
    const resp = await fetch(`/api/receivers/${receiverId}/reboot`, { method: 'POST' });
    const data = await resp.json();
    toast(data.status === 200 ? 'Reboot initiated' : `Reboot failed (${data.status})`,
          data.status === 200 ? 'warning' : 'danger');
  }

  async function restartReceiver(receiverId) {
    const resp = await fetch(`/api/receivers/${receiverId}/restart`, { method: 'POST' });
    const data = await resp.json();
    toast(data.status === 200 ? 'Video restart initiated' : `Restart failed (${data.status})`,
          data.status === 200 ? 'info' : 'danger');
  }

  // ── Add receiver ─────────────────────────────────────────────────────────
  async function addReceiver() {
    const index  = document.getElementById('add-index')?.value;
    const octet  = document.getElementById('add-octet')?.value;
    const label  = document.getElementById('add-label')?.value;

    if (!index || !octet) { toast('Index and IP octet are required', 'warning'); return; }

    const resp = await fetch('/api/receivers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: parseInt(index), ip_last_octet: octet, label }),
    });
    const data = await resp.json();
    if (resp.ok) {
      toast(`Receiver ${index} added`, 'success');
      setTimeout(() => location.reload(), 800);
    } else {
      toast(data.error || 'Failed to add receiver', 'danger');
    }
  }

  // ── Bind dashboard events ────────────────────────────────────────────────
  function bindDashboard() {
    document.getElementById('btn-bulk-reload')?.addEventListener('click', bulkReload);
    document.getElementById('btn-discover')?.addEventListener('click', discoverSources);
    document.getElementById('btn-add-receiver')?.addEventListener('click', addReceiver);

    // Source selects
    document.querySelectorAll('.source-select').forEach(sel => {
      sel.addEventListener('change', () => {
        const rid = sel.dataset.receiverId;
        const val = sel.value;
        if (val) setSource(rid, val);
      });
    });

    // Per-card buttons
    document.querySelectorAll('.btn-poll').forEach(btn => {
      btn.addEventListener('click', () => pollReceiver(btn.dataset.receiverId));
    });
    document.querySelectorAll('.btn-reboot').forEach(btn => {
      btn.addEventListener('click', () => rebootReceiver(btn.dataset.receiverId));
    });
    document.querySelectorAll('.btn-restart').forEach(btn => {
      btn.addEventListener('click', () => restartReceiver(btn.dataset.receiverId));
    });
  }

  document.addEventListener('DOMContentLoaded', bindDashboard);

  return { toast, pollReceiver, bulkReload, discoverSources, setSource, rebootReceiver, restartReceiver };
})();
