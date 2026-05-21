/**
 * Operator view — streamlined mobile-first control surface.
 *
 * Four bottom-nav tabs (Receivers / Snapshots / Groups / PTZ) over a single
 * page render. Source changes and group sends hit the same /api endpoints
 * that the desktop UI uses; snapshot recalls open a brief progress overlay
 * so the operator gets feedback without losing their tab.
 */
document.addEventListener('DOMContentLoaded', () => {

  // ── Bottom-nav tab switching ─────────────────────────────────────────
  const tabButtons = document.querySelectorAll('.operator-bottom-nav button');
  const panes = document.querySelectorAll('.operator-pane');

  function showTab(name) {
    let matched = false;
    panes.forEach(p => {
      const is = p.dataset.pane === name;
      p.style.display = is ? '' : 'none';
      if (is) matched = true;
    });
    if (!matched) return;
    tabButtons.forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    try { localStorage.setItem('leash-op-tab', name); } catch (_e) {}
  }

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  // Restore last-used tab so the operator picks up where they left off
  try {
    const last = localStorage.getItem('leash-op-tab');
    if (last) showTab(last);
  } catch (_e) {}

  // Mark this user as having visited the operator view so the suggestion
  // banner stops appearing on other pages.
  try { localStorage.setItem('leash-operator-suggest-dismissed', '1'); } catch (_e) {}

  // ── Receiver search filter (shared helper from main.js) ──────────────
  window.Leash?.attachReceiverSearch?.({
    inputId:    'op-search',
    clearBtnId: 'op-search-clear',
    countElId:  'op-search-count',
    emptyElId:  'op-search-empty',
    itemSel:    '.operator-receiver-row',
    hiddenClass: 'col-search-hidden',
  });

  // ── Show / hide offline receivers ────────────────────────────────────
  const offlineToggle = document.getElementById('op-show-offline');
  function applyOfflineFilter() {
    const show = offlineToggle?.checked ?? true;
    document.querySelectorAll('.op-rcv-offline').forEach(el => {
      el.classList.toggle('col-offline-hidden', !show);
    });
  }
  offlineToggle?.addEventListener('change', applyOfflineFilter);
  applyOfflineFilter();
  // Reuse the same CSS rule injected by the / page (in case the operator
  // page is hit directly without visiting / first).
  if (!document.querySelector('style[data-op-offline-style]')) {
    const style = document.createElement('style');
    style.setAttribute('data-op-offline-style', '1');
    style.textContent = '.col-offline-hidden { display: none !important; }';
    document.head.appendChild(style);
  }

  // ── Source changes on receiver rows ──────────────────────────────────
  document.querySelectorAll('.op-rcv-source').forEach(sel => {
    sel.addEventListener('change', async () => {
      const row = sel.closest('.operator-receiver-row');
      const rid = row?.dataset.rid;
      if (!rid || !sel.value) return;
      sel.disabled = true;
      try {
        await window.Leash.setSource(rid, sel.value);
      } finally {
        sel.disabled = (row.dataset.status === 'offline');
      }
    });
  });

  // ── Snapshot recall ──────────────────────────────────────────────────
  document.querySelectorAll('.op-recall-snap').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.dataset.snapName;
      if (!confirm(`Recall snapshot "${name}"?\n\nThis applies it to every online receiver.`)) return;
      btn.disabled = true;
      const originalHtml = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Recalling…';
      try {
        const resp = await fetch(`/api/snapshots/${btn.dataset.snapId}/recall`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const data = await resp.json();
        if (resp.ok) {
          window.Leash.toast(
            `Recalled — ${data.succeeded}/${data.attempted} receivers`,
            data.succeeded === data.attempted ? 'success' : 'warning',
          );
        } else {
          window.Leash.toast(data.error || 'Recall failed', 'danger');
        }
      } catch (_e) {
        window.Leash.toast('Network error', 'danger');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
      }
    });
  });

  // ── Group source send ────────────────────────────────────────────────
  document.querySelectorAll('.op-send-group').forEach(btn => {
    btn.addEventListener('click', async () => {
      const gid = btn.dataset.gid;
      const gname = btn.dataset.gname;
      const sel = document.querySelector(`.op-group-source[data-gid="${gid}"]`);
      const src = sel?.value;
      if (!src) {
        window.Leash.toast('Pick a source first', 'warning');
        return;
      }
      if (!confirm(`Send "${src}" to every online member of "${gname}"?`)) return;
      btn.disabled = true;
      const originalHtml = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      try {
        const resp = await fetch(`/api/groups/${gid}/source`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_name: src }),
        });
        const data = await resp.json();
        if (resp.ok) {
          window.Leash.toast(
            `${src} → ${data.succeeded ?? 0}/${data.attempted ?? 0} in ${gname}`,
            (data.succeeded === data.attempted) ? 'success' : 'warning',
          );
        } else {
          window.Leash.toast(data.error || 'Send failed', 'danger');
        }
      } catch (_e) {
        window.Leash.toast('Network error', 'danger');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
      }
    });
  });

  // ── Auto-refresh receiver state every 20s so the operator sees
  // out-of-band source changes (someone else routed a card from the
  // desktop UI) without needing to pull-to-refresh. Pauses while the
  // tab is hidden to keep the device's radio quiet in the user's pocket.
  const REFRESH_MS = 20000;
  let refreshTimer = null;
  async function refreshReceivers() {
    if (document.hidden) return;
    try {
      const resp = await fetch('/api/receivers');
      if (!resp.ok) return;
      const receivers = await resp.json();
      const map = {};
      receivers.forEach(r => { map[r.id] = r; });
      document.querySelectorAll('.operator-receiver-row').forEach(row => {
        const r = map[Number(row.dataset.rid)];
        if (!r) return;
        row.dataset.status = r.status;
        row.classList.toggle('is-offline',    r.status === 'offline');
        row.classList.toggle('op-rcv-offline', r.status === 'offline');
        const dot = row.querySelector('.status-dot');
        if (dot) dot.className = `status-dot status-${r.status}`;
        const sel = row.querySelector('.op-rcv-source');
        if (sel) {
          sel.disabled = r.status === 'offline';
          if (r.current_source) {
            if (!Array.from(sel.options).some(o => o.value === r.current_source)) {
              sel.insertBefore(new Option(r.current_source, r.current_source), sel.options[1]);
            }
            sel.value = r.current_source;
          }
        }
      });
      applyOfflineFilter();
    } catch (_e) { /* network blip — try again next tick */ }
  }
  function startRefresh() {
    if (refreshTimer) return;
    refreshTimer = setInterval(refreshReceivers, REFRESH_MS);
  }
  function stopRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopRefresh();
    else { startRefresh(); refreshReceivers(); }
  });
  startRefresh();
});
