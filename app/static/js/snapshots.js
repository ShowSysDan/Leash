/**
 * Snapshots page — capture, preview (with bulk source edit / entry removal),
 * recall, delete.
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

  // Group chips: click → set selection to this group's members.
  // Shift+click → additive (keep existing selection and add this group).
  document.querySelectorAll('.cap-group-chip').forEach(chip => {
    chip.addEventListener('click', (ev) => {
      let memberIds;
      try {
        memberIds = new Set((JSON.parse(chip.dataset.receiverIds) || []).map(Number));
      } catch (_) {
        memberIds = new Set();
      }
      const additive = ev.shiftKey;
      document.querySelectorAll('.cap-recv-check').forEach(cb => {
        const inGroup = memberIds.has(parseInt(cb.value));
        if (additive) {
          if (inGroup) cb.checked = true;
        } else {
          cb.checked = inGroup;
        }
      });
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

      const entryById = new Map(entries.map(e => [e.id, e]));
      const rowById = new Map();        // entry id → <tr>
      const refreshCellById = new Map();  // entry id → re-render source cell

      const summary = document.createElement('p');
      summary.className = 'text-muted small mb-2';
      const setSummary = (n) => { summary.textContent = `${n} receivers captured.`; };
      setSummary(entries.length);
      previewBody.appendChild(summary);

      // ── Bulk edit toolbar ──
      const toolbar = document.createElement('div');
      toolbar.className = 'd-flex align-items-center gap-2 flex-wrap mb-2';

      const toolbarLabel = document.createElement('span');
      toolbarLabel.className = 'small text-muted';
      toolbarLabel.textContent = 'With selected:';

      const bulkSel = document.createElement('select');
      bulkSel.className = 'form-select form-select-sm w-auto';
      bulkSel.add(new Option('— none —', ''));
      (window.LEASH?.sources || []).forEach(name => bulkSel.add(new Option(name, name)));

      const bulkApplyBtn = document.createElement('button');
      bulkApplyBtn.className = 'btn btn-xs btn-success';
      bulkApplyBtn.innerHTML = '<i class="bi bi-pencil me-1"></i>Set source';

      const bulkDeleteBtn = document.createElement('button');
      bulkDeleteBtn.className = 'btn btn-xs btn-outline-danger';
      bulkDeleteBtn.innerHTML = '<i class="bi bi-trash me-1"></i>Remove';

      const countSpan = document.createElement('span');
      countSpan.className = 'small text-muted ms-auto';

      toolbar.appendChild(toolbarLabel);
      toolbar.appendChild(bulkSel);
      toolbar.appendChild(bulkApplyBtn);
      toolbar.appendChild(bulkDeleteBtn);
      toolbar.appendChild(countSpan);
      previewBody.appendChild(toolbar);

      const table = document.createElement('table');
      table.className = 'table table-sm table-dark';
      table.innerHTML = '<thead><tr>' +
        '<th class="text-center" style="width:1%"></th>' +
        '<th>Receiver</th><th>IP</th>' +
        '<th>Saved Source <small class="text-muted fw-normal">(click ✏ to edit)</small></th>' +
        '<th>Status</th><th></th>' +
        '</tr></thead>';
      const tbody = document.createElement('tbody');

      const headerCheck = document.createElement('input');
      headerCheck.type = 'checkbox';
      headerCheck.className = 'form-check-input';
      headerCheck.title = 'Select all';
      table.querySelector('thead th').appendChild(headerCheck);
      headerCheck.addEventListener('change', () => {
        tbody.querySelectorAll('.snap-entry-check').forEach(cb => { cb.checked = headerCheck.checked; });
        refreshToolbar();
      });

      function getCheckedIds() {
        return Array.from(tbody.querySelectorAll('.snap-entry-check:checked'))
                    .map(cb => parseInt(cb.value));
      }

      function refreshToolbar() {
        const total = tbody.querySelectorAll('.snap-entry-check').length;
        const n = getCheckedIds().length;
        countSpan.textContent = `${n} selected`;
        bulkApplyBtn.disabled = n === 0;
        bulkDeleteBtn.disabled = n === 0;
        headerCheck.checked = total > 0 && n === total;
      }

      async function deleteEntries(ids) {
        const resp = await fetch(`/api/snapshots/${_previewSnapId}/entries`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entry_ids: ids }),
        });
        const result = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          window.Leash.toast(result.error || 'Remove failed', 'danger');
          return;
        }
        ids.forEach(id => {
          rowById.get(id)?.remove();
          rowById.delete(id);
          refreshCellById.delete(id);
          entryById.delete(id);
        });
        const count = result.entry_count ?? entryById.size;
        setSummary(count);
        updateSnapEntryCount(_previewSnapId, count);
        refreshToolbar();
        window.Leash.toast(`Removed ${ids.length} receiver${ids.length === 1 ? '' : 's'} from snapshot`, 'warning');
      }

      bulkApplyBtn.addEventListener('click', async () => {
        const ids = getCheckedIds();
        if (!ids.length) return;
        const newSource = bulkSel.value;
        const resp = await fetch(`/api/snapshots/${_previewSnapId}/entries`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entry_ids: ids, source_name: newSource }),
        });
        if (resp.ok) {
          ids.forEach(id => {
            const entry = entryById.get(id);
            if (entry) entry.source_name = newSource;
            refreshCellById.get(id)?.();
          });
          window.Leash.toast(`Updated ${ids.length} entr${ids.length === 1 ? 'y' : 'ies'}`, 'success');
        } else {
          const result = await resp.json().catch(() => ({}));
          window.Leash.toast(result.error || 'Update failed', 'danger');
        }
      });

      bulkDeleteBtn.addEventListener('click', () => {
        const ids = getCheckedIds();
        if (!ids.length) return;
        if (!confirm(`Remove ${ids.length} receiver${ids.length === 1 ? '' : 's'} from this snapshot?`)) return;
        deleteEntries(ids);
      });

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
        refreshCellById.set(e.id, showView);
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

        const checkTd = document.createElement('td');
        checkTd.className = 'text-center';
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.className = 'form-check-input snap-entry-check';
        check.value = e.id;
        check.addEventListener('change', refreshToolbar);
        checkTd.appendChild(check);
        tr.appendChild(checkTd);

        tr.appendChild(td('', e.receiver_label || '—'));
        tr.appendChild(td('text-muted small', e.receiver_ip || '—'));
        tr.appendChild(makeSourceCell(e));

        const statusTd = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `badge ${STATUS_CLS[e.receiver_status] || 'bg-secondary'}`;
        badge.textContent = e.receiver_status || 'unknown';
        statusTd.appendChild(badge);
        tr.appendChild(statusTd);

        const actionTd = document.createElement('td');
        actionTd.className = 'text-end';
        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-xs btn-outline-danger';
        delBtn.title = 'Remove from snapshot';
        delBtn.innerHTML = '<i class="bi bi-trash"></i>';
        delBtn.addEventListener('click', () => {
          if (!confirm(`Remove "${e.receiver_label || 'receiver'}" from this snapshot?`)) return;
          deleteEntries([e.id]);
        });
        actionTd.appendChild(delBtn);
        tr.appendChild(actionTd);

        rowById.set(e.id, tr);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      previewBody.appendChild(table);
      refreshToolbar();
    });
  });

  // Keep the snapshot list's receiver-count badges in sync after entry removals.
  function updateSnapEntryCount(snapId, count) {
    if (count == null) return;
    const badge = document.querySelector(`#snap-row-${snapId} td.text-center .badge`);
    if (badge) badge.textContent = count;
    const meta = document.querySelector(`#snap-card-${snapId} .mc-meta span`);
    if (meta) {
      meta.textContent = `${count} receivers`;
      const icon = document.createElement('i');
      icon.className = 'bi bi-broadcast-pin me-1';
      meta.prepend(icon);
    }
  }

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
        document.getElementById(`snap-card-${btn.dataset.snapId}`)?.remove();
        window.Leash.toast('Snapshot deleted', 'warning');
      }
    });
  });
});
