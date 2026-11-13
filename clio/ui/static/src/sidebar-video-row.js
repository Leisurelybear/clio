import { state } from './state.js';
import { escapeHtml, setStatus } from './utils.js';
import { api, icon } from './api.js';
import { buildVideoMenuItems, videoMenuItemsToHtml } from './video-menu.js';
import { buildVideoStageStatuses, stageLabel, STAGE_CELLS } from './sidebar-video-filter.js';
import { videoSelectionKey } from './video-selection.js';
import { bindVideoInfoHover } from './video-info.js';
import { t } from './i18n.js';
import { confirmDialog } from './confirm.js';
import { bindCoverThumbFallback, videoDisplayName, videoThumbHtml } from './sidebar-video-display.js';

let _portalDropdown = null;
let _portalCloseHandler = null;

async function requestRerun(payload) {
  try {
    return await api('POST', '/api/rerun', payload);
  } catch (e) {
    if (e?.status !== 409 || e.body?.code !== 'artifact_overwrite_confirmation_required') throw e;
    const items = Array.isArray(e.body.items)
      ? e.body.items.map(item => String(item)).filter(Boolean)
      : [];
    const message = items.length
      ? t('run.confirm_artifact_overwrite', { items: items.join(t('common.list_separator')) })
      : (e.body.error || e.message);
    if (!(await confirmDialog(message, { danger: true }))) {
      setStatus(t('sidebar.rerun_cancelled'), 'warn', { persist: false });
      return null;
    }
    return api('POST', '/api/rerun', { ...payload, confirm_artifact_overwrite: true });
  }
}

export function renderVideoItem(v, { relinkVideo, removeVideoFromProject, rerenderVideoList }) {
  const li = document.createElement('li');
  let checkboxHtml = '';
  let selectedClass = '';
  if (state.selectionMode) {
    const selectKey = videoSelectionKey(v);
    const isSelected = state.selectedFiles.includes(selectKey);
    selectedClass = isSelected ? ' selected' : '';
    const disabled = v.missing ? `disabled title="${t('sidebar.offline_not_selectable')}"` : '';
    checkboxHtml = `<input type="checkbox" class="video-checkbox" data-file="${escapeHtml(selectKey)}" ${isSelected ? 'checked' : ''} ${disabled}>`;
  }
  li.className = 'video-item' + selectedClass;
  if (state.currentVideo === v.file) li.classList.add('active');
  if (!v.match) li.classList.add('no-match');
  if (v.missing) li.classList.add('missing');

  const display = videoDisplayName(v);

  const tCls = v.text_json ? 'has' : 'miss';
  const sCls = v.script_json ? 'has' : 'miss';
  const tTransCls = v.transcript_file ? 'has' : 'miss';
  const tLabel = v.text_json ? `${icon('check', 12)} texts` : '· texts';
  const sLabel = v.script_json ? `${icon('check', 12)} voiceover` : '· voiceover';
  const tTransLabel = v.transcript_file ? `${icon('check', 12)} trans.` : '· trans.';
  const counterpartLabel = state.source === 'compressed' ? t('sidebar.counterpart_char.original') : t('sidebar.counterpart_char.compressed');
  let matchBadge;
  if (v.match) {
    if (v.match.missing) {
      matchBadge = `<span class="match-badge miss" title="${escapeHtml(v.match.abs_path || v.match.file || '')}">→ ${counterpartLabel}: ${t('sidebar.match_offline')}</span>`;
    } else if (v.segment_matches && v.segment_matches.length > 1) {
      const segList = v.segment_matches.map(m => m.file).join(', ');
      matchBadge = `<button type="button" class="match-badge match-jump" title="${escapeHtml(segList)}">→ ${counterpartLabel}: ${escapeHtml(t('sidebar.match_segments', { index: v.index || '', total: v.segment_matches.length }))}</button>`;
    } else {
      matchBadge = `<button type="button" class="match-badge match-jump" title="${escapeHtml(v.match.file)}">→ ${counterpartLabel}: ${escapeHtml(v.match.file)}</button>`;
    }
  } else {
    matchBadge = `<span class="match-badge miss" title="${t('sidebar.match_none_title', { kind: state.source === 'compressed' ? t('sidebar.kind.original') : t('sidebar.kind.compressed') })}">${t('sidebar.match_none')}</span>`;
  }
  const stepStatuses = buildVideoStageStatuses(v, state.source);
  const stepDots = STAGE_CELLS
    .map(c => `<span class="video-step-dot ${stepStatuses[c.key] ? 'done' : 'pending'}" title="${stageLabel(c)}: ${stepStatuses[c.key] ? t('sidebar.step_done') : t('sidebar.step_pending')}"></span>`)
    .join('');
  const menuHtml = videoMenuItemsToHtml(buildVideoMenuItems(v, state.source, state.deps));

  const durHtml = (v.duration_sec != null && Number(v.duration_sec) >= 0)
    ? `<span class="video-duration">${Math.round(Number(v.duration_sec))}s</span>`
    : '';

  li.innerHTML = `${videoThumbHtml(v)}
    <div class="video-info">
      <div class="video-name">${checkboxHtml}${v.index ? '[' + v.index + '] ' : ''}${escapeHtml(display)}${durHtml}</div>
      <div class="video-step-dots">${stepDots}</div>
      ${v.title ? `<div class="video-title">${escapeHtml(v.title)}</div>` : ''}
      <div class="video-match">${matchBadge}</div>
    </div>
      <div class="video-actions">
        <button class="menu-btn" title="${t('sidebar.actions')}">⋮</button>
        <div class="menu-dropdown">
          ${menuHtml}
        </div>
      </div>
  `;
  bindCoverThumbFallback(li);
  bindVideoInfoHover(li, v, state.source);

  li.onclick = (e) => {
    if (e.target.closest('.match-jump')) return;
    if (e.target.closest('.video-actions')) return;
    if (v.missing) {
      setStatus(t('sidebar.video_unavailable'), 'warn');
      relinkVideo(v.file, v.abs_path || (v.match && v.match.abs_path) || null);
      return;
    }
    if (state.selectionMode) {
      if (e.target.closest('.video-checkbox')) return;
      const cb = li.querySelector('.video-checkbox');
      if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change', { bubbles: true })); }
      return;
    }
    import('./sidebar.js').then(mod => mod.selectVideo(v.file));
  };

  const matchJump = li.querySelector('.match-jump');
  if (matchJump) {
    matchJump.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      import('./sidebar.js').then(mod => mod.jumpToCounterpart(v));
    };
  }

  const menuBtn = li.querySelector('.menu-btn');
  const dropdown = li.querySelector('.menu-dropdown');
  menuBtn.onclick = (e) => {
    e.stopPropagation();
    if (_portalDropdown) { _portalDropdown.remove(); _portalDropdown = null; }
    if (_portalCloseHandler) { document.removeEventListener('click', _portalCloseHandler); _portalCloseHandler = null; }
    const rect = menuBtn.getBoundingClientRect();
    const clone = dropdown.cloneNode(true);
    clone.classList.add('open');
    clone.style.cssText = 'position:fixed;top:' + (rect.bottom + 4) + 'px;right:auto;left:' + Math.max(4, rect.right - 160) + 'px;z-index:10000;min-width:160px;width:auto;';
    document.body.appendChild(clone);
    _portalDropdown = clone;
    clone.querySelectorAll('.menu-item').forEach(item => {
      item.onclick = async (ev) => {
        ev.stopPropagation(); if (item.disabled) return;
        clone.remove(); _portalDropdown = null;
        if (_portalCloseHandler) { document.removeEventListener('click', _portalCloseHandler); _portalCloseHandler = null; }
        const task = item.dataset.action;
        const file = v.file;
        if (task === 'info') {
          import('./video-info.js').then(mod => mod.openVideoInfoModal(v, state.source));
          return;
        }
        if (task === 'relink') {
          await relinkVideo(file, v.abs_path || (v.match && v.match.abs_path) || null);
          return;
        }
        if (task === 'remove') {
          if (await confirmDialog(t('sidebar.confirm_remove', { file }), { danger: true })) {
            await removeVideoFromProject(file, v.abs_path || (v.match && v.match.abs_path) || null);
          }
          return;
        }
        if (task === 'reveal') {
          const path = v.abs_path || v.match?.abs_path;
          try {
            const r = await api('POST', '/api/fs/reveal', { path });
            if (r.ok) setStatus(t('sidebar.opened', { path: r.path || path }), 'ok');
            else throw new Error(r.error || t('sidebar.open_failed'));
          } catch (e) { setStatus(t('sidebar.open_location_failed', { detail: e.message }), 'err'); }
          return;
        }
        setStatus(t('sidebar.rerunning', { task, file }), 'ok', { persist: false });
        try {
          const r = await requestRerun({
            video: file,
            task: task,
            source: state.source,
            index: v.index || undefined,
            abspath: v.abs_path || (v.match && v.match.abs_path) || undefined,
          });
          if (!r) return;
          if (r.ok) {
            setStatus(r.message || t('sidebar.rerun_started', { task }), 'ok', { persist: false });
            import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file, r.task_id));
          } else { throw new Error(r.error || t('sidebar.rerun_failed')); }
        } catch (e) { setStatus(t('sidebar.rerun_failed_detail', { detail: e.message }), 'err'); }
      };
    });
    if (!_portalCloseHandler) {
      _portalCloseHandler = (ev) => {
        if (_portalDropdown && !_portalDropdown.contains(ev.target) && !ev.target.closest('.menu-btn')) {
          _portalDropdown.remove(); _portalDropdown = null;
          document.removeEventListener('click', _portalCloseHandler);
          _portalCloseHandler = null;
        }
      };
      setTimeout(() => document.addEventListener('click', _portalCloseHandler), 0);
    }
  };

  if (state.selectionMode) {
    const cb = li.querySelector('.video-checkbox');
    if (cb) {
      cb.addEventListener('change', (e) => {
        e.stopPropagation();
        const selectKey = videoSelectionKey(v);
        if (cb.checked) {
          if (!state.selectedFiles.includes(selectKey)) state.selectedFiles.push(selectKey);
        } else {
          state.selectedFiles = state.selectedFiles.filter(f => f !== selectKey);
        }
        rerenderVideoList();
      });
    }
  }

  return li;
}
