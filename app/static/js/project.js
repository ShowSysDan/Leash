/* Leash — Tractus Project page */
(function () {
  'use strict';

  const modal = new bootstrap.Modal(document.getElementById('cueModal'));
  let knownViewers = [];   // [{index, name, ...}] from the last viewer refresh

  // Small DOM helper — user/device strings always go in via textContent.
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

  function initTooltips(root = document) {
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
      if (!bootstrap.Tooltip.getInstance(el)) new bootstrap.Tooltip(el);
    });
  }

  function viewerLabel(i) {
    const v = knownViewers.find(x => x.index === i);
    return v ? `${i} · ${v.name}` : `${i} (not reporting)`;
  }

  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    return data;
  }

  // ── status / now playing ──────────────────────────────────────────────────

  function renderStatus(st) {
    const phase = document.getElementById('np-phase');
    const detail = document.getElementById('np-detail');
    const holds = st.active_cues.filter(c => c.mode === 'hold');
    const remaining = st.phase_seconds_remaining != null ? ` · ${Math.ceil(st.phase_seconds_remaining)}s` : '';

    if (st.phase === 'dwell' && st.current_cue) {
      phase.className = 'badge bg-success';
      phase.textContent = 'projecting';
      detail.textContent = `${st.current_cue.name} → ${st.current_cue.source_name}${remaining}`;
    } else if (st.phase === 'gap') {
      phase.className = 'badge bg-info text-dark';
      phase.textContent = 'normal view';
      detail.textContent = st.phase_seconds_remaining != null
        ? `Next cue in ${Math.ceil(st.phase_seconds_remaining)}s` : 'Next cue soon';
    } else {
      phase.className = 'badge bg-secondary';
      phase.textContent = 'idle';
      detail.textContent = holds.length ? 'Rotation idle.' : 'No active cues — viewers show their normal layout.';
    }
    if (holds.length) {
      detail.textContent += `  Hold: ${holds.map(c => c.name).join(', ')}`;
    }

    const wrap = document.getElementById('np-active');
    wrap.textContent = '';
    st.active_cues.forEach(c => {
      const b = h('span', { class: `badge ${c.mode === 'hold' ? 'bg-danger' : 'bg-dark border border-secondary'}` }, c.name);
      if (c.active_until) {
        const mins = Math.max(0, Math.round((new Date(c.active_until + 'Z') - Date.now()) / 60000));
        b.textContent += ` (${mins}m left)`;
      }
      wrap.appendChild(b);
    });

    // Host health
    st.hosts.forEach(hs => {
      const badge = document.querySelector(`.host-badge[data-host="${CSS.escape(hs.host)}"]`);
      if (!badge) return;
      badge.className = `badge ms-1 host-badge ${hs.ok === false ? 'bg-danger' : hs.ok ? 'bg-success' : 'bg-dark border border-secondary'}`;
      badge.title = hs.ok === false ? `Last error: ${hs.last_error || '?'}` : 'OK';
    });

    // Cue row active state
    const active = new Set(st.active_cues.map(c => c.id));
    document.querySelectorAll('.cue-row').forEach(row => {
      const id = Number(row.dataset.cueId);
      const on = active.has(id);
      const btn = row.querySelector('.btn-cue-toggle');
      btn.className = `btn btn-sm btn-cue-toggle ${on ? 'btn-success' : 'btn-outline-secondary'}`;
      btn.innerHTML = `<i class="bi bi-${on ? 'broadcast' : 'play-fill'}"></i>`;
      row.querySelector('.cue-live-badge').style.display = on ? '' : 'none';
      row.classList.toggle('table-active', st.current_cue_id === id && st.phase === 'dwell');
    });
  }

  async function pollStatus() {
    try { renderStatus(await api('GET', '/api/project/status')); }
    catch (e) { /* transient — next poll retries */ }
  }

  // ── viewers ───────────────────────────────────────────────────────────────

  function renderViewers(data) {
    knownViewers = data.viewers;
    const errs = document.getElementById('viewer-host-errors');
    errs.textContent = '';
    data.hosts.filter(x => !x.ok).forEach(x => {
      errs.appendChild(h('div', { class: 'alert alert-danger py-1 px-2 m-2 small' },
        `${x.host} unreachable: ${x.error || 'unknown error'}`));
    });

    const checked = new Set([...document.querySelectorAll('.viewer-check:checked')].map(c => c.value));
    const tbody = document.getElementById('viewer-rows');
    tbody.textContent = '';
    if (!data.viewers.length) {
      tbody.appendChild(h('tr', {})).appendChild(
        h('td', { colSpan: 4, class: 'text-muted text-center py-3' }, 'No viewers reported by any host.'));
      return;
    }
    data.viewers.forEach(v => {
      const tr = h('tr');
      const cb = h('input', { type: 'checkbox', class: 'form-check-input viewer-check', value: String(v.index) });
      cb.checked = checked.has(String(v.index));
      tr.appendChild(h('td')).appendChild(cb);
      tr.appendChild(h('td', { class: 'font-monospace' }, v.index));
      const nameTd = h('td', {}, v.name);
      if (v.managed) nameTd.appendChild(h('span', { class: 'badge bg-info text-dark ms-1' }, 'cue'));
      tr.appendChild(nameTd);

      const projTd = h('td');
      const hosts = Object.keys(v.hosts);
      const sources = new Set(hosts.map(k => v.hosts[k].projected_source || ''));
      if (sources.size === 1) {
        const src = [...sources][0];
        projTd.appendChild(src ? h('span', { class: 'text-warning' }, src) : h('span', { class: 'text-muted' }, '—'));
        if (hosts.length < data.hosts.length) {
          projTd.appendChild(h('span', { class: 'badge bg-danger ms-1', title: 'Missing on some hosts' }, `${hosts.length}/${data.hosts.length} hosts`));
        }
      } else {
        // Hosts disagree — show each so the mismatch is obvious
        hosts.forEach(k => projTd.appendChild(
          h('div', { class: 'text-danger' }, `${k}: ${v.hosts[k].projected_source || '—'}`)));
      }
      tr.appendChild(projTd);
      tbody.appendChild(tr);
    });
  }

  async function refreshViewers() {
    try {
      renderViewers(await api('GET', '/api/project/viewers'));
      document.querySelectorAll('.viewer-name-badge').forEach(b => {
        b.textContent = viewerLabel(Number(b.dataset.viewer));
      });
      updateManualSelection();
    }
    catch (e) { Leash.toast(`Viewer refresh failed: ${e.message}`, 'danger'); }
  }

  function checkedViewers() {
    return [...document.querySelectorAll('.viewer-check:checked')].map(c => Number(c.value));
  }

  // Tell the operator what Project / Stop will hit, and only enable them
  // once there is something to hit.
  function updateManualSelection() {
    const sel = checkedViewers();
    const source = document.getElementById('manual-source').value;
    const label = document.getElementById('manual-selection');
    label.textContent = sel.length
      ? `Selected: ${sel.map(viewerLabel).join(', ')}`
      : 'Check viewers above to project onto them.';
    label.className = `small mb-1 ${sel.length ? 'text-info' : 'text-muted'}`;
    document.getElementById('btn-manual-project').disabled = !sel.length || !source;
    document.getElementById('btn-manual-stop').disabled = !sel.length;
  }

  document.getElementById('btn-refresh-viewers').addEventListener('click', refreshViewers);
  document.getElementById('viewer-select-all').addEventListener('change', function () {
    document.querySelectorAll('.viewer-check').forEach(c => { c.checked = this.checked; });
    updateManualSelection();
  });
  document.getElementById('viewer-rows').addEventListener('change', updateManualSelection);
  document.getElementById('manual-source').addEventListener('change', updateManualSelection);

  async function manual(source) {
    const viewers = checkedViewers();
    if (!viewers.length) { Leash.toast('Check at least one viewer', 'warning'); return; }
    if (source === '') { Leash.toast('Choose a source to project', 'warning'); return; }
    try {
      const r = await api('POST', '/api/project/manual', { viewers, source });
      Leash.toast(`${source ? 'Projected' : 'Stopped'} — ${r.succeeded}/${r.attempted} host commands OK`);
    } catch (e) {
      Leash.toast(`Some host commands failed: ${e.message}`, 'danger');
    }
    setTimeout(refreshViewers, 500);
  }
  document.getElementById('btn-manual-project').addEventListener('click',
    () => manual(document.getElementById('manual-source').value));
  document.getElementById('btn-manual-stop').addEventListener('click', () => manual(null));

  // ── cues ──────────────────────────────────────────────────────────────────

  document.querySelectorAll('.btn-cue-toggle').forEach(btn => btn.addEventListener('click', async () => {
    const on = btn.classList.contains('btn-success');
    try {
      await api('POST', `/api/project/cues/${btn.dataset.cueId}/${on ? 'deactivate' : 'activate'}`, {});
      pollStatus();
    } catch (e) { Leash.toast(e.message, 'danger'); }
  }));

  document.getElementById('btn-clear-all').addEventListener('click', async () => {
    if (!confirm('Deactivate every cue and restore the normal view on all viewers?')) return;
    try {
      const r = await api('POST', '/api/project/clear', {});
      Leash.toast(`All clear — ${r.deactivated} cue(s) released`);
      pollStatus();
    } catch (e) { Leash.toast(e.message, 'danger'); }
  });

  document.querySelectorAll('.btn-copy-url').forEach(btn => btn.addEventListener('click', async () => {
    const url = `${location.origin}/api/v1/project/cues/${btn.dataset.code}/activate`;
    try { await navigator.clipboard.writeText(url); Leash.toast(`Copied ${url}`, 'info'); }
    catch (e) { prompt('Show-control URL:', url); }
  }));

  document.querySelectorAll('.btn-delete-cue').forEach(btn => btn.addEventListener('click', async () => {
    if (!confirm(`Delete cue "${btn.dataset.name}"? If it is live it will be stopped.`)) return;
    try { await api('DELETE', `/api/project/cues/${btn.dataset.cueId}`); location.reload(); }
    catch (e) { Leash.toast(e.message, 'danger'); }
  }));

  // ── target viewer picker: a dropdown that adds removable chips ──────────
  let pickedViewers = [];

  function renderViewerPicker() {
    const select = document.getElementById('cue-viewer-add');
    select.textContent = '';
    select.appendChild(h('option', { value: '' },
      pickedViewers.length ? '+ add another viewer…' : 'All viewers (every viewer on every host)'));
    const indexes = new Set(knownViewers.map(v => v.index));
    [...indexes].sort((a, b) => a - b)
      .filter(i => !pickedViewers.includes(i))
      .forEach(i => select.appendChild(h('option', { value: String(i) }, viewerLabel(i))));

    const chips = document.getElementById('cue-viewer-chips');
    chips.textContent = '';
    pickedViewers.forEach(i => {
      const chip = h('span', { class: 'badge bg-primary d-inline-flex align-items-center gap-1' }, viewerLabel(i));
      const x = h('button', { type: 'button', class: 'btn-close btn-close-white', title: 'Remove' });
      x.style.fontSize = '0.55em';
      x.addEventListener('click', () => {
        pickedViewers = pickedViewers.filter(v => v !== i);
        renderViewerPicker();
      });
      chip.appendChild(x);
      chips.appendChild(chip);
    });
    if (pickedViewers.length) {
      const all = h('button', { type: 'button', class: 'btn btn-link btn-sm p-0 ms-1' }, 'use all viewers');
      all.addEventListener('click', () => { pickedViewers = []; renderViewerPicker(); });
      chips.appendChild(all);
    }
  }

  document.getElementById('cue-viewer-add').addEventListener('change', function () {
    if (this.value !== '') {
      pickedViewers = [...pickedViewers, Number(this.value)].sort((a, b) => a - b);
      renderViewerPicker();
    }
  });

  function setSource(name) {
    const select = document.getElementById('cue-source');
    // A cue may point at a source Leash has never seen — keep it selectable.
    if (name && ![...select.options].some(o => o.value === name)) {
      select.appendChild(h('option', { value: name }, `${name} (unknown)`));
    }
    select.value = name || '';
  }

  function applyModeUI() {
    const cycle = document.getElementById('cue-mode').value === 'cycle';
    document.querySelectorAll('.cue-cycle-only').forEach(el => { el.style.display = cycle ? '' : 'none'; });
    document.querySelectorAll('.cue-hold-only').forEach(el => { el.style.display = cycle ? 'none' : ''; });
  }
  document.getElementById('cue-mode').addEventListener('change', applyModeUI);

  function openModal(c) {
    document.getElementById('cue-modal-title').textContent = c ? 'Edit Cue' : 'New Cue';
    document.getElementById('cue-id').value = c ? c.id : '';
    document.getElementById('cue-name').value = c ? c.name : '';
    document.getElementById('cue-code').value = c ? c.code : '';
    setSource(c ? c.source_name : '');
    document.getElementById('cue-mode').value = c ? c.mode : 'cycle';
    document.getElementById('cue-dwell').value = c ? c.dwell_seconds : 10;
    document.getElementById('cue-gap').value = c ? c.gap_seconds : 30;
    document.getElementById('cue-priority').value = c ? c.priority : 0;
    document.getElementById('cue-order').value = c ? c.sort_order : 0;
    pickedViewers = c && c.viewers ? [...c.viewers] : [];
    renderViewerPicker();
    applyModeUI();
    modal.show();
  }
  document.getElementById('btn-new-cue').addEventListener('click', () => openModal(null));
  document.querySelectorAll('.btn-edit-cue').forEach(btn =>
    btn.addEventListener('click', () => openModal(JSON.parse(btn.dataset.cue))));

  // Suggest a code from the name for new cues
  document.getElementById('cue-name').addEventListener('input', function () {
    const code = document.getElementById('cue-code');
    if (document.getElementById('cue-id').value || code.dataset.touched) return;
    code.value = this.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
  });
  document.getElementById('cue-code').addEventListener('input', function () { this.dataset.touched = '1'; });

  document.getElementById('btn-save-cue').addEventListener('click', async () => {
    const id = document.getElementById('cue-id').value;
    const body = {
      name: document.getElementById('cue-name').value,
      code: document.getElementById('cue-code').value,
      source_name: document.getElementById('cue-source').value,
      viewers: pickedViewers,
      mode: document.getElementById('cue-mode').value,
      dwell_seconds: document.getElementById('cue-dwell').value,
      gap_seconds: document.getElementById('cue-gap').value,
      priority: document.getElementById('cue-priority').value,
      sort_order: document.getElementById('cue-order').value,
    };
    try {
      await api(id ? 'PUT' : 'POST', id ? `/api/project/cues/${id}` : '/api/project/cues', body);
      location.reload();
    } catch (e) { Leash.toast(e.message, 'danger'); }
  });

  // ── boot ──────────────────────────────────────────────────────────────────

  initTooltips();
  pollStatus();
  refreshViewers();
  setInterval(pollStatus, 1000);
  setInterval(refreshViewers, 10000);
})();
