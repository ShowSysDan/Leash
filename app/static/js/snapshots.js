/**
 * Snapshots page — capture, preview, recall, delete.
 */
document.addEventListener('DOMContentLoaded', () => {

  // ── Selection helpers ────────────────────────────────────────────────────
  document.getElementById('cap-select-all')?.addEventListener('click', () => {
    document.querySelectorAll('.cap-recv-check').forEach(cb => { cb.checked = true; });
  });
  document.getElementById('cap-select-none')?.addEventListener('click', () => {
    document.querySelectorAll('.cap-recv-check').forEach(cb => { cb.checked = false; });
  });
  document.getElementById('cap-select-online')?.addEventListener('click', () => {
    document.querySelectorAll('.cap-recv-check').forEach(cb => {
      cb.checked = cb.dataset.status === 'online';
    });
  });

  // ── Capture ──────────────────────────────────────────────────────────────
  document.getElementById('btn-capture')?.addEventListener('click', async () => {
    const name = document.getElementById('cap-name')?.value?.trim();
    const desc = document.getElementById('cap-desc')?.value?.trim();
    if (!name) { window.Leash.toast('Name is required', 'warning'); return; }

    const ids = Array.from(document.querySelectorAll('.cap-recv-check:checked'))
                     .map(cb => parseInt(cb.value));

    const resp = await fetch('/api/snapshots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: desc, receiver_ids: ids.length ? ids : undefined }),
    });
    const data = await resp.json();
    if (resp.ok) {
      window.Leash.toast(`Snapshot "${name}" saved (${data.entry_count} receivers)`, 'success');
      setTimeout(() => location.reload(), 700);
    } else {
      window.Leash.toast(data.error || 'Capture failed', 'danger');
    }
  });

  // ── Preview ──────────────────────────────────────────────────────────────
  let _previewSnapId = null;

  document.querySelectorAll('.btn-preview-snap').forEach(btn => {
    btn.addEventListener('click', async () => {
      _previewSnapId = btn.dataset.snapId;
      document.getElementById('preview-snap-title').textContent = btn.dataset.snapName;
      document.getElementById('preview-snap-body').innerHTML =
        '<div class="loader-overlay"><span class="spinner-border"></span></div>';

      new bootstrap.Modal(document.getElementById('previewSnapModal')).show();

      const resp = await fetch(`/api/snapshots/${_previewSnapId}`);
      const data = await resp.json();

      if (!resp.ok) {
        document.getElementById('preview-snap-body').innerHTML =
          `<div class="text-danger">Failed to load snapshot</div>`;
        return;
      }

      const entries = data.entries || [];
      let html = `<p class="text-muted small">${entries.length} receivers captured.</p>
        <table class="table table-sm table-dark">
          <thead><tr><th>Receiver</th><th>IP</th><th>Saved Source</th><th>Current Status</th></tr></thead>
          <tbody>`;
      entries.forEach(e => {
        const statusBadge = e.receiver_status === 'online'
          ? '<span class="badge bg-success">online</span>'
          : e.receiver_status === 'offline'
          ? '<span class="badge bg-danger">offline</span>'
          : '<span class="badge bg-secondary">unknown</span>';
        html += `<tr>
          <td>${e.receiver_label || '—'}</td>
          <td class="text-muted">${e.receiver_ip || '—'}</td>
          <td>${e.source_name || '<em class="text-muted">none</em>'}</td>
          <td>${statusBadge}</td>
        </tr>`;
      });
      html += '</tbody></table>';
      document.getElementById('preview-snap-body').innerHTML = html;
    });
  });

  document.getElementById('btn-recall-from-preview')?.addEventListener('click', () => {
    if (_previewSnapId) recallSnapshot(_previewSnapId);
  });

  // ── Recall ───────────────────────────────────────────────────────────────
  document.querySelectorAll('.btn-recall-snap').forEach(btn => {
    btn.addEventListener('click', () => recallSnapshot(btn.dataset.snapId, btn.dataset.snapName));
  });

  async function recallSnapshot(snapId, snapName) {
    if (!confirm(`Recall "${snapName || 'snapshot'}"?\n\nThis will change the source on all saved receivers.`)) return;

    const progressModal = new bootstrap.Modal(document.getElementById('recallProgressModal'),
                                              { backdrop: 'static' });
    progressModal.show();

    try {
      const resp = await fetch(`/api/snapshots/${snapId}/recall`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await resp.json();
      progressModal.hide();

      if (resp.ok) {
        window.Leash.toast(
          `Recalled: ${data.succeeded}/${data.attempted} succeeded, ${data.skipped} skipped`,
          data.succeeded > 0 ? 'success' : 'warning'
        );
      } else {
        window.Leash.toast(data.error || 'Recall failed', 'danger');
      }
    } catch (err) {
      progressModal.hide();
      window.Leash.toast(`Error: ${err.message}`, 'danger');
    }
  }

  // ── Delete ───────────────────────────────────────────────────────────────
  document.querySelectorAll('.btn-delete-snap').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Delete snapshot "${btn.dataset.snapName}"?`)) return;
      const resp = await fetch(`/api/snapshots/${btn.dataset.snapId}`, { method: 'DELETE' });
      if (resp.ok) {
        document.getElementById(`snap-row-${btn.dataset.snapId}`)?.remove();
        window.Leash.toast('Snapshot deleted', 'warning');
      }
    });
  });
});
