/** Sidebar summary, filter-chip, and stage-count rendering. */

import { state } from './state.js';
import { $ } from './utils.js';
import { summarizeOfflineVideos } from './offline-media.js';
import { countChipStats, countStageSummary } from './sidebar-video-filter.js';
import { t } from './i18n.js';

export function renderOfflineSummary() {
  const box = $('offline-summary');
  if (!box) return;
  // Only meaningful in original view where offline paths are listed
  const summary = summarizeOfflineVideos(state.videos || []);
  if (!summary.count || state.source !== 'original') {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  box.innerHTML = `
    <div class="offline-summary-inner">
      <span class="offline-summary-text">${t('sidebar.offline_summary', { count: summary.count })}</span>
      <button type="button" class="sidebar-btn offline-batch-btn" id="btn-batch-relink" title="${t('sidebar.batch_relink_title')}">${t('sidebar.batch_relink')}</button>
    </div>
  `;
  const btn = box.querySelector('#btn-batch-relink');
  if (btn) {
    btn.onclick = () => {
      import('./sidebar-batch-relink.js').then(m => m.openBatchRelinkModal());
    };
  }
}

export function renderFilterChips(onChange) {
  const box = $('video-filter-chips');
  if (!box) return;
  const all = state.videos.length;
  const stats = countChipStats(state.videos, state.source);
  box.innerHTML = '';
  const isMissingFilter = state.videoFilter.mode !== 'done';
  const allBtn = document.createElement('button');
  allBtn.type = 'button';
  allBtn.className = 'video-filter-chip' + (!state.videoFilter.stage ? ' active' : '');
  allBtn.textContent = t('sidebar.filter_all', { count: all });
  allBtn.onclick = () => {
    state.videoFilter.stage = '';
    state.videoFilter.mode = 'missing';
    onChange();
  };
  box.appendChild(allBtn);
  for (const s of stats) {
    if (s.count === 0) continue;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'video-filter-chip' + (
      isMissingFilter && state.videoFilter.stage === s.key ? ' active' : ''
    );
    b.textContent = `${s.label} ${s.count}`;
    b.onclick = () => {
      state.videoFilter.stage = state.videoFilter.stage === s.key ? '' : s.key;
      state.videoFilter.mode = 'missing';
      onChange();
    };
    box.appendChild(b);
  }
}

export function renderStageCountBar(onChange) {
  const box = $('stage-count-bar');
  if (!box) return;
  box.innerHTML = '';
  if (!state.videos.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const summary = countStageSummary(state.videos, state.source);
  const isDoneFilter = state.videoFilter.mode === 'done';
  for (const s of summary) {
    const cell = document.createElement('div');
    const isActive = isDoneFilter && state.videoFilter.stage === s.key;
    cell.className = 'stage-count-cell' + (s.done === 0 ? ' empty' : '') + (isActive ? ' active' : '');
    cell.innerHTML = `<span class="stage-count-num">${s.done}/${s.total}</span>${s.label}`;
    if (s.done === 0) {
      cell.title = t('sidebar.stage_none', { label: s.label });
    } else if (s.done === s.total) {
      cell.title = isDoneFilter && isActive ? t('sidebar.stage_all_done_active', { label: s.label, count: s.done }) : t('sidebar.stage_all_done', { label: s.label });
    } else {
      cell.title = t('sidebar.stage_partial', { label: s.label, done: s.done, missing: s.total - s.done });
    }
    cell.onclick = () => {
      if (s.done === 0) return;
      const activeNow = state.videoFilter.stage === s.key && state.videoFilter.mode === 'done';
      if (activeNow) {
        state.videoFilter.stage = '';
        state.videoFilter.mode = 'missing';
      } else {
        state.videoFilter.stage = s.key;
        state.videoFilter.mode = 'done';
      }
      onChange();
    };
    box.appendChild(cell);
  }
}
