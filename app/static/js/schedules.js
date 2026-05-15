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

    const dupBtn = h('button', {
      class: 'btn btn-xs btn-outline-info btn-duplicate-sched',
      title: 'Duplicate & reschedule',
      html: '<i class="bi bi-files"></i>',
    });
    dupBtn.setAttribute('data-sched-id', s.id);
    dupBtn.setAttribute('data-sched-name', s.name);
    actionsWrap.appendChild(dupBtn);

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

  // duplicateOf is set to the source schedule's id when the modal is opened
  // for "duplicate & reschedule"; cleared otherwise.
  let duplicateOf = null;

  function clearModal() {
    duplicateOf = null;
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

    let url, method;
    if (duplicateOf) {
      url = `/api/schedules/${duplicateOf}/duplicate`;
      method = 'POST';
    } else if (editId) {
      url = `/api/schedules/${editId}`;
      method = 'PUT';
    } else {
      url = '/api/schedules';
      method = 'POST';
    }

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
    if (duplicateOf) {
      duplicateOf = null;
      appendRow(data);
      window.Leash.toast('Schedule duplicated', 'success');
    } else if (editId) {
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

    root.querySelector('.btn-duplicate-sched')?.addEventListener('click', function () {
      // Reuse the edit form pre-filled from the sibling .btn-edit-sched data,
      // then flip into duplicate mode so save POSTs to /duplicate.
      const editBtn = root.querySelector('.btn-edit-sched');
      if (!editBtn) return;
      openEditModal(editBtn);
      duplicateOf = this.dataset.schedId;
      document.getElementById('sched-edit-id').value = '';
      document.getElementById('schedule-modal-title').textContent = 'Duplicate & Reschedule';
      const nameInput = document.getElementById('sched-name');
      if (nameInput && !nameInput.value.endsWith('(copy)')) nameInput.value += ' (copy)';
      // Default the duplicate to disabled — user typically wants to set new date first.
      document.getElementById('sched-enabled').checked = false;
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

  // Bind actions on server-rendered rows (active + past tables)
  document.querySelectorAll('#schedules-table-body tr, #past-schedules-table-body tr').forEach(bindRowActions);

  // ── Past-schedules toggle ──────────────────────────────────────────────────

  document.getElementById('btn-toggle-past')?.addEventListener('click', function () {
    const wrap = document.getElementById('past-schedules-wrap');
    if (!wrap) return;
    const shown = this.dataset.shown === 'true';
    wrap.style.display = shown ? 'none' : '';
    this.dataset.shown = shown ? 'false' : 'true';
    const pastBody = document.getElementById('past-schedules-table-body');
    const n = pastBody ? pastBody.querySelectorAll('tr').length : 0;
    const label = document.getElementById('btn-toggle-past-label');
    if (label) {
      const noun = n === 1 ? 'past schedule' : 'past schedules';
      label.textContent = shown ? `Show ${n} ${noun}` : `Hide ${n} ${noun}`;
    }
  });

  // ── View toggle (List / Month / Week) ──────────────────────────────────────

  const panes = {
    list:  document.getElementById('view-list-pane'),
    month: document.getElementById('view-month-pane'),
    week:  document.getElementById('view-week-pane'),
  };

  function showView(name) {
    for (const k in panes) if (panes[k]) panes[k].style.display = (k === name) ? '' : 'none';
    if (name === 'month' && !monthState.rendered) renderMonth();
    if (name === 'week'  && !weekState.rendered)  renderWeek();
    try { localStorage.setItem('leash.schedules.view', name); } catch (_) {}
  }

  document.querySelectorAll('input[name="sched-view"]').forEach(input => {
    input.addEventListener('change', () => { if (input.checked) showView(input.value); });
  });

  // Restore last view choice
  try {
    const saved = localStorage.getItem('leash.schedules.view');
    if (saved && panes[saved]) {
      const radio = document.getElementById(`view-${saved}`);
      if (radio) { radio.checked = true; showView(saved); }
    }
  } catch (_) {}

  // ── Calendar shared helpers ────────────────────────────────────────────────

  const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const MONTH_LABELS = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];

  function ymd(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }

  function parseYmd(s) {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  function startOfWeekMonday(d) {
    // 0=Sun, 1=Mon … 6=Sat. Shift to Monday-first.
    const out = new Date(d);
    const dow = (out.getDay() + 6) % 7;  // 0=Mon … 6=Sun
    out.setDate(out.getDate() - dow);
    out.setHours(0, 0, 0, 0);
    return out;
  }

  async function fetchOccurrences(startStr, endStr) {
    const url = `/api/schedules/occurrences?start=${startStr}&end=${endStr}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      window.Leash.toast('Failed to load schedule occurrences', 'danger');
      return [];
    }
    return resp.json();
  }

  function eventTooltip(occ) {
    const lines = [occ.name, `${occ.date} ${occ.time_of_day}`];
    if (occ.snapshot_name) lines.push(`Snapshot: ${occ.snapshot_name}`);
    if (occ.camera_name)   lines.push(`Camera: ${occ.camera_name} #${occ.preset_number}`);
    if (occ.persistent)    lines.push('Persistent');
    if (!occ.enabled)      lines.push('(disabled)');
    return lines.join('\n');
  }

  function openEditForScheduleId(id) {
    // Find the rendered edit button for this schedule and reuse its dataset.
    const btn = document.querySelector(`.btn-edit-sched[data-sched-id="${id}"]`);
    if (btn) { openEditModal(btn); return; }
    // Past-events table was hidden but the row still exists — same selector covers it.
    // If for some reason the row is absent (e.g. page hasn't fetched it), fall back to a fetch.
    fetch(`/api/schedules/${id}`).then(r => r.json()).then(s => {
      const fake = { dataset: {
        schedId: String(s.id),
        name: s.name,
        snapshotId: s.snapshot_id || '',
        mode: s.schedule_mode || 'weekly',
        days: s.days_of_week || '',
        runDate: s.run_date || '',
        endDate: s.end_date || '',
        time: s.time_of_day,
        enabled: String(s.enabled),
        persistent: String(s.persistent),
        persistMinutes: String(s.persist_minutes),
      }};
      openEditModal(fake);
    });
  }

  function openCreateForDate(dateStr, timeStr) {
    clearModal();
    document.getElementById('sched-mode').value = 'once';
    applyModeUI('once');
    document.getElementById('sched-run-date').value = dateStr;
    if (timeStr) document.getElementById('sched-time').value = timeStr;
    modal.show();
  }

  async function rescheduleOccurrence(scheduleId, scheduleMode, newDateStr, newTimeStr) {
    const body = {};
    if (newDateStr) body.run_date = newDateStr;
    if (newTimeStr) body.time_of_day = newTimeStr;
    const resp = await fetch(`/api/schedules/${scheduleId}/reschedule`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      window.Leash.toast(data.error || 'Reschedule failed', 'danger');
      return false;
    }
    window.Leash.toast('Schedule moved', 'success');
    // Re-render whatever calendar view is open so things update without a reload.
    monthState.rendered = false;
    weekState.rendered = false;
    const active = document.querySelector('input[name="sched-view"]:checked');
    if (active && active.value === 'month') renderMonth();
    else if (active && active.value === 'week') renderWeek();
    return true;
  }

  // ── Month view ─────────────────────────────────────────────────────────────

  const monthState = { year: 0, month: 0, rendered: false };
  (function initMonth() {
    const now = new Date();
    monthState.year = now.getFullYear();
    monthState.month = now.getMonth();
  })();

  document.getElementById('cal-prev')?.addEventListener('click', () => {
    monthState.month -= 1;
    if (monthState.month < 0) { monthState.month = 11; monthState.year -= 1; }
    renderMonth();
  });
  document.getElementById('cal-next')?.addEventListener('click', () => {
    monthState.month += 1;
    if (monthState.month > 11) { monthState.month = 0; monthState.year += 1; }
    renderMonth();
  });
  document.getElementById('cal-today')?.addEventListener('click', () => {
    const now = new Date();
    monthState.year = now.getFullYear();
    monthState.month = now.getMonth();
    renderMonth();
  });

  async function renderMonth() {
    const grid = document.getElementById('month-grid');
    if (!grid) return;
    monthState.rendered = true;

    const first = new Date(monthState.year, monthState.month, 1);
    const last = new Date(monthState.year, monthState.month + 1, 0);
    document.getElementById('cal-title').textContent =
      `${MONTH_LABELS[monthState.month]} ${monthState.year}`;

    // Show the 6 weeks that cover this month, Monday-first.
    const gridStart = startOfWeekMonday(first);
    const gridEnd = new Date(gridStart);
    gridEnd.setDate(gridEnd.getDate() + 41);  // 6 weeks * 7 days - 1

    grid.textContent = '';

    // Header row
    const head = document.createElement('div');
    head.className = 'month-grid-head';
    DOW_LABELS.forEach(label => {
      const c = document.createElement('div');
      c.className = 'month-grid-head-cell';
      c.textContent = label;
      head.appendChild(c);
    });
    grid.appendChild(head);

    // Body
    const body = document.createElement('div');
    body.className = 'month-grid-body';
    grid.appendChild(body);

    const today = ymd(new Date());
    const dayByDate = new Map();
    const cur = new Date(gridStart);
    for (let i = 0; i < 42; i++) {
      const cell = document.createElement('div');
      const dateStr = ymd(cur);
      cell.className = 'month-cell';
      cell.dataset.date = dateStr;
      if (cur.getMonth() !== monthState.month) cell.classList.add('out-of-month');
      if (dateStr === today) cell.classList.add('today');

      const cellHead = document.createElement('div');
      cellHead.className = 'month-cell-head';
      const num = document.createElement('span');
      num.className = 'month-cell-num';
      num.textContent = String(cur.getDate());
      cellHead.appendChild(num);
      cell.appendChild(cellHead);

      const events = document.createElement('div');
      events.className = 'month-cell-events';
      cell.appendChild(events);

      // Click empty area → create new schedule on this date
      cell.addEventListener('click', e => {
        if (e.target.closest('.month-event')) return;
        openCreateForDate(dateStr, '');
      });

      // Drag target
      cell.addEventListener('dragover', e => {
        if (dragState.scheduleId) { e.preventDefault(); cell.classList.add('drag-over'); }
      });
      cell.addEventListener('dragleave', () => cell.classList.remove('drag-over'));
      cell.addEventListener('drop', e => {
        cell.classList.remove('drag-over');
        if (!dragState.scheduleId) return;
        e.preventDefault();
        const targetDate = cell.dataset.date;
        const { scheduleId, mode } = dragState;
        dragState.scheduleId = null;
        if (mode !== 'once') {
          window.Leash.toast('Only one-time schedules can be moved. Use Edit for recurring.', 'warning');
          return;
        }
        rescheduleOccurrence(scheduleId, mode, targetDate, '');
      });

      dayByDate.set(dateStr, events);
      body.appendChild(cell);
      cur.setDate(cur.getDate() + 1);
    }

    const occurrences = await fetchOccurrences(ymd(gridStart), ymd(gridEnd));
    // Sort by time so events appear in chronological order within a day
    occurrences.sort((a, b) => a.time_of_day.localeCompare(b.time_of_day));

    occurrences.forEach(occ => {
      const slot = dayByDate.get(occ.date);
      if (!slot) return;
      slot.appendChild(buildMonthEvent(occ));
    });
  }

  function buildMonthEvent(occ) {
    const pill = document.createElement('div');
    const cls = ['month-event'];
    if (!occ.enabled) cls.push('disabled');
    if (occ.is_past) cls.push('past');
    if (occ.persistent) cls.push('persistent');
    if (occ.schedule_mode === 'once') cls.push('draggable');
    pill.className = cls.join(' ');
    pill.title = eventTooltip(occ);
    pill.dataset.scheduleId = occ.schedule_id;
    pill.dataset.mode = occ.schedule_mode;

    const t = document.createElement('span');
    t.className = 'month-event-time';
    t.textContent = occ.time_of_day;
    pill.appendChild(t);

    const n = document.createElement('span');
    n.className = 'month-event-name';
    n.textContent = ' ' + occ.name;
    pill.appendChild(n);

    pill.addEventListener('click', e => {
      e.stopPropagation();
      openEditForScheduleId(occ.schedule_id);
    });

    if (occ.schedule_mode === 'once') {
      pill.draggable = true;
      pill.addEventListener('dragstart', e => {
        dragState.scheduleId = occ.schedule_id;
        dragState.mode = occ.schedule_mode;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(occ.schedule_id));
      });
      pill.addEventListener('dragend', () => {
        dragState.scheduleId = null;
        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
      });
    }
    return pill;
  }

  // ── Week view ──────────────────────────────────────────────────────────────

  const weekState = { start: null, rendered: false };
  (function initWeek() {
    weekState.start = startOfWeekMonday(new Date());
  })();

  document.getElementById('week-prev')?.addEventListener('click', () => {
    weekState.start.setDate(weekState.start.getDate() - 7);
    renderWeek();
  });
  document.getElementById('week-next')?.addEventListener('click', () => {
    weekState.start.setDate(weekState.start.getDate() + 7);
    renderWeek();
  });
  document.getElementById('week-today')?.addEventListener('click', () => {
    weekState.start = startOfWeekMonday(new Date());
    renderWeek();
  });

  const WEEK_HOUR_START = 0;
  const WEEK_HOUR_END = 24;  // hours 0..23 shown
  const WEEK_PX_PER_HOUR = 36;

  async function renderWeek() {
    const grid = document.getElementById('week-grid');
    if (!grid) return;
    weekState.rendered = true;

    const start = new Date(weekState.start);
    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    document.getElementById('week-title').textContent =
      `${start.toLocaleDateString(undefined, {month:'short', day:'numeric'})} – ${end.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'})}`;

    grid.textContent = '';

    // Header row: blank + 7 day labels
    const head = document.createElement('div');
    head.className = 'week-grid-head';
    head.appendChild(document.createElement('div'));  // spacer
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(d.getDate() + i);
      const c = document.createElement('div');
      c.className = 'week-grid-head-cell';
      if (ymd(d) === ymd(new Date())) c.classList.add('today');
      c.innerHTML = `<div class="week-dow">${DOW_LABELS[i]}</div><div class="week-day-num">${d.getDate()}</div>`;
      head.appendChild(c);
    }
    grid.appendChild(head);

    // Body: hour rows × 7 day columns
    const body = document.createElement('div');
    body.className = 'week-grid-body';
    body.style.setProperty('--week-hours', String(WEEK_HOUR_END - WEEK_HOUR_START));
    body.style.setProperty('--week-px-per-hour', `${WEEK_PX_PER_HOUR}px`);

    // Hour labels column
    const hours = document.createElement('div');
    hours.className = 'week-hours-col';
    for (let h = WEEK_HOUR_START; h < WEEK_HOUR_END; h++) {
      const row = document.createElement('div');
      row.className = 'week-hour-row';
      row.textContent = `${String(h).padStart(2,'0')}:00`;
      hours.appendChild(row);
    }
    body.appendChild(hours);

    const dayCols = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(d.getDate() + i);
      const dateStr = ymd(d);
      const col = document.createElement('div');
      col.className = 'week-day-col';
      col.dataset.date = dateStr;

      for (let h = WEEK_HOUR_START; h < WEEK_HOUR_END; h++) {
        const slot = document.createElement('div');
        slot.className = 'week-hour-slot';
        slot.dataset.hour = String(h);
        slot.addEventListener('click', () => {
          openCreateForDate(dateStr, `${String(h).padStart(2,'0')}:00`);
        });
        slot.addEventListener('dragover', e => {
          if (dragState.scheduleId) { e.preventDefault(); slot.classList.add('drag-over'); }
        });
        slot.addEventListener('dragleave', () => slot.classList.remove('drag-over'));
        slot.addEventListener('drop', e => {
          slot.classList.remove('drag-over');
          if (!dragState.scheduleId) return;
          e.preventDefault();
          const rect = slot.getBoundingClientRect();
          const offset = e.clientY - rect.top;
          const fraction = Math.max(0, Math.min(1, offset / rect.height));
          // Snap to 15-minute increments
          const totalMinutes = Math.round((h * 60 + fraction * 60) / 15) * 15;
          const hh = String(Math.floor(totalMinutes / 60) % 24).padStart(2, '0');
          const mm = String(totalMinutes % 60).padStart(2, '0');
          const newTime = `${hh}:${mm}`;
          const { scheduleId, mode, sourceDate } = dragState;
          dragState.scheduleId = null;
          const sameDay = sourceDate === dateStr;
          if (!sameDay && mode !== 'once') {
            window.Leash.toast('Only one-time schedules can change date. Edit recurring schedules to change days.', 'warning');
            return;
          }
          rescheduleOccurrence(scheduleId, mode, sameDay ? '' : dateStr, newTime);
        });
        col.appendChild(slot);
      }
      dayCols.push({ date: dateStr, el: col });
      body.appendChild(col);
    }
    grid.appendChild(body);

    const occurrences = await fetchOccurrences(ymd(start), ymd(end));
    occurrences.forEach(occ => {
      const dc = dayCols.find(c => c.date === occ.date);
      if (!dc) return;
      const [hh, mm] = occ.time_of_day.split(':').map(Number);
      const topPx = ((hh - WEEK_HOUR_START) + mm / 60) * WEEK_PX_PER_HOUR;
      const ev = buildWeekEvent(occ, topPx);
      dc.el.appendChild(ev);
    });
  }

  function buildWeekEvent(occ, topPx) {
    const block = document.createElement('div');
    const cls = ['week-event'];
    if (!occ.enabled) cls.push('disabled');
    if (occ.is_past) cls.push('past');
    if (occ.persistent) cls.push('persistent');
    block.className = cls.join(' ');
    block.style.top = `${topPx}px`;
    block.title = eventTooltip(occ);
    block.dataset.scheduleId = occ.schedule_id;
    block.dataset.mode = occ.schedule_mode;

    const t = document.createElement('div');
    t.className = 'week-event-time';
    t.textContent = occ.time_of_day;
    block.appendChild(t);

    const n = document.createElement('div');
    n.className = 'week-event-name';
    n.textContent = occ.name;
    block.appendChild(n);

    block.addEventListener('click', e => {
      e.stopPropagation();
      openEditForScheduleId(occ.schedule_id);
    });

    block.draggable = true;
    block.addEventListener('dragstart', e => {
      dragState.scheduleId = occ.schedule_id;
      dragState.mode = occ.schedule_mode;
      dragState.sourceDate = occ.date;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(occ.schedule_id));
    });
    block.addEventListener('dragend', () => {
      dragState.scheduleId = null;
      document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    });
    return block;
  }

  // Shared drag state for month + week views
  const dragState = { scheduleId: null, mode: null, sourceDate: null };
})();
