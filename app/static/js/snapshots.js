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

      // Build the table with textContent so receiver labels, IPs, and
      // source names (user- and device-supplied) are treated as data, not HTML.
      const entries = data.entries || [];
      const previewBody = document.getElementById('preview-snap-body');
      previewBody.textContent = '';

      const summary = document.createElement('p');
      summary.className = 'text-muted small';
      summary.textContent = `${entries.length} receivers captured.`;
      previewBody.appendChild(summary);

      const table = document.createElement('table');
      table.className = 'table table-sm table-dark';
      table.innerHTML = '<thead><tr><th>Receiver</th><th>IP</th><th>Saved Source <small class="text-muted fw-normal">(click ✏ to edit)</small></th><th>Status</th></tr></thead>';
      const tbody = document.createElement('tbody');

      const STATUS_CLS = { online: 'bg-success', offline: 'bg-danger' };
      const allSources = window.LEASH?.sources || [];

      function makeSourceCell(e) {
        const td = document.createElement('td');

        function showView() {
          td.textContent = '';
          const wrap = document.createElement('div');
          wrap.className = 'd-flex align-items-center gap-2';

          const label = document.createElement('span');
          label.className = 'snap-source-label';
          if (e.source_name) {
            label.textContent = e.source_name;
          } else {
            label.className += ' text-muted fst-italic';
            label.textContent = 'none';
          }

          const editBtn = document.createElement('button');
          editBtn.className = 'btn btn-xs btn-outline-secondary ms-auto';
          editBtn.title = 'Edit saved source';
          editBtn.innerHTML = '<i class="bi bi-pencil"></i>';
          editBtn.addEventListener('click', showEdit);

          wrap.appendChild(label);
          wrap.appendChild(editBtn);
          td.appendChild(wrap);
        }

        function showEdit() {
          td.textContent = '';
          const wrap = document.createElement('div');
          wrap.className = 'd-flex align-items-center gap-1';

          const sel = document.createElement('select');
          sel.className = 'form-select form-select-sm';
          sel.style.minWidth = '10rem';
          sel.add(new Option('— none —', ''));
          allSources.forEach(name => sel.add(new Option(name, name)));
          sel.value = e.source_name || '';

          const saveBtn = document.createElement('button');
          saveBtn.className = 'btn btn-xs btn-success';
          saveBtn.textContent = 'Save';
          saveBtn.addEventListener('click', async () => {
            const newSource = sel.value;
            const resp = await fetch(`/api/snapshots/${_previewSnapId}/entries/${e.id}`, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ source_name: newSource }),
            });
            if (resp.ok) {
              e.source_name = newSource;
              window.Leash.toast('Entry updated', 'success');
            } else {
              window.Leash.toast('Update failed', 'danger');
            }
            showView();
          });

          const cancelBtn = document.createElement('button');
          cancelBtn.className = 'btn btn-xs btn-secondary';
          cancelBtn.textContent = 'Cancel';
          cancelBtn.addEventListener('click', showView);

          wrap.appendChild(sel);
          wrap.appendChild(saveBtn);
          wrap.appendChild(cancelBtn);
          td.appendChild(wrap);
        }

        showView();
        return td;
      }

      entries.forEach(e => {
        const tr = document.createElement('tr');
        const td = (cls, text) => {
          const el = document.createElement('td');
          if (cls) el.className = cls;
          el.textContent = text;
          return el;
        };
        tr.appendChild(td('', e.receiver_label || '—'));
        tr.appendChild(td('text-muted small', e.receiver_ip || '—'));
        tr.appendChild(makeSourceCell(e));

        const statusTd = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `badge ${STATUS_CLS[e.receiver_status] || 'bg-secondary'}`;
        badge.textContent = e.receiver_status || 'unknown';
        statusTd.appendChild(badge);
        tr.appendChild(statusTd);

        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      previewBody.appendChild(table);
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
