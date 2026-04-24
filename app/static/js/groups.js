/**
 * Groups page — create, edit, manage members, bulk source send.
 */
document.addEventListener('DOMContentLoaded', () => {

  // ── Colour swatch pickers ────────────────────────────────────────────────
  function bindSwatches(containerId, hiddenId) {
    const container = document.getElementById(containerId);
    const hidden    = document.getElementById(hiddenId);
    if (!container || !hidden) return;
    container.querySelectorAll('.color-swatch').forEach(s => {
      s.addEventListener('click', () => {
        container.querySelectorAll('.color-swatch').forEach(x => x.classList.remove('selected'));
        s.classList.add('selected');
        hidden.value = s.dataset.color;
      });
    });
  }
  bindSwatches('cg-color-swatches', 'cg-color');
  bindSwatches('eg-color-swatches', 'eg-color');

  // ── Create group ─────────────────────────────────────────────────────────
  document.getElementById('btn-create-group')?.addEventListener('click', async () => {
    const name  = document.getElementById('cg-name')?.value?.trim();
    const desc  = document.getElementById('cg-desc')?.value?.trim();
    const color = document.getElementById('cg-color')?.value;
    if (!name) { window.Leash.toast('Name is required', 'warning'); return; }

    const resp = await fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: desc, color }),
    });
    const data = await resp.json();
    if (resp.ok) {
      window.Leash.toast(`Group "${name}" created`, 'success');
      setTimeout(() => location.reload(), 600);
    } else {
      window.Leash.toast(data.error || 'Failed', 'danger');
    }
  });

  // ── Edit group (open modal) ──────────────────────────────────────────────
  document.querySelectorAll('.btn-edit-group').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('eg-id').value    = btn.dataset.groupId;
      document.getElementById('eg-name').value  = btn.dataset.groupName;
      document.getElementById('eg-desc').value  = btn.dataset.groupDescription;
      document.getElementById('eg-color').value = btn.dataset.groupColor;

      // Highlight matching swatch
      document.querySelectorAll('#eg-color-swatches .color-swatch').forEach(s => {
        s.classList.toggle('selected', s.dataset.color === btn.dataset.groupColor);
      });

      new bootstrap.Modal(document.getElementById('editGroupModal')).show();
    });
  });

  document.getElementById('btn-save-group')?.addEventListener('click', async () => {
    const id    = document.getElementById('eg-id')?.value;
    const name  = document.getElementById('eg-name')?.value?.trim();
    const desc  = document.getElementById('eg-desc')?.value?.trim();
    const color = document.getElementById('eg-color')?.value;
    if (!name) { window.Leash.toast('Name is required', 'warning'); return; }

    const resp = await fetch(`/api/groups/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: desc, color }),
    });
    if (resp.ok) {
      window.Leash.toast('Group updated', 'success');
      setTimeout(() => location.reload(), 600);
    }
  });

  // ── Delete group ─────────────────────────────────────────────────────────
  document.querySelectorAll('.btn-delete-group').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Delete group "${btn.dataset.groupName}"?`)) return;
      const resp = await fetch(`/api/groups/${btn.dataset.groupId}`, { method: 'DELETE' });
      if (resp.ok) {
        document.getElementById(`group-col-${btn.dataset.groupId}`)?.remove();
        window.Leash.toast('Group deleted', 'warning');
      }
    });
  });

  // ── Send source to entire group ──────────────────────────────────────────
  document.querySelectorAll('.btn-send-group-source').forEach(btn => {
    btn.addEventListener('click', async () => {
      const gid = btn.dataset.groupId;
      const sel = document.querySelector(`.group-source-select[data-group-id="${gid}"]`);
      const sourceName = sel?.value;
      if (!sourceName) { window.Leash.toast('Select a source first', 'warning'); return; }

      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      try {
        const resp = await fetch(`/api/groups/${gid}/source`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_name: sourceName }),
        });
        const data = await resp.json();
        if (resp.ok) {
          window.Leash.toast(
            `Sent "${sourceName}" to ${data.succeeded}/${data.attempted} receivers`, 'success');
        } else {
          window.Leash.toast(data.error || 'Failed', 'danger');
        }
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-send me-1"></i>Send';
        if (sel) sel.value = '';
      }
    });
  });

  // ── Remove single member badge ───────────────────────────────────────────
  document.querySelectorAll('.btn-remove-member').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const gid = btn.dataset.groupId;
      const rid = btn.dataset.receiverId;
      const resp = await fetch(`/api/groups/${gid}/receivers`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver_ids: [parseInt(rid)] }),
      });
      if (resp.ok) {
        btn.closest('.group-member-badge')?.remove();
        window.Leash.toast('Removed from group', 'info');
      }
    });
  });

  // ── Manage members modal ─────────────────────────────────────────────────
  let _currentGroupData = null;

  document.querySelectorAll('.btn-manage-members').forEach(btn => {
    btn.addEventListener('click', async () => {
      const gid  = btn.dataset.groupId;
      const name = btn.dataset.groupName;

      document.getElementById('mm-group-id').value  = gid;
      document.getElementById('mm-group-name').textContent = name;

      // Load current members
      const resp = await fetch(`/api/groups/${gid}`);
      _currentGroupData = await resp.json();
      const memberIds = new Set((_currentGroupData.receivers || []).map(r => r.id));

      // Tick current members
      document.querySelectorAll('.mm-recv-check').forEach(cb => {
        cb.checked = memberIds.has(parseInt(cb.value));
      });

      new bootstrap.Modal(document.getElementById('membersModal')).show();
    });
  });

  document.getElementById('btn-save-members')?.addEventListener('click', async () => {
    const gid  = document.getElementById('mm-group-id')?.value;
    const checked = Array.from(document.querySelectorAll('.mm-recv-check:checked'))
                         .map(cb => parseInt(cb.value));
    const unchecked = Array.from(document.querySelectorAll('.mm-recv-check:not(:checked)'))
                           .map(cb => parseInt(cb.value));

    // Add checked
    if (checked.length) {
      await fetch(`/api/groups/${gid}/receivers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver_ids: checked }),
      });
    }
    // Remove unchecked
    if (unchecked.length) {
      await fetch(`/api/groups/${gid}/receivers`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver_ids: unchecked }),
      });
    }

    window.Leash.toast('Members updated', 'success');
    setTimeout(() => location.reload(), 500);
  });

});
