/**
 * PTZ camera detail page controller.
 *
 * PTZ / focus buttons use press-and-hold (pointerdown → move, pointerup/leave → stop).
 * BirdDog cameras keep moving until an explicit STOP is sent.
 */
document.addEventListener('DOMContentLoaded', () => {
  const cameraId = window.LEASH?.cameraId;
  if (!cameraId) return;

  const toast = window.Leash?.toast || (() => {});

  // ── Helpers ────────────────────────────────────────────────────────────────
  async function api(path, body) {
    try {
      const resp = await fetch(`/api/cameras/${cameraId}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const text = await resp.text();
      try { return JSON.parse(text); } catch { return { status: resp.status }; }
    } catch (err) {
      toast(`Request failed: ${err.message}`, 'danger');
      return { status: 0 };
    }
  }

  // ── Speed slider ───────────────────────────────────────────────────────────
  const speedSlider = document.getElementById('ptz-speed');
  const speedVal    = document.getElementById('ptz-speed-val');
  speedSlider?.addEventListener('input', () => {
    speedVal.textContent = speedSlider.value;
  });
  function getSpeed() { return parseInt(speedSlider?.value || '8', 10); }

  // ── PTZ press-and-hold ─────────────────────────────────────────────────────
  let ptzActive = false;

  async function ptzSend(pan, tilt, zoom) {
    await api('/ptz', { pan, tilt, zoom, speed: getSpeed() });
  }

  async function ptzStop() {
    if (!ptzActive) return;
    ptzActive = false;
    const d = await api('/ptz', { pan: 'STOP', tilt: 'STOP', zoom: 'STOP', speed: 1 });
    if (d?.status && d.status !== 200) toast(`PTZ error (HTTP ${d.status})`, 'danger');
  }

  document.querySelectorAll('.ptz-btn').forEach(btn => {
    const pan  = btn.dataset.pan;
    const tilt = btn.dataset.tilt;
    const zoom = btn.dataset.zoom;

    btn.addEventListener('pointerdown', async e => {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      ptzActive = true;
      await ptzSend(pan, tilt, zoom);
    });
    btn.addEventListener('pointerup',     () => ptzStop());
    btn.addEventListener('pointercancel', () => ptzStop());
    btn.addEventListener('pointerleave',  () => ptzStop());
  });

  document.querySelectorAll('.ptz-stop-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      ptzActive = false;
      await api('/ptz', { pan: 'STOP', tilt: 'STOP', zoom: 'STOP', speed: 1 });
    });
  });

  // ── Focus press-and-hold ───────────────────────────────────────────────────
  document.querySelectorAll('.focus-btn').forEach(btn => {
    const action = btn.dataset.action;
    const isHold = action !== 'AUTO';

    if (isHold) {
      btn.addEventListener('pointerdown', async e => {
        e.preventDefault();
        btn.setPointerCapture(e.pointerId);
        await api('/focus', { action });
      });
      btn.addEventListener('pointerup',     async () => api('/focus', { action: 'STOP' }));
      btn.addEventListener('pointercancel', async () => api('/focus', { action: 'STOP' }));
    } else {
      btn.addEventListener('click', async () => {
        await api('/focus', { action: 'AUTO' });
        toast('Auto focus enabled', 'info');
      });
    }
  });

  // ── Refresh status ─────────────────────────────────────────────────────────
  document.getElementById('btn-poll-camera')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-poll-camera');
    btn.disabled = true;
    try {
      const d = await fetch(`/api/cameras/${cameraId}/status`).then(r => r.json());
      toast(d.status === 200 ? 'Status updated' : 'Device offline', d.status === 200 ? 'info' : 'warning');
    } finally {
      btn.disabled = false;
    }
  });

  // ── Preset recall ──────────────────────────────────────────────────────────
  function bindPresetButtons() {
    document.querySelectorAll('.btn-recall-preset').forEach(btn => {
      btn.addEventListener('click', async () => {
        const num = btn.dataset.presetNum;
        btn.disabled = true;
        try {
          const d = await api(`/presets/${num}/recall`, {});
          toast(d.status === 200 ? `Preset ${num} recalled` : `Recall failed (${d.status})`,
                d.status === 200 ? 'success' : 'danger');
        } finally {
          btn.disabled = false;
        }
      });
    });

    document.querySelectorAll('.btn-delete-preset').forEach(btn => {
      btn.addEventListener('click', async () => {
        const num = btn.dataset.presetNum;
        if (!confirm(`Delete label for preset ${num}?`)) return;
        const resp = await fetch(`/api/cameras/${cameraId}/presets/${num}`, { method: 'DELETE' });
        if (resp.ok) {
          btn.closest('.preset-row').querySelector('.preset-name').textContent = '';
          btn.remove();
          toast(`Preset ${num} label removed`, 'warning');
        } else {
          toast('Delete failed', 'danger');
        }
      });
    });
  }

  bindPresetButtons();

  // ── Save preset form ───────────────────────────────────────────────────────
  document.getElementById('btn-save-preset-open')?.addEventListener('click', () => {
    document.getElementById('save-preset-form').style.display = '';
  });
  document.getElementById('btn-save-preset-cancel')?.addEventListener('click', () => {
    document.getElementById('save-preset-form').style.display = 'none';
  });
  document.getElementById('btn-save-preset-confirm')?.addEventListener('click', async () => {
    const num  = parseInt(document.getElementById('save-preset-num').value, 10);
    const name = document.getElementById('save-preset-name').value.trim();
    if (isNaN(num) || num < 1 || num > 99) { toast('Preset number must be 1–99', 'warning'); return; }
    if (!name) { toast('Label is required', 'warning'); return; }

    const d = await api(`/presets/${num}/save`, { name });
    if (d.status === 200) {
      toast(`Preset ${num} saved`, 'success');
      document.getElementById('save-preset-form').style.display = 'none';

      let row = document.querySelector(`.preset-row[data-preset-num="${num}"]`);
      if (row) {
        row.querySelector('.preset-name').textContent = name;
        // Add delete button if not already present
        if (!row.querySelector('.btn-delete-preset')) {
          const delBtn = document.createElement('button');
          delBtn.className = 'btn btn-xs btn-outline-danger btn-delete-preset';
          delBtn.dataset.presetNum = num;
          const icon = document.createElement('i');
          icon.className = 'bi bi-trash';
          delBtn.appendChild(icon);
          row.appendChild(delBtn);
          bindPresetButtons();
        }
      } else {
        row = document.createElement('div');
        row.className = 'd-flex align-items-center gap-2 mb-2 preset-row';
        row.dataset.presetNum = num;
        row.innerHTML = '';

        const badge = document.createElement('span');
        badge.className = 'badge bg-secondary';
        badge.style.minWidth = '2.5rem';
        badge.textContent = num;

        const label = document.createElement('span');
        label.className = 'flex-grow-1 preset-name';
        label.textContent = name;

        const recallBtn = document.createElement('button');
        recallBtn.className = 'btn btn-xs btn-outline-primary btn-recall-preset';
        recallBtn.dataset.presetNum = num;
        recallBtn.textContent = 'Recall';

        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-xs btn-outline-danger btn-delete-preset';
        delBtn.dataset.presetNum = num;
        const icon = document.createElement('i');
        icon.className = 'bi bi-trash';
        delBtn.appendChild(icon);

        row.appendChild(badge);
        row.appendChild(label);
        row.appendChild(recallBtn);
        row.appendChild(delBtn);

        // Insert in sorted order
        const list = document.getElementById('preset-list');
        const existing = Array.from(list.querySelectorAll('.preset-row'));
        const after = existing.find(r => parseInt(r.dataset.presetNum) > num);
        if (after) list.insertBefore(row, after);
        else list.appendChild(row);

        bindPresetButtons();
      }
    } else {
      toast(`Save failed (${d.status})`, 'danger');
    }
  });

});
