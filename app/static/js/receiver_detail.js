/**
 * Receiver detail page — settings panels and live controls.
 */
document.addEventListener('DOMContentLoaded', () => {
  const receiverId = window.LEASH?.receiverId;
  if (!receiverId) return;

  // ── Refresh device info ─────────────────────────────────────────────────
  function applyDeviceInfo(data) {
    if (!data) return;
    const hn = document.getElementById('detail-hostname');
    if (hn) hn.textContent = data.hostname || '—';
    const st = document.getElementById('detail-status');
    if (st) st.textContent = data.status;
    const fw = document.getElementById('detail-firmware');
    if (fw) fw.textContent = data.firmware_version || '—';
    const sn = document.getElementById('detail-serial');
    if (sn) sn.textContent = data.serial_number || '—';
    const vf = document.getElementById('detail-format');
    if (vf) vf.textContent = data.video_format || '—';
    if (data.current_source) {
      const cs = document.getElementById('detail-current-source');
      if (cs) cs.textContent = data.current_source;
      const sel = document.getElementById('detail-source-select');
      if (sel && sel.value !== data.current_source) {
        if (!Array.from(sel.options).some(o => o.value === data.current_source)) {
          sel.insertBefore(new Option(data.current_source, data.current_source), sel.options[1]);
        }
        sel.value = data.current_source;
      }
    }
  }

  // Manual button hits the live device. Background poll reads the DB
  // (kept fresh by the backend RECEIVER_POLL_INTERVAL job) so the UI
  // updates without firing a fresh 3-call burst per tick.
  async function refreshFromDevice() {
    try {
      const data = await window.Leash.pollReceiver(receiverId);
      applyDeviceInfo(data);
    } catch (_e) { /* network blip */ }
  }

  async function refreshFromDb() {
    try {
      const resp = await fetch(`/api/receivers/${receiverId}`);
      if (!resp.ok) return;
      applyDeviceInfo(await resp.json());
    } catch (_e) { /* network blip */ }
  }

  document.getElementById('btn-poll')?.addEventListener('click', refreshFromDevice);

  // Auto-refresh every 15s so out-of-band source changes show up.
  (function () {
    const INTERVAL_MS = 15000;
    let timer = null;
    function start() {
      if (timer) return;
      timer = setInterval(() => { if (!document.hidden) refreshFromDb(); }, INTERVAL_MS);
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop();
      else { start(); refreshFromDb(); }
    });
    start();
  })();

  // ── Reboot / restart ───────────────────────────────────────────────────
  document.getElementById('btn-reboot')?.addEventListener('click', () =>
    window.Leash.rebootReceiver(receiverId));

  document.getElementById('btn-restart')?.addEventListener('click', () =>
    window.Leash.restartReceiver(receiverId));

  // ── Set source ─────────────────────────────────────────────────────────
  document.getElementById('btn-set-source')?.addEventListener('click', () => {
    const sel = document.getElementById('detail-source-select');
    if (!sel?.value) return;
    window.Leash.setSource(receiverId, sel.value).then(() => {
      document.getElementById('detail-current-source').textContent = sel.value;
      loadHistory();
    });
  });

  // ── Activity history ───────────────────────────────────────────────────
  // Server text fields are device/operator-supplied — render with textContent.
  const EVENT_BADGES = {
    SOURCE_CHANGE:        'bg-success',
    SOURCE_CHANGE_FAILED: 'bg-warning text-dark',
    RECEIVER_ONLINE:      'bg-success',
    RECEIVER_OFFLINE:     'bg-danger',
    RECEIVER_ADDED:       'bg-info text-dark',
    DEVICE_ERROR:         'bg-danger',
  };

  function fmtTimestamp(iso) {
    if (!iso) return '—';
    const d = new Date(iso + 'Z');
    return isNaN(d) ? iso : d.toLocaleString();
  }

  function renderHistory(events) {
    const tbody   = document.getElementById('history-body');
    const wrap    = document.getElementById('history-wrap');
    const empty   = document.getElementById('history-empty');
    const loading = document.getElementById('history-loading');
    const summary = document.getElementById('history-summary');

    if (loading) loading.style.display = 'none';
    if (!tbody) return;

    tbody.textContent = '';
    if (!events || !events.length) {
      if (wrap)  wrap.style.display  = 'none';
      if (empty) empty.style.display = '';
      if (summary) summary.textContent = '';
      return;
    }

    if (empty) empty.style.display = 'none';
    if (wrap)  wrap.style.display  = '';
    if (summary) summary.textContent = `(${events.length} most recent)`;

    events.forEach(e => {
      const tr = document.createElement('tr');

      const tdWhen = document.createElement('td');
      tdWhen.className = 'text-muted font-monospace';
      tdWhen.textContent = fmtTimestamp(e.timestamp);
      tr.appendChild(tdWhen);

      const tdType = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `badge ${EVENT_BADGES[e.event_type] || 'bg-secondary'}`;
      badge.textContent = e.event_type;
      tdType.appendChild(badge);
      tr.appendChild(tdType);

      const tdDetail = document.createElement('td');
      tdDetail.textContent = e.detail || '';
      tr.appendChild(tdDetail);

      tbody.appendChild(tr);
    });
  }

  async function loadHistory() {
    const loading = document.getElementById('history-loading');
    if (loading) loading.style.display = '';
    try {
      const resp = await fetch(`/api/receivers/${receiverId}/history?limit=100`);
      if (!resp.ok) {
        renderHistory([]);
        return;
      }
      renderHistory(await resp.json());
    } catch (_e) {
      renderHistory([]);
    }
  }

  document.getElementById('btn-refresh-history')?.addEventListener('click', loadHistory);
  loadHistory();

  // ── Settings panels — lazy load on tab click ────────────────────────────
  // Settings keys and values come from the BirdDog device (untrusted).
  // Build the form with createElement so the device can never inject HTML.
  function renderSettingsForm(container, group, data) {
    container.textContent = '';

    if (typeof data !== 'object' || data === null) {
      const pre = document.createElement('pre');
      pre.className = 'text-warning small';
      pre.textContent = JSON.stringify(data, null, 2);
      container.appendChild(pre);
      return;
    }

    const isReadOnly = container.dataset.readonly === 'true';
    const form = document.createElement('form');
    form.className = 'row settings-form';
    form.dataset.group = group;

    Object.entries(data).forEach(([key, val]) => {
      const col = document.createElement('div');
      col.className = 'col-md-4 col-lg-3 mb-2 settings-form-group';
      const label = document.createElement('label');
      label.className = 'form-label mb-0';
      label.textContent = key;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'form-control form-control-sm settings-field';
      input.dataset.key = key;
      input.value = val ?? '';
      if (isReadOnly) input.readOnly = true;
      col.appendChild(label);
      col.appendChild(input);
      form.appendChild(col);
    });

    if (!isReadOnly) {
      const saveCol = document.createElement('div');
      saveCol.className = 'col-12 mt-2';
      const saveBtn = document.createElement('button');
      saveBtn.type = 'submit';
      saveBtn.className = 'btn btn-sm btn-primary';
      saveBtn.innerHTML = '<i class="bi bi-check2 me-1"></i>Save';
      saveCol.appendChild(saveBtn);
      form.appendChild(saveCol);
    }

    container.appendChild(form);

    if (!isReadOnly) {
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const payload = {};
        form.querySelectorAll('.settings-field').forEach(input => {
          payload[input.dataset.key] = input.value;
        });
        const resp = await fetch(`/api/receivers/${receiverId}/settings/${group}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await resp.json();
        window.Leash.toast(
          result.status === 200 ? 'Settings saved' : `Save failed (${result.status})`,
          result.status === 200 ? 'success' : 'danger'
        );
      });
    }
  }

  async function loadSettings(container) {
    const group = container.dataset.group;
    if (container.dataset.loaded) return;
    container.dataset.loaded = '1';

    container.innerHTML = '<div class="loader-overlay"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>';

    const resp = await fetch(`/api/receivers/${receiverId}/settings/${group}`);
    const result = await resp.json();

    if (!resp.ok || result.status !== 200) {
      container.innerHTML = `<div class="text-danger small p-2">Failed to load ${group} (${result.status ?? resp.status})</div>`;
      return;
    }

    // Receiver settings are display-only — write directly to the device instead.
    container.dataset.readonly = 'true';

    renderSettingsForm(container, group, result.data);
  }

  // Load the first visible tab immediately
  document.querySelectorAll('.settings-loader').forEach(container => {
    const tabPane = container.closest('.tab-pane');
    if (tabPane?.classList.contains('show')) {
      loadSettings(container);
    }
  });

  // Load on tab show
  document.querySelectorAll('#settingsTabs button[data-bs-toggle="tab"]').forEach(btn => {
    btn.addEventListener('shown.bs.tab', e => {
      const targetId = e.target.dataset.bsTarget;
      const pane = document.querySelector(targetId);
      pane?.querySelectorAll('.settings-loader').forEach(loadSettings);
    });
  });
});
