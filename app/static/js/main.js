/**
 * Leash — shared JS utilities and dashboard logic.
 */
window.Leash = (() => {

  // ── Toast helper ────────────────────────────────────────────────────────
  // Message content is inserted with textContent to prevent XSS from any
  // server- or device-supplied string passed in (hostnames, source names,
  // error messages etc).
  function toast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const colours = {
      success: 'bg-success',
      danger:  'bg-danger',
      warning: 'bg-warning text-dark',
      info:    'bg-info text-dark',
    };
    const bg = colours[type] || 'bg-secondary';

    const el = document.createElement('div');
    el.className = `toast align-items-center text-white ${bg} border-0`;
    el.setAttribute('role', 'alert');

    const wrap = document.createElement('div');
    wrap.className = 'd-flex';
    const body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = String(message);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-close btn-close-white me-2 m-auto';
    btn.setAttribute('data-bs-dismiss', 'toast');
    wrap.appendChild(body);
    wrap.appendChild(btn);
    el.appendChild(wrap);
    container.appendChild(el);

    const t = new bootstrap.Toast(el, { delay: 4000 });
    t.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  }

  // ── Update a single receiver card ───────────────────────────────────────
  function updateReceiverCard(data) {
    const card = document.querySelector(`.receiver-card[data-receiver-id="${data.id}"]`);
    if (!card) return;

    // Border + col class
    const col = document.getElementById(`col-${data.id}`);
    card.classList.remove('border-success', 'border-danger', 'border-secondary');
    if (col) col.classList.remove('col-offline');

    if (data.status === 'online') {
      card.classList.add('border-success');
    } else if (data.status === 'offline') {
      card.classList.add('border-danger');
      if (col) col.classList.add('col-offline');
    } else {
      card.classList.add('border-secondary');
    }

    // Status dot
    const dot = card.querySelector('.status-dot');
    if (dot) dot.className = `status-dot status-${data.status}`;

    // Hostname / label
    const hn = card.querySelector('.hostname-display');
    if (hn) hn.textContent = data.hostname || data.label || `Player ${data.ip_last_octet}`;

    // Firmware
    const fw = card.querySelector('.firmware-display');
    if (fw) fw.textContent = data.firmware_version || '—';

    // Source select
    const sel = card.querySelector('.source-select');
    if (sel) {
      sel.disabled = data.status === 'offline';
      if (data.current_source) {
        if (!Array.from(sel.options).some(o => o.value === data.current_source)) {
          sel.insertBefore(new Option(data.current_source, data.current_source), sel.options[1]);
        }
        sel.value = data.current_source;
      }
    }
  }

  // ── Update the summary badges ───────────────────────────────────────────
  function updateSummary(receivers) {
    const total   = receivers.length;
    const online  = receivers.filter(r => r.status === 'online').length;
    const offline = receivers.filter(r => r.status === 'offline').length;
    const tc = document.getElementById('total-count');
    const oc = document.getElementById('online-count');
    if (tc) tc.textContent = total;
    if (oc) oc.textContent = `${online} online${offline ? ` / ${offline} offline` : ''}`;
  }

  // ── Rebuild source dropdowns ────────────────────────────────────────────
  function refreshSourceDropdowns(sourceNames) {
    document.querySelectorAll('.source-select').forEach(sel => {
      const cur = sel.value;
      while (sel.options.length > 1) sel.remove(1);
      sourceNames.forEach(n => sel.add(new Option(n, n)));
      sel.add(new Option('⚡ Reboot Device', 'Reboot'));
      if (cur) sel.value = cur;
    });
  }

  // ── Set NDI source on a receiver ────────────────────────────────────────
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

  // ── Poll a single receiver ───────────────────────────────────────────────
  async function pollReceiver(receiverId) {
    const resp = await fetch(`/api/receivers/${receiverId}/status`);
    if (!resp.ok) return null;
    const data = await resp.json();
    updateReceiverCard(data);
    return data;
  }

  // ── Bulk reload ─────────────────────────────────────────────────────────
  async function bulkReload() {
    const btn = document.getElementById('btn-bulk-reload');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Reloading…'; }
    document.querySelectorAll('.receiver-card').forEach(c => c.classList.add('reloading'));

    try {
      const resp = await fetch('/api/receivers/bulk-reload');
      if (!resp.ok) { toast('Bulk reload failed', 'danger'); return; }
      const receivers = await resp.json();
      receivers.forEach(updateReceiverCard);
      updateSummary(receivers);
      const online = receivers.filter(r => r.status === 'online').length;
      toast(`Reload complete — ${online}/${receivers.length} online`, 'info');
    } finally {
      document.querySelectorAll('.receiver-card').forEach(c => c.classList.remove('reloading'));
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Reload All'; }
    }
  }

  // ── Subnet scan ──────────────────────────────────────────────────────────
  async function scanNetwork() {
    const modal = new bootstrap.Modal(document.getElementById('scanResultModal'));
    const body  = document.getElementById('scan-result-body');
    const reloadBtn = document.getElementById('btn-scan-reload-page');

    // Static loader markup — no interpolation, safe.
    body.innerHTML = '<div class="loader-overlay"><span class="spinner-border me-2"></span>Scanning 10.1.248.1–254… (this takes ~5 s)</div>';
    if (reloadBtn) reloadBtn.style.display = 'none';
    modal.show();

    try {
      const resp = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await resp.json();

      if (!resp.ok) {
        body.textContent = '';
        const div = document.createElement('div');
        div.className = 'text-danger';
        div.textContent = `Scan failed: ${data.error || resp.status}`;
        body.appendChild(div);
        return;
      }

      const added   = data.added ?? 0;
      const updated = data.updated ?? 0;
      const found   = data.found ?? 0;
      const total   = data.scanned ?? 254;

      body.textContent = '';

      // Stats row — numbers are safe but keep consistent building style.
      const stats = document.createElement('div');
      stats.className = 'row text-center mb-3';
      [
        ['text-info', total, 'Probed'],
        ['text-success', found, 'Found'],
        ['text-primary', added, 'New'],
        ['text-warning', updated, 'Updated'],
      ].forEach(([cls, num, label]) => {
        const col = document.createElement('div');
        col.className = 'col';
        const big = document.createElement('div');
        big.className = `display-6 fw-bold ${cls}`;
        big.textContent = num;
        const small = document.createElement('small');
        small.textContent = label;
        col.appendChild(big);
        col.appendChild(small);
        stats.appendChild(col);
      });
      body.appendChild(stats);

      if (data.receivers && data.receivers.length) {
        // Build device table with textContent for every device-supplied field —
        // hostnames and firmware versions come from untrusted BirdDog /about.
        const table = document.createElement('table');
        table.className = 'table table-sm table-dark table-hover mb-0';
        table.innerHTML = '<thead><tr><th>IP</th><th>Hostname</th><th>Firmware</th><th>Status</th></tr></thead>';
        const tbody = document.createElement('tbody');
        data.receivers.forEach(r => {
          const tr = document.createElement('tr');
          const td = (cls, text) => {
            const el = document.createElement('td');
            if (cls) el.className = cls;
            el.textContent = text;
            return el;
          };
          tr.appendChild(td('', r.ip_address));
          tr.appendChild(td('', r.hostname || '—'));
          tr.appendChild(td('text-muted small', r.firmware_version || '—'));
          const statusTd = document.createElement('td');
          const badge = document.createElement('span');
          badge.className = `badge ${r.status === 'online' ? 'bg-success' : 'bg-danger'}`;
          badge.textContent = r.status;
          statusTd.appendChild(badge);
          tr.appendChild(statusTd);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);

        // Also update the dashboard grid without a page reload
        data.receivers.forEach(updateReceiverCard);
        updateSummary(data.receivers);
      } else {
        const p = document.createElement('p');
        p.className = 'text-muted text-center';
        p.textContent = 'No BirdDog PLAY devices found.';
        body.appendChild(p);
      }

      if (reloadBtn && added > 0) reloadBtn.style.display = '';

    } catch (err) {
      body.textContent = '';
      const div = document.createElement('div');
      div.className = 'text-danger';
      div.textContent = `Error: ${err.message}`;
      body.appendChild(div);
    }
  }

  // ── Discover NDI sources ─────────────────────────────────────────────────
  async function discoverSources() {
    const btn = document.getElementById('btn-discover');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Discovering…'; }

    try {
      const resp = await fetch('/api/sources/discover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
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

  // ── Reboot / restart ─────────────────────────────────────────────────────
  async function rebootReceiver(receiverId) {
    if (!confirm('Reboot this device?')) return;
    const resp = await fetch(`/api/receivers/${receiverId}/reboot`, { method: 'POST' });
    const data = await resp.json();
    toast(data.status === 200 ? 'Reboot initiated' : `Reboot failed (${data.status})`,
          data.status === 200 ? 'warning' : 'danger');
  }

  async function rebootAll() {
    const btn = document.getElementById('btn-reboot-all-confirm');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Rebooting…'; }
    try {
      const resp = await fetch('/api/receivers/bulk-reboot', { method: 'POST' });
      const data = await resp.json();
      bootstrap.Modal.getInstance(document.getElementById('rebootAllModal'))?.hide();
      if (resp.ok) {
        const msg = `Reboot sent to ${data.rebooted} decoder(s)` +
                    (data.failed ? ` — ${data.failed} failed` : '');
        toast(msg, data.failed ? 'warning' : 'success');
      } else {
        toast('Bulk reboot failed', 'danger');
      }
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-power me-1"></i>Reboot All'; }
    }
  }

  async function restartReceiver(receiverId) {
    const resp = await fetch(`/api/receivers/${receiverId}/restart`, { method: 'POST' });
    const data = await resp.json();
    toast(data.status === 200 ? 'Video restart initiated' : `Restart failed (${data.status})`,
          data.status === 200 ? 'info' : 'danger');
  }

  // ── Remove offline receiver ──────────────────────────────────────────────
  async function removeReceiver(receiverId, name) {
    if (!confirm(`Remove "${name}" from the database?\n\nThis will not affect the device itself.`)) return;
    const resp = await fetch(`/api/receivers/${receiverId}`, { method: 'DELETE' });
    if (resp.ok) {
      document.getElementById(`col-${receiverId}`)?.remove();
      toast(`Removed ${name}`, 'warning');
      // Update counts
      const remaining = Array.from(document.querySelectorAll('.receiver-card')).map(c => ({
        id: c.dataset.receiverId,
        status: c.querySelector('.status-dot')?.classList[1]?.replace('status-', '') || 'unknown',
      }));
      updateSummary(remaining);
    } else {
      toast('Remove failed', 'danger');
    }
  }

  // ── Add receiver manually ────────────────────────────────────────────────
  async function addReceiver() {
    const octet = document.getElementById('add-octet')?.value?.trim();
    const label = document.getElementById('add-label')?.value?.trim();
    if (!octet) { toast('IP octet is required', 'warning'); return; }

    const resp = await fetch('/api/receivers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip_last_octet: octet, label: label || undefined }),
    });
    const data = await resp.json();
    if (resp.ok) {
      toast(`Receiver .${octet} added`, 'success');
      setTimeout(() => location.reload(), 800);
    } else {
      toast(data.error || 'Failed to add receiver', 'danger');
    }
  }

  // ── Bind dashboard events ────────────────────────────────────────────────
  function bindDashboard() {
    document.getElementById('btn-scan')?.addEventListener('click', scanNetwork);
    document.getElementById('btn-bulk-reload')?.addEventListener('click', bulkReload);
    document.getElementById('btn-discover')?.addEventListener('click', discoverSources);
    document.getElementById('btn-add-receiver')?.addEventListener('click', addReceiver);
    document.getElementById('btn-reboot-all')?.addEventListener('click', () =>
      new bootstrap.Modal(document.getElementById('rebootAllModal')).show());
    document.getElementById('btn-reboot-all-confirm')?.addEventListener('click', rebootAll);

    document.querySelectorAll('.source-select').forEach(sel => {
      sel.addEventListener('change', () => {
        if (sel.value) setSource(sel.dataset.receiverId, sel.value);
      });
    });

    document.querySelectorAll('.btn-poll').forEach(b =>
      b.addEventListener('click', () => pollReceiver(b.dataset.receiverId)));
    document.querySelectorAll('.btn-reboot').forEach(b =>
      b.addEventListener('click', () => rebootReceiver(b.dataset.receiverId)));
    document.querySelectorAll('.btn-restart').forEach(b =>
      b.addEventListener('click', () => restartReceiver(b.dataset.receiverId)));
    document.querySelectorAll('.btn-remove-offline').forEach(b =>
      b.addEventListener('click', () => removeReceiver(b.dataset.receiverId, b.dataset.receiverName)));
  }

  document.addEventListener('DOMContentLoaded', bindDashboard);

  return {
    toast, pollReceiver, bulkReload, scanNetwork,
    discoverSources, setSource, rebootReceiver, rebootAll, restartReceiver,
    removeReceiver,
  };
})();
