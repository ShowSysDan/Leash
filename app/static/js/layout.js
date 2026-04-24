/**
 * Layout canvas — drag-and-drop positioning (edit mode) + source control (view mode).
 *
 * Positions are stored as percentage (0–100) of canvas width/height so the layout
 * scales correctly on any screen size.
 */
document.addEventListener('DOMContentLoaded', () => {
  const layoutId  = window.LEASH?.layoutId;
  const sources   = window.LEASH?.sources || [];
  let positions   = window.LEASH?.positions || [];  // array from server
  let editMode    = false;
  let dragging    = null;   // { el, offsetX, offsetY }

  const canvas    = document.getElementById('layout-canvas');
  const saveBtn   = document.getElementById('btn-save-layout');

  if (!canvas || !layoutId) return;

  // ── Populate a <select> with source options ─────────────────────────────
  // Source names come from NDI devices (untrusted) — use Option() which
  // assigns via text property, not innerHTML.
  function fillSourceOptions(select, current) {
    select.appendChild(new Option('— source —', ''));
    sources.forEach(name => {
      const opt = new Option(name, name);
      if (name === current) opt.selected = true;
      select.appendChild(opt);
    });
    select.appendChild(new Option('⚡ Reboot', 'Reboot'));
  }

  // ── Render a single card ────────────────────────────────────────────────
  // Build with createElement so device hostnames/labels cannot inject HTML.
  function makeCard(pos) {
    const r   = pos.receiver;
    const div = document.createElement('div');
    div.className = 'lc-card';
    div.dataset.receiverId  = pos.receiver_id;
    div.dataset.positionId  = pos.id;
    div.style.left = `${pos.x_pct}%`;
    div.style.top  = `${pos.y_pct}%`;

    const header = document.createElement('div');
    header.className = 'lc-card-header';
    const hostSpan = document.createElement('span');
    hostSpan.className = 'lc-hostname';
    hostSpan.textContent = r?.hostname || r?.label || `.${r?.ip_last_octet}`;
    const ixSpan = document.createElement('span');
    ixSpan.className = 'lc-index';
    ixSpan.textContent = r?.ip_last_octet ?? '';
    header.appendChild(hostSpan);
    header.appendChild(ixSpan);

    const sourceWrap = document.createElement('div');
    sourceWrap.className = 'lc-card-source';
    const select = document.createElement('select');
    select.className = 'lc-source-select';
    select.dataset.receiverId = pos.receiver_id;
    fillSourceOptions(select, r?.current_source);
    sourceWrap.appendChild(select);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'lc-remove-btn';
    removeBtn.dataset.receiverId = pos.receiver_id;
    removeBtn.title = 'Remove from layout';
    removeBtn.textContent = '×';

    div.appendChild(header);
    div.appendChild(sourceWrap);
    div.appendChild(removeBtn);

    // Source change
    div.querySelector('.lc-source-select').addEventListener('change', async e => {
      const name = e.target.value;
      if (!name) return;
      const resp = await fetch(`/api/receivers/${pos.receiver_id}/source`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_name: name }),
      });
      const data = await resp.json();
      window.Leash.toast(data.status === 200 ? `Set: ${name}` : `Failed (${data.status})`,
                         data.status === 200 ? 'success' : 'danger');
    });

    // Remove from layout
    div.querySelector('.lc-remove-btn').addEventListener('click', async () => {
      if (!confirm('Remove from layout?')) return;
      const resp = await fetch(`/api/layouts/${layoutId}/receivers/${pos.receiver_id}`, {
        method: 'DELETE',
      });
      if (resp.ok) {
        div.remove();
        positions = positions.filter(p => p.receiver_id !== pos.receiver_id);
        window.Leash.toast('Removed from layout', 'info');
      }
    });

    // Drag start
    div.addEventListener('mousedown', e => {
      if (!editMode) return;
      if (e.target.closest('select, button')) return;
      const rect = canvas.getBoundingClientRect();
      dragging = {
        el: div,
        offsetX: e.clientX - div.getBoundingClientRect().left,
        offsetY: e.clientY - div.getBoundingClientRect().top,
        receiverId: pos.receiver_id,
      };
      div.classList.add('dragging');
      e.preventDefault();
    });

    return div;
  }

  // ── Render all cards ────────────────────────────────────────────────────
  function renderAll() {
    canvas.querySelectorAll('.lc-card').forEach(c => c.remove());
    positions.forEach(pos => canvas.appendChild(makeCard(pos)));
  }

  renderAll();

  // ── Mouse move / up for drag ────────────────────────────────────────────
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    let x = (e.clientX - rect.left - dragging.offsetX) / rect.width  * 100;
    let y = (e.clientY - rect.top  - dragging.offsetY) / rect.height * 100;
    x = Math.max(0, Math.min(90, x));
    y = Math.max(0, Math.min(90, y));
    dragging.el.style.left = `${x}%`;
    dragging.el.style.top  = `${y}%`;
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    // Update local positions array
    const el   = dragging.el;
    const rid  = dragging.receiverId;
    const x    = parseFloat(el.style.left);
    const y    = parseFloat(el.style.top);
    const pos  = positions.find(p => p.receiver_id === rid);
    if (pos) { pos.x_pct = x; pos.y_pct = y; }
    el.classList.remove('dragging');
    dragging = null;
  });

  // ── Mode toggle ─────────────────────────────────────────────────────────
  document.getElementById('btn-view-mode')?.addEventListener('click', () => {
    editMode = false;
    canvas.classList.remove('edit-mode');
    document.getElementById('btn-view-mode').classList.add('active');
    document.getElementById('btn-edit-mode').classList.remove('active');
    if (saveBtn) saveBtn.style.display = 'none';
  });

  document.getElementById('btn-edit-mode')?.addEventListener('click', () => {
    editMode = true;
    canvas.classList.add('edit-mode');
    document.getElementById('btn-edit-mode').classList.add('active');
    document.getElementById('btn-view-mode').classList.remove('active');
    if (saveBtn) saveBtn.style.display = '';
  });

  // ── Save positions ───────────────────────────────────────────────────────
  saveBtn?.addEventListener('click', async () => {
    // Read current card positions from DOM
    const payload = Array.from(canvas.querySelectorAll('.lc-card')).map(card => ({
      receiver_id: parseInt(card.dataset.receiverId),
      x_pct: parseFloat(card.style.left),
      y_pct: parseFloat(card.style.top),
    }));

    const resp = await fetch(`/api/layouts/${layoutId}/positions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions: payload }),
    });
    const data = await resp.json();
    if (resp.ok) {
      positions = data.positions || [];
      window.Leash.toast('Layout saved', 'success');
    } else {
      window.Leash.toast('Save failed', 'danger');
    }
  });

  // ── Add receivers to layout ─────────────────────────────────────────────
  document.getElementById('btn-add-to-layout')?.addEventListener('click', () => {
    new bootstrap.Modal(document.getElementById('addToLayoutModal')).show();
  });

  document.getElementById('btn-confirm-add-to-layout')?.addEventListener('click', async () => {
    const checked = Array.from(document.querySelectorAll('.atl-check:checked'))
                         .map(cb => parseInt(cb.value));
    if (!checked.length) { window.Leash.toast('Select at least one receiver', 'warning'); return; }

    for (const rid of checked) {
      await fetch(`/api/layouts/${layoutId}/receivers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver_id: rid }),
      });
    }
    window.Leash.toast(`Added ${checked.length} receiver(s)`, 'success');
    setTimeout(() => location.reload(), 500);
  });

  // ── Periodic poll — update hostnames and source selections ───────────────
  async function pollAll() {
    const resp = await fetch('/api/receivers/bulk-reload');
    if (!resp.ok) return;
    const receivers = await resp.json();
    const map = {};
    receivers.forEach(r => { map[r.id] = r; });

    canvas.querySelectorAll('.lc-card').forEach(card => {
      const rid = parseInt(card.dataset.receiverId);
      const r   = map[rid];
      if (!r) return;
      const hn = card.querySelector('.lc-hostname');
      if (hn) hn.textContent = r.hostname || r.label || `.${r.ip_last_octet}`;
      const sel = card.querySelector('.lc-source-select');
      if (sel && r.current_source && sel.value !== r.current_source) {
        if (!Array.from(sel.options).some(o => o.value === r.current_source)) {
          sel.insertBefore(new Option(r.current_source, r.current_source), sel.options[1]);
        }
        sel.value = r.current_source;
      }
    });
  }

  // Poll every 30 seconds
  setInterval(pollAll, 30000);
});
