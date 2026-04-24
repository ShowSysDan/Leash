/**
 * Receiver detail page — settings panels and live controls.
 */
document.addEventListener('DOMContentLoaded', () => {
  const receiverId = window.LEASH?.receiverId;
  if (!receiverId) return;

  // ── Refresh device info ─────────────────────────────────────────────────
  document.getElementById('btn-poll')?.addEventListener('click', async () => {
    const data = await window.Leash.pollReceiver(receiverId);
    if (!data) return;
    document.getElementById('detail-hostname').textContent  = data.hostname || '—';
    document.getElementById('detail-status').textContent    = data.status;
    document.getElementById('detail-firmware').textContent  = data.firmware_version || '—';
    document.getElementById('detail-serial').textContent    = data.serial_number || '—';
    document.getElementById('detail-format').textContent    = data.video_format || '—';
    if (data.current_source)
      document.getElementById('detail-current-source').textContent = data.current_source;
  });

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
    });
  });

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

    // decode_status is read-only
    if (group === 'decode_status') {
      container.dataset.readonly = 'true';
    }

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
