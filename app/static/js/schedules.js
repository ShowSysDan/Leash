/* Leash — Schedules page */
(function () {
  'use strict';

  const modal = new bootstrap.Modal(document.getElementById('scheduleModal'));

  // ── enforcement countdown (updates every 30s) ──────────────────────────────

  function updateCountdowns() {
    document.querySelectorAll('.enforcement-badge[data-enforcing-until]').forEach(badge => {
      const until = new Date(badge.dataset.enforcingUntil + 'Z');  // treat as UTC
      const remaining = Math.max(0, Math.round((until - Date.now()) / 60000));
      if (remaining <= 0) {
        badge.textContent = 'Expiring…';
        setTimeout(() => location.reload(), 5000);
      } else {
        badge.innerHTML = `<i class="bi bi-shield-check me-1"></i>${remaining} min left`;
      }
    });
  }
  updateCountdowns();
  setInterval(updateCountdowns, 30000);

  // ── mode toggle ────────────────────────────────────────────────────────────

  function applyModeUI(mode) {
    document.getElementById('days-section').style.display    = (mode === 'once')  ? 'none' : '';
    document.getElementById('run-date-section').style.display = (mode === 'once')  ? '' : 'none';
    document.getElementById('end-date-section').style.display = (mode === 'weekly_until') ? '' : 'none';
  }

  document.getElementById('sched-mode')?.addEventListener('change', function () {
    applyModeUI(this.value);
  });

  // ── persist options toggle ─────────────────────────────────────────────────

  document.getElementById('sched-persistent')?.addEventListener('change', function () {
    document.getElementById('persist-options').style.display = this.checked ? '' : 'none';
  });

  // ── helpers ────────────────────────────────────────────────────────────────

  // Small DOM helpers for safe, terse construction.
  function h(tag, attrs = {}, text) {
    const el = document.createElement(tag);
    for (const k in attrs) {
      if (k === 'class')     el.className = attrs[k];
      else if (k === 'html') el.innerHTML = attrs[k];      // caller-controlled (icons only)
      else if (k.startsWith('data-')) el.setAttribute(k, attrs[k]);
      else el[k] = attrs[k];
    }
    if (text !== undefined) el.textContent = String(text);
    return el;
  }

  function enforcementCell(s) {
    const td = document.createElement('td');
    if (!s.persistent) {
      td.appendChild(h('span', { class: 'text-muted small' }, '—'));
      return td;
    }

    if (s.is_enforcing && s.enforcing_until) {
      const until = new Date(s.enforcing_until + 'Z');
      const mins  = Math.max(0, Math.round((until - Date.now()) / 60000));
      const badge = h('span', {
        class: 'badge bg-success enforcement-badge',
        'data-enforcing-until': s.enforcing_until,
        html: '<i class="bi bi-shield-check me-1"></i>',
      });
      badge.appendChild(document.createTextNode(`${mins} min left`));
      td.appendChild(badge);

      const stop = h('button', {
        class: 'btn btn-xs btn-outline-warning ms-1 btn-stop-enforcement',
        'data-sched-id': s.id,
        title: 'Stop enforcement now',
        html: '<i class="bi bi-stop-circle"></i>',
      });
      td.appendChild(stop);
    } else {
      const span = h('span', { class: 'text-muted small', html: '<i class="bi bi-shield me-1"></i>' });
      span.appendChild(document.createTextNode(`${s.persist_minutes} min window`));
      td.appendChild(span);
    }
    return td;
  }

  function renderRow(s) {
    const row = document.getElementById(`sched-row-${s.id}`);
    if (!row) { location.reload(); return; }

    const lastRunStr = s.last_run
      ? new Date(s.last_run + 'Z').toLocaleString()
      : 'Never run';

    let resultClass = 'text-muted';
    if (s.last_result) {
      if (s.last_result.startsWith('ERROR'))   resultClass = 'text-danger';
      else if (s.last_result.startsWith('OK')) resultClass = 'text-success';
      else                                      resultClass = 'text-warning';
    }

    row.className = s.enabled ? '' : 'opacity-50';
    row.textContent = '';

    // Name (user-supplied)
    const nameTd = document.createElement('td');
    nameTd.appendChild(h('strong', {}, s.name));
    row.appendChild(nameTd);

    // Snapshot (name user-supplied)
    const snapTd = document.createElement('td');
    if (s.snapshot_name) {
      snapTd.appendChild(h('span', { class: 'badge bg-dark border border-secondary' }, s.snapshot_name));
    } else {
      snapTd.appendChild(h('span', {
        class: 'text-danger small',
        html: '<i class="bi bi-exclamation-triangle me-1"></i>Deleted',
      }));
    }
    row.appendChild(snapTd);

    // Days (server-generated labels, safe but stay consistent)
    const daysTd = document.createElement('td');
    const daysWrap = h('div', { class: 'd-flex gap-1 flex-wrap' });
    s.day_labels.forEach(d => daysWrap.appendChild(h('span', { class: 'badge bg-secondary' }, d)));
    daysTd.appendChild(daysWrap);
    row.appendChild(daysTd);

    // Time (validated HH:MM)
    const timeTd = document.createElement('td');
    timeTd.appendChild(h('span', { class: 'font-monospace' }, s.time_of_day));
    row.appendChild(timeTd);

    // On/off toggle
    const toggleTd = h('td', { class: 'text-center' });
    const toggleBtn = h('button', {
      class: `btn btn-xs ${s.enabled ? 'btn-success' : 'btn-outline-secondary'} btn-toggle-sched`,
      'data-sched-id': s.id,
      title: s.enabled ? 'Disable' : 'Enable',
      html: `<i class="bi bi-${s.enabled ? 'check-circle-fill' : 'pause-circle'}"></i>`,
    });
    toggleTd.appendChild(toggleBtn);
    row.appendChild(toggleTd);

    // Enforcement cell (already returns a <td>)
    row.appendChild(enforcementCell(s));

    // Last run / result (server-generated — last_result can contain exception text)
    const lastTd = document.createElement('td');
    lastTd.appendChild(h('small', { class: 'text-muted d-block' }, lastRunStr));
    lastTd.appendChild(h('small', { class: resultClass }, s.last_result || ''));
    row.appendChild(lastTd);

    // Actions — dataset values use setAttribute which safely escapes
    const actionsTd = h('td', { class: 'text-end' });
    const actionsWrap = h('div', { class: 'd-flex gap-1 justify-content-end' });

    const editBtn = h('button', {
      class: 'btn btn-xs btn-outline-secondary btn-edit-sched',
      title: 'Edit',
      html: '<i class="bi bi-pencil"></i>',
    });
    editBtn.setAttribute('data-sched-id', s.id);
    editBtn.setAttribute('data-name', s.name);
    editBtn.setAttribute('data-snapshot-id', s.snapshot_id || '');
    editBtn.setAttribute('data-mode', s.schedule_mode || 'weekly');
    editBtn.setAttribute('data-days', s.days_of_week || '');
    editBtn.setAttribute('data-run-date', s.run_date || '');
    editBtn.setAttribute('data-end-date', s.end_date || '');
    editBtn.setAttribute('data-time', s.time_of_day);
    editBtn.setAttribute('data-enabled', s.enabled);
    editBtn.setAttribute('data-persistent', s.persistent);
    editBtn.setAttribute('data-persist-minutes', s.persist_minutes);
    actionsWrap.appendChild(editBtn);

    const delBtn = h('button', {
      class: 'btn btn-xs btn-outline-danger btn-delete-sched',
      title: 'Delete',
      html: '<i class="bi bi-trash"></i>',
    });
    delBtn.setAttribute('data-sched-id', s.id);
    delBtn.setAttribute('data-sched-name', s.name);
    actionsWrap.appendChild(delBtn);

    actionsTd.appendChild(actionsWrap);
    row.appendChild(actionsTd);

    bindRowActions(row);
    updateCountdowns();
  }

  function appendRow(s) {
    // If this is the first schedule, reload to show the table instead of the empty state
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
    document.getElementById('sched-mode').value = 'weekly';
    document.getElementById('sched-time').value = '';
    document.getElementById('sched-run-date').value = '';
    document.getElementById('sched-end-date').value = '';
    document.getElementById('sched-enabled').checked = true;
    document.getElementById('sched-persistent').checked = false;
    document.getElementById('sched-persist-minutes').value = '60';
    document.getElementById('persist-options').style.display = 'none';
    document.querySelectorAll('.day-check').forEach(cb => { cb.checked = false; });
    applyModeUI('weekly');
  }

  function openEditModal(btn) {
    clearModal();
    document.getElementById('schedule-modal-title').textContent = 'Edit Schedule';
    document.getElementById('sched-edit-id').value = btn.dataset.schedId;
    document.getElementById('sched-name').value = btn.dataset.name;
    document.getElementById('sched-snapshot').value = btn.dataset.snapshotId;
    document.getElementById('sched-time').value = btn.dataset.time;
    document.getElementById('sched-enabled').checked = btn.dataset.enabled === 'true';

    const mode = btn.dataset.mode || 'weekly';
    document.getElementById('sched-mode').value = mode;
    document.getElementById('sched-run-date').value = btn.dataset.runDate || '';
    document.getElementById('sched-end-date').value = btn.dataset.endDate || '';
    applyModeUI(mode);

    const persistent = btn.dataset.persistent === 'true';
    document.getElementById('sched-persistent').checked = persistent;
    document.getElementById('sched-persist-minutes').value = btn.dataset.persistMinutes || '60';
    document.getElementById('persist-options').style.display = persistent ? '' : 'none';

    const days = (btn.dataset.days || '').split(',');
    document.querySelectorAll('.day-check').forEach(cb => {
      cb.checked = days.includes(cb.value);
    });
    modal.show();
  }

  // ── save (create or update) ────────────────────────────────────────────────

  document.getElementById('btn-save-schedule')?.addEventListener('click', async () => {
    const editId      = document.getElementById('sched-edit-id').value;
    const name        = document.getElementById('sched-name').value.trim();
    const snapId      = document.getElementById('sched-snapshot').value;
    const mode        = document.getElementById('sched-mode').value;
    const time        = document.getElementById('sched-time').value;
    const runDate     = document.getElementById('sched-run-date').value;
    const endDate     = document.getElementById('sched-end-date').value;
    const enabled     = document.getElementById('sched-enabled').checked;
    const persistent  = document.getElementById('sched-persistent').checked;
    const persistMins = parseInt(document.getElementById('sched-persist-minutes').value) || 60;
    const days        = [...document.querySelectorAll('.day-check:checked')].map(cb => cb.value);

    if (!name)                              { window.Leash.toast('Name is required', 'warning'); return; }
    if (!snapId)                            { window.Leash.toast('Select a snapshot', 'warning'); return; }
    if (!time)                              { window.Leash.toast('Set a time', 'warning'); return; }
    if (mode === 'once' && !runDate)        { window.Leash.toast('Date is required', 'warning'); return; }
    if (mode !== 'once' && !days.length)   { window.Leash.toast('Select at least one day', 'warning'); return; }

    const body = {
      name,
      snapshot_id: parseInt(snapId),
      schedule_mode: mode,
      time_of_day: time,
      enabled,
      persistent,
      persist_minutes: persistMins,
    };

    if (mode === 'once') {
      body.run_date = runDate;
    } else {
      body.days_of_week = days.join(',');
      if (mode === 'weekly_until' && endDate) body.end_date = endDate;
    }

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

  // ── row-level actions ──────────────────────────────────────────────────────

  function bindRowActions(root) {
    root.querySelector('.btn-toggle-sched')?.addEventListener('click', async function () {
      const id   = this.dataset.schedId;
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

    root.querySelector('.btn-stop-enforcement')?.addEventListener('click', async function () {
      const id   = this.dataset.schedId;
      const resp = await fetch(`/api/schedules/${id}/enforcement`, { method: 'DELETE' });
      const data = await resp.json();
      if (resp.ok) {
        renderRow(data);
        window.Leash.toast('Enforcement stopped', 'warning');
      }
    });
  }

  // Bind actions on server-rendered rows
  document.querySelectorAll('#schedules-table-body tr').forEach(bindRowActions);
})();
