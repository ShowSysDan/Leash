/* Leash — Schedules page */
(function () {
  'use strict';

  const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const modal     = new bootstrap.Modal(document.getElementById('scheduleModal'));

  // ── helpers ────────────────────────────────────────────────────────────────

  function renderRow(s) {
    const row = document.getElementById(`sched-row-${s.id}`);
    if (!row) { location.reload(); return; }

    const dayBadges = s.day_labels.map(d =>
      `<span class="badge bg-secondary">${d}</span>`
    ).join(' ');

    const lastRun = s.last_run
      ? new Date(s.last_run + 'Z').toLocaleString()
      : 'Never run';

    let resultClass = 'text-muted';
    if (s.last_result) {
      if (s.last_result.startsWith('ERROR'))   resultClass = 'text-danger';
      else if (s.last_result.startsWith('OK')) resultClass = 'text-success';
      else                                      resultClass = 'text-warning';
    }

    const snapBadge = s.snapshot_name
      ? `<span class="badge bg-dark border border-secondary">${s.snapshot_name}</span>`
      : `<span class="text-danger small"><i class="bi bi-exclamation-triangle me-1"></i>Snapshot deleted</span>`;

    const enabledCls = s.enabled ? 'btn-success' : 'btn-outline-secondary';
    const enabledIcon = s.enabled ? 'check-circle-fill' : 'pause-circle';

    row.className = s.enabled ? '' : 'opacity-50';
    row.innerHTML = `
      <td><strong>${s.name}</strong></td>
      <td>${snapBadge}</td>
      <td><div class="d-flex gap-1 flex-wrap">${dayBadges}</div></td>
      <td><span class="font-monospace">${s.time_of_day}</span></td>
      <td class="text-center">
        <button class="btn btn-xs ${enabledCls} btn-toggle-sched"
                data-sched-id="${s.id}" title="${s.enabled ? 'Disable' : 'Enable'}">
          <i class="bi bi-${enabledIcon}"></i>
        </button>
      </td>
      <td>
        <small class="text-muted d-block">${lastRun}</small>
        <small class="${resultClass}">${s.last_result || ''}</small>
      </td>
      <td class="text-end">
        <div class="d-flex gap-1 justify-content-end">
          <button class="btn btn-xs btn-outline-secondary btn-edit-sched"
                  data-sched-id="${s.id}"
                  data-name="${s.name}"
                  data-snapshot-id="${s.snapshot_id || ''}"
                  data-days="${s.days_of_week}"
                  data-time="${s.time_of_day}"
                  data-enabled="${s.enabled}"
                  title="Edit"><i class="bi bi-pencil"></i></button>
          <button class="btn btn-xs btn-outline-danger btn-delete-sched"
                  data-sched-id="${s.id}" data-sched-name="${s.name}"
                  title="Delete"><i class="bi bi-trash"></i></button>
        </div>
      </td>`;
    bindRowActions(row);
  }

  function appendRow(s) {
    const tbody = document.getElementById('schedules-table-body');
    if (!tbody) { location.reload(); return; }
    const tr = document.createElement('tr');
    tr.id = `sched-row-${s.id}`;
    tbody.appendChild(tr);
    renderRow(s);
  }

  // ── modal helpers ──────────────────────────────────────────────────────────

  function clearModal() {
    document.getElementById('sched-edit-id').value = '';
    document.getElementById('schedule-modal-title').textContent = 'Add Schedule';
    document.getElementById('sched-name').value = '';
    document.getElementById('sched-snapshot').value = '';
    document.getElementById('sched-time').value = '';
    document.getElementById('sched-enabled').checked = true;
    document.querySelectorAll('.day-check').forEach(cb => { cb.checked = false; });
  }

  function openEditModal(btn) {
    clearModal();
    document.getElementById('schedule-modal-title').textContent = 'Edit Schedule';
    document.getElementById('sched-edit-id').value = btn.dataset.schedId;
    document.getElementById('sched-name').value = btn.dataset.name;
    document.getElementById('sched-snapshot').value = btn.dataset.snapshotId;
    document.getElementById('sched-time').value = btn.dataset.time;
    document.getElementById('sched-enabled').checked = btn.dataset.enabled === 'true';

    const days = btn.dataset.days.split(',');
    document.querySelectorAll('.day-check').forEach(cb => {
      cb.checked = days.includes(cb.value);
    });
    modal.show();
  }

  // ── save (create or update) ────────────────────────────────────────────────

  document.getElementById('btn-save-schedule')?.addEventListener('click', async () => {
    const editId = document.getElementById('sched-edit-id').value;
    const name = document.getElementById('sched-name').value.trim();
    const snapId = document.getElementById('sched-snapshot').value;
    const time = document.getElementById('sched-time').value;
    const enabled = document.getElementById('sched-enabled').checked;
    const days = [...document.querySelectorAll('.day-check:checked')].map(cb => cb.value);

    if (!name)          { window.Leash.toast('Name is required', 'warning'); return; }
    if (!snapId)        { window.Leash.toast('Select a snapshot', 'warning'); return; }
    if (!time)          { window.Leash.toast('Set a time', 'warning'); return; }
    if (!days.length)   { window.Leash.toast('Select at least one day', 'warning'); return; }

    const body = {
      name,
      snapshot_id: parseInt(snapId),
      days_of_week: days.join(','),
      time_of_day: time,
      enabled,
    };

    const url    = editId ? `/api/schedules/${editId}` : '/api/schedules';
    const method = editId ? 'PUT' : 'POST';

    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (!resp.ok) {
      window.Leash.toast(data.error || 'Save failed', 'danger');
      return;
    }

    modal.hide();
    if (editId) {
      renderRow(data);
      window.Leash.toast('Schedule updated', 'success');
    } else {
      appendRow(data);
      window.Leash.toast('Schedule created', 'success');
    }
  });

  // ── add button ─────────────────────────────────────────────────────────────

  document.getElementById('btn-add-schedule')?.addEventListener('click', () => {
    clearModal();
    modal.show();
  });

  // ── day preset buttons ─────────────────────────────────────────────────────

  document.getElementById('days-weekdays')?.addEventListener('click', () => {
    document.querySelectorAll('.day-check').forEach(cb => {
      cb.checked = ['0','1','2','3','4'].includes(cb.value);
    });
  });
  document.getElementById('days-all')?.addEventListener('click', () => {
    document.querySelectorAll('.day-check').forEach(cb => { cb.checked = true; });
  });
  document.getElementById('days-none')?.addEventListener('click', () => {
    document.querySelectorAll('.day-check').forEach(cb => { cb.checked = false; });
  });

  // ── row-level actions (toggle, edit, delete) ───────────────────────────────

  function bindRowActions(root) {
    root.querySelector('.btn-toggle-sched')?.addEventListener('click', async function () {
      const id = this.dataset.schedId;
      const resp = await fetch(`/api/schedules/${id}/toggle`, { method: 'PATCH' });
      const data = await resp.json();
      if (resp.ok) {
        renderRow(data);
        window.Leash.toast(data.enabled ? 'Schedule enabled' : 'Schedule disabled', 'info');
      }
    });

    root.querySelector('.btn-edit-sched')?.addEventListener('click', function () {
      openEditModal(this);
    });

    root.querySelector('.btn-delete-sched')?.addEventListener('click', async function () {
      const id   = this.dataset.schedId;
      const name = this.dataset.schedName;
      if (!confirm(`Delete schedule "${name}"?`)) return;
      const resp = await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
      if (resp.ok) {
        document.getElementById(`sched-row-${id}`)?.remove();
        window.Leash.toast('Schedule deleted', 'warning');
      }
    });
  }

  // Bind actions on server-rendered rows
  document.querySelectorAll('#schedules-table-body tr').forEach(bindRowActions);
})();
