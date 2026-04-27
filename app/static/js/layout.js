/**
 * Layout canvas — drag-and-drop positioning (edit mode) + source control (view mode).
 *
 * Positions are stored as percentage (0–100) of canvas width/height so the layout
 * scales correctly on any screen size.
 *
 * Edit mode features:
 *   - Drag cards / labels to reposition
 *   - Shift+click to build a multi-selection; drag any selected card to move all
 *   - Snap toggle: rounds positions to the nearest 5% grid
 *   - Delete buttons only visible in edit mode
 */
document.addEventListener('DOMContentLoaded', () => {
  const layoutId  = window.LEASH?.layoutId;
  const sources   = window.LEASH?.sources || [];
  let positions   = window.LEASH?.positions || [];
  let editMode    = false;
  let snapEnabled = false;
  const SNAP      = 5;   // % grid step

  let dragging    = null;
  // dragging = { el, offsetX, offsetY, receiverId?, labelId?,
  //              primaryStartX, primaryStartY,
  //              peers: [{el, startX, startY, receiverId?, labelId?}] }

  let selected    = new Set();   // Set<Element> — current multi-selection

  let labels      = window.LEASH?.labels || [];

  const canvas    = document.getElementById('layout-canvas');
  const saveBtn   = document.getElementById('btn-save-layout');
  const snapBtn   = document.getElementById('btn-snap-grid');

  if (!canvas || !layoutId) return;

  // ── Source dropdown ─────────────────────────────────────────────────────
  function fillSourceOptions(select, current) {
    select.appendChild(new Option('— source —', ''));
    sources.forEach(name => {
      const opt = new Option(name, name);
      if (name === current) opt.selected = true;
      select.appendChild(opt);
    });
  }

  // ── Selection helpers ────────────────────────────────────────────────────
  function toggleSelect(el) {
    if (selected.has(el)) {
      selected.delete(el);
      el.classList.remove('lc-selected');
    } else {
      selected.add(el);
      el.classList.add('lc-selected');
    }
  }

  function clearSelection() {
    selected.forEach(el => el.classList.remove('lc-selected'));
    selected.clear();
  }

  // Clicking on bare canvas (not a card) clears the selection
  canvas.addEventListener('mousedown', e => {
    if (e.target === canvas || e.target.classList.contains('layout-title')) {
      clearSelection();
    }
  });

  // ── Drag helpers ─────────────────────────────────────────────────────────
  function startDrag(e, el, receiverId, labelId) {
    const primaryStartX = parseFloat(el.style.left) || 0;
    const primaryStartY = parseFloat(el.style.top)  || 0;

    const peers = Array.from(selected)
      .filter(peerEl => peerEl !== el)
      .map(peerEl => ({
        el:         peerEl,
        startX:     parseFloat(peerEl.style.left) || 0,
        startY:     parseFloat(peerEl.style.top)  || 0,
        receiverId: peerEl.dataset.receiverId ? parseInt(peerEl.dataset.receiverId) : undefined,
        labelId:    peerEl.dataset.labelId    ? parseInt(peerEl.dataset.labelId)    : undefined,
      }));

    dragging = {
      el,
      offsetX:       e.clientX - el.getBoundingClientRect().left,
      offsetY:       e.clientY - el.getBoundingClientRect().top,
      primaryStartX,
      primaryStartY,
      receiverId,
      labelId,
      peers,
    };
    el.classList.add('dragging');
    e.preventDefault();
  }

  // ── Make receiver card ────────────────────────────────────────────────────
  function makeCard(pos) {
    const r   = pos.receiver;
    const div = document.createElement('div');
    div.className = 'lc-card';
    div.dataset.receiverId = pos.receiver_id;
    div.dataset.positionId = pos.id;
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
    select.addEventListener('change', async e => {
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
    removeBtn.addEventListener('click', async () => {
      if (!confirm('Remove from layout?')) return;
      const resp = await fetch(`/api/layouts/${layoutId}/receivers/${pos.receiver_id}`,
                               { method: 'DELETE' });
      if (resp.ok) {
        div.remove();
        selected.delete(div);
        positions = positions.filter(p => p.receiver_id !== pos.receiver_id);
        window.Leash.toast('Removed from layout', 'info');
      }
    });

    // Drag / shift-select
    div.addEventListener('mousedown', e => {
      if (!editMode) return;
      if (e.target.closest('select, button')) return;
      if (e.shiftKey) { toggleSelect(div); e.preventDefault(); return; }
      if (!selected.has(div)) clearSelection();
      startDrag(e, div, pos.receiver_id, undefined);
    });

    return div;
  }

  // ── Make text label ───────────────────────────────────────────────────────
  function makeLabelCard(label) {
    const div = document.createElement('div');
    div.className = 'lc-label';
    div.dataset.labelId   = label.id;
    div.dataset.labelText = label.text;
    div.style.left = `${label.x_pct}%`;
    div.style.top  = `${label.y_pct}%`;

    const textEl = document.createElement('span');
    textEl.className = 'lc-label-text';
    textEl.textContent = label.text;
    div.appendChild(textEl);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'lc-remove-btn';
    removeBtn.title = 'Remove text label';
    removeBtn.textContent = '×';
    div.appendChild(removeBtn);

    removeBtn.addEventListener('click', async () => {
      if (!confirm('Remove this text label?')) return;
      const resp = await fetch(`/api/layouts/${layoutId}/labels/${label.id}`,
                               { method: 'DELETE' });
      if (resp.ok) {
        div.remove();
        selected.delete(div);
        labels = labels.filter(l => l.id !== label.id);
        window.Leash.toast('Label removed', 'info');
      }
    });

    div.addEventListener('mousedown', e => {
      if (!editMode) return;
      if (e.target.closest('button')) return;
      if (e.shiftKey) { toggleSelect(div); e.preventDefault(); return; }
      if (!selected.has(div)) clearSelection();
      startDrag(e, div, undefined, label.id);
    });

    return div;
  }

  // ── Render all cards ──────────────────────────────────────────────────────
  function renderAll() {
    canvas.querySelectorAll('.lc-card, .lc-label').forEach(c => c.remove());
    positions.forEach(pos => canvas.appendChild(makeCard(pos)));
    labels.forEach(label => canvas.appendChild(makeLabelCard(label)));
    clearSelection();
  }

  renderAll();

  // ── Mouse move / up for drag ──────────────────────────────────────────────
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    let x = (e.clientX - rect.left - dragging.offsetX) / rect.width  * 100;
    let y = (e.clientY - rect.top  - dragging.offsetY) / rect.height * 100;
    x = Math.max(0, Math.min(90, x));
    y = Math.max(0, Math.min(90, y));
    if (snapEnabled) {
      x = Math.round(x / SNAP) * SNAP;
      y = Math.round(y / SNAP) * SNAP;
    }

    const dx = x - dragging.primaryStartX;
    const dy = y - dragging.primaryStartY;
    dragging.el.style.left = `${x}%`;
    dragging.el.style.top  = `${y}%`;

    dragging.peers.forEach(peer => {
      peer.el.style.left = `${Math.max(0, Math.min(90, peer.startX + dx))}%`;
      peer.el.style.top  = `${Math.max(0, Math.min(90, peer.startY + dy))}%`;
    });
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    const el = dragging.el;
    const x  = parseFloat(el.style.left);
    const y  = parseFloat(el.style.top);

    // Update primary position in local data
    if (dragging.labelId !== undefined) {
      const label = labels.find(l => l.id === dragging.labelId);
      if (label) { label.x_pct = x; label.y_pct = y; }
    } else {
      const pos = positions.find(p => p.receiver_id === dragging.receiverId);
      if (pos) { pos.x_pct = x; pos.y_pct = y; }
    }

    // Update all peer positions in local data
    dragging.peers.forEach(peer => {
      const px = parseFloat(peer.el.style.left);
      const py = parseFloat(peer.el.style.top);
      if (peer.receiverId !== undefined) {
        const pos = positions.find(p => p.receiver_id === peer.receiverId);
        if (pos) { pos.x_pct = px; pos.y_pct = py; }
      } else if (peer.labelId !== undefined) {
        const label = labels.find(l => l.id === peer.labelId);
        if (label) { label.x_pct = px; label.y_pct = py; }
      }
    });

    el.classList.remove('dragging');
    dragging = null;
  });

  // ── Mode toggle ───────────────────────────────────────────────────────────
  document.getElementById('btn-view-mode')?.addEventListener('click', () => {
    editMode = false;
    snapEnabled = false;
    canvas.classList.remove('edit-mode', 'snap-grid');
    document.getElementById('btn-view-mode').classList.add('active');
    document.getElementById('btn-edit-mode').classList.remove('active');
    if (saveBtn) saveBtn.style.display = 'none';
    if (snapBtn) { snapBtn.style.display = 'none'; snapBtn.classList.remove('active', 'btn-info'); snapBtn.classList.add('btn-outline-secondary'); }
    clearSelection();
  });

  document.getElementById('btn-edit-mode')?.addEventListener('click', () => {
    editMode = true;
    canvas.classList.add('edit-mode');
    document.getElementById('btn-edit-mode').classList.add('active');
    document.getElementById('btn-view-mode').classList.remove('active');
    if (saveBtn) saveBtn.style.display = '';
    if (snapBtn) snapBtn.style.display = '';
  });

  // ── Snap toggle ───────────────────────────────────────────────────────────
  snapBtn?.addEventListener('click', () => {
    snapEnabled = !snapEnabled;
    snapBtn.classList.toggle('active',               snapEnabled);
    snapBtn.classList.toggle('btn-info',             snapEnabled);
    snapBtn.classList.toggle('btn-outline-secondary', !snapEnabled);
    canvas.classList.toggle('snap-grid', snapEnabled);
  });

  // ── Save positions ────────────────────────────────────────────────────────
  saveBtn?.addEventListener('click', async () => {
    const payload = Array.from(canvas.querySelectorAll('.lc-card')).map(card => ({
      receiver_id: parseInt(card.dataset.receiverId),
      x_pct: parseFloat(card.style.left),
      y_pct: parseFloat(card.style.top),
    }));
    const labelPayload = Array.from(canvas.querySelectorAll('.lc-label')).map(el => ({
      id:    parseInt(el.dataset.labelId),
      x_pct: parseFloat(el.style.left),
      y_pct: parseFloat(el.style.top),
    }));

    const resp = await fetch(`/api/layouts/${layoutId}/positions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions: payload, labels: labelPayload }),
    });
    const data = await resp.json();
    if (resp.ok) {
      positions = data.positions || [];
      labels    = data.labels    || [];
      window.Leash.toast('Layout saved', 'success');
    } else {
      window.Leash.toast('Save failed', 'danger');
    }
  });

  // ── Add receivers to layout ───────────────────────────────────────────────
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

  // ── Add text label ────────────────────────────────────────────────────────
  document.getElementById('btn-add-text')?.addEventListener('click', async () => {
    const text = prompt('Enter text:');
    if (!text?.trim()) return;
    const resp = await fetch(`/api/layouts/${layoutId}/labels`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.trim(), x_pct: 5, y_pct: 5 }),
    });
    if (resp.ok) {
      const label = await resp.json();
      labels.push(label);
      canvas.appendChild(makeLabelCard(label));
      window.Leash.toast('Text added — switch to Edit mode to drag it', 'info');
    }
  });

  // ── Periodic poll — update hostnames and source selections ────────────────
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

  setInterval(pollAll, 30000);
});
