import { state } from './state.js';
import { $, escapeHtml, markDirty, clearDirty, updateSidebarDay, setStatus } from './utils.js';
import { api, icon } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { renderPreviewBar, startPreview, _playPreviewSegment } from './viewer.js';
import { buildTimeline, clampGlobal } from './plan-timeline.js';
import { recomposePlanWaveformFromCache, isPlanWaveformMode } from './waveform.js';
import { addToast } from './toast.js';
import { resolveEditorSaveTarget } from './editor-save.js';
import { exportJianyingDraft } from './plan-export.js';
import { openPlanRangePicker } from './plan-range-picker.js';
import {
  reorderSequence,
  removeSegment,
  patchSegment,
  insertSegment,
  computeDropToIndex,
  computeDragAutoScrollDelta,
  nextExpandedAfterDelete,
  nextExpandedAfterInsert,
  nextExpandedAfterMove,
} from './plan-edit.js';

let _readinessTimer = null;
let _lastReadiness = { ok: true, errors: [], warnings: [] };
let _dragFromIndex = null;
let _dropToIndex = null;
let _highlightTimer = null;
let _expandedSegIndex = null;

export function getPlanExpandedIndex() {
  return _expandedSegIndex;
}

/**
 * @param {number|null} index
 * @param {{ render?: boolean }} [opts]
 */
export function setPlanExpandedIndex(index, opts = {}) {
  const render = opts.render !== false;
  let next = index;
  if (next != null) {
    const n = Number(next);
    if (!Number.isFinite(n) || n < 0) next = null;
    else next = n | 0;
  } else {
    next = null;
  }
  if (next === _expandedSegIndex) return;
  // Avoid replacing the list DOM mid-drag (drop indicators / drag source vanish).
  if (_dragFromIndex != null && render) {
    _expandedSegIndex = next;
    return;
  }
  _expandedSegIndex = next;
  if (render && document.getElementById('plan-list')) {
    renderPlan();
  }
}

function planScrollParent() {
  return document.querySelector('#tab-plan');
}

/** Scroll the plan pane when the pointer nears its top/bottom during drag. */
function autoScrollDuringDrag(clientY) {
  const scroller = planScrollParent();
  if (!scroller || _dragFromIndex == null) return;
  const rect = scroller.getBoundingClientRect();
  const delta = computeDragAutoScrollDelta(clientY, rect.top, rect.bottom);
  if (delta) scroller.scrollTop += delta;
}

export function configSaveStatusForTab(tab) {
  if (tab === 'project') {
    return { message: '项目配置已保存，立即生效', level: 'ok' };
  }
  if (tab === 'global') {
    return {
      message: '全局配置已保存并热加载；正在运行中的任务仍使用启动时配置',
      level: 'ok',
    };
  }
  return { message: '合并视图为只读，无法保存', level: 'warn' };
}

function applySequence(next, opts = {}) {
  if (!state.plan) return;
  state.plan.sequence = next;
  markDirty();
  renderPlan();
  const hi = opts.highlightIndex;
  if (hi == null || hi < 0) return;
  const el = document.querySelector(`#plan-list [data-preview-index="${hi}"]`);
  if (!el) return;
  el.classList.add('plan-seg-just-moved');
  try {
    el.scrollIntoView({ block: 'nearest' });
  } catch { /* ignore */ }
  if (_highlightTimer) clearTimeout(_highlightTimer);
  _highlightTimer = setTimeout(() => {
    el.classList.remove('plan-seg-just-moved');
    _highlightTimer = null;
  }, 700);
}

function clearDropIndicator() {
  document.querySelectorAll('.plan-seg-drop-before, .plan-seg-drop-after').forEach((node) => {
    node.classList.remove('plan-seg-drop-before', 'plan-seg-drop-after');
  });
  _dropToIndex = null;
}

/** Paint insert line for pending toIndex (final index of moved item). */
function paintDropIndicator(fromIndex, toIndex, length) {
  clearDropIndicator();
  if (toIndex == null || length <= 0) return;
  _dropToIndex = toIndex;
  const list = document.querySelectorAll('#plan-list .plan-seg');
  if (!list.length) return;
  const insertBefore = toIndex < fromIndex ? toIndex : toIndex + 1;
  if (insertBefore >= length) {
    list[length - 1]?.classList.add('plan-seg-drop-after');
  } else {
    list[insertBefore]?.classList.add('plan-seg-drop-before');
  }
}

function scheduleReadinessCheck() {
  if (_readinessTimer) clearTimeout(_readinessTimer);
  _readinessTimer = setTimeout(() => {
    _readinessTimer = null;
    refreshReadinessPanel();
  }, 400);
}

async function refreshReadinessPanel() {
  const panel = $('plan-readiness');
  if (!panel || !state.plan) return;
  const ac = beginLatest('plan-readiness');
  panel.innerHTML = '<p class="muted">检查规划就绪状态…</p>';
  try {
    const r = await api('POST', '/api/plan/readiness', {
      day: state.currentDay || 'day1',
      source: $('cut-source')?.value || 'compressed',
      plan: state.plan,
    }, { signal: ac.signal });
    if (!isLatest('plan-readiness', ac)) return;
    const errors = r.errors || [];
    const warnings = r.warnings || [];
    _lastReadiness = { ok: !!r.ok, errors, warnings };
    renderReadinessList(panel, errors, warnings);
    updateActionButtonsGate();
  } catch (e) {
    if (isAbortError(e) || !isLatest('plan-readiness', ac)) return;
    panel.innerHTML = `<p class="err">就绪检查失败: ${escapeHtml(e.message || e)}</p>`;
    _lastReadiness = { ok: false, errors: [{ message: String(e.message || e) }], warnings: [] };
    updateActionButtonsGate();
  } finally {
    endLatest('plan-readiness', ac);
  }
}

function renderReadinessList(panel, errors, warnings) {
  if (!errors.length && !warnings.length) {
    panel.innerHTML = '<p class="ok">规划检查通过，可以裁剪/导出。</p>';
    return;
  }
  const parts = ['<h3>导出/裁剪检查</h3>'];
  if (errors.length) {
    parts.push('<ul class="plan-readiness-errors">');
    for (const issue of errors) {
      const idx = issue.segment_index;
      parts.push(
        `<li class="plan-issue-error" data-seg="${idx != null ? idx : ''}">${escapeHtml(issue.message || issue.code || '错误')}</li>`
      );
    }
    parts.push('</ul>');
  }
  if (warnings.length) {
    parts.push('<ul class="plan-readiness-warnings">');
    for (const issue of warnings) {
      const idx = issue.segment_index;
      parts.push(
        `<li class="plan-issue-warn" data-seg="${idx != null ? idx : ''}">${escapeHtml(issue.message || issue.code || '警告')}</li>`
      );
    }
    parts.push('</ul>');
  }
  panel.innerHTML = parts.join('');
  panel.querySelectorAll('[data-seg]').forEach((el) => {
    el.onclick = () => {
      const raw = el.getAttribute('data-seg');
      if (raw === '' || raw == null) return;
      const idx = parseInt(raw, 10);
      if (!Number.isFinite(idx) || idx < 0) return;
      _expandedSegIndex = idx;
      if (document.getElementById('plan-list')) renderPlan();
      document.querySelector(`[data-preview-index="${idx}"]`)?.scrollIntoView({ block: 'nearest' });
    };
  });
}

function updateActionButtonsGate() {
  const hasErrors = (_lastReadiness.errors || []).length > 0;
  const cutBtn = $('btn-cut-exec');
  const expBtn = $('btn-jianying-export');
  if (cutBtn) {
    cutBtn.disabled = hasErrors;
    cutBtn.title = hasErrors ? '请先修复规划错误' : '';
  }
  if (expBtn) {
    expBtn.disabled = hasErrors;
    expBtn.title = hasErrors ? '请先修复规划错误' : '';
  }
}

function ensureSavedBeforeAction() {
  if (!state.dirty) return true;
  alert('有未保存的规划修改，请先保存后再裁剪/导出。');
  return false;
}

function confirmWarningsIfNeeded() {
  const warnings = _lastReadiness.warnings || [];
  const errors = _lastReadiness.errors || [];
  if (errors.length) {
    addToast(errors[0].message || '规划存在错误，无法继续', 'error', 6000);
    return false;
  }
  if (warnings.length) {
    const preview = warnings.slice(0, 5).map((w) => w.message).join('\n');
    return confirm(`规划存在警告：\n${preview}\n\n仍要继续？`);
  }
  return true;
}

/** Clamp composite playhead and rebuild preview bar after use_timeline changes. */
function _refreshPreviewTimeline() {
  const seq = state.plan?.sequence;
  if (!Array.isArray(seq)) return;
  const tl = buildTimeline(seq);
  state.previewGlobalSec = clampGlobal(tl, state.previewGlobalSec);
  renderPreviewBar();
  if (isPlanWaveformMode()) recomposePlanWaveformFromCache();
}

let _insertAfterIndex = -1;
let _insertSelectedIndex = null;
let _insertModalBound = false;

function ensureInsertModalBound() {
  if (_insertModalBound) return;
  _insertModalBound = true;
  const modal = $('modal-plan-insert');
  if (!modal) return;
  modal.querySelector('.modal-backdrop')?.addEventListener('click', closeInsertModal);
  $('plan-insert-cancel')?.addEventListener('click', closeInsertModal);
  $('plan-insert-confirm')?.addEventListener('click', confirmInsertSegment);
  $('plan-insert-filter')?.addEventListener('input', (e) => {
    renderInsertVideoList(e.target.value || '');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (modal.style.display !== 'flex') return;
    closeInsertModal();
  });
}

function closeInsertModal() {
  const modal = $('modal-plan-insert');
  if (modal) modal.style.display = 'none';
  _insertAfterIndex = -1;
  _insertSelectedIndex = null;
}

function openInsertModal(afterIndex) {
  ensureInsertModalBound();
  const modal = $('modal-plan-insert');
  if (!modal) {
    addToast('插入对话框不可用', 'error');
    return;
  }
  _insertAfterIndex = afterIndex;
  _insertSelectedIndex = null;
  const filter = $('plan-insert-filter');
  if (filter) filter.value = '';
  const hint = $('plan-insert-hint');
  if (hint) {
    const n = (state.plan?.sequence?.length || 0);
    if (n === 0 || afterIndex < 0) {
      hint.textContent = '选择要插入到规划开头的视频';
    } else if (afterIndex >= n - 1) {
      hint.textContent = '选择要插入到末尾的视频';
    } else {
      hint.textContent = `选择要插入到第 ${afterIndex + 1} 段之后的视频`;
    }
  }
  renderInsertVideoList('');
  const confirmBtn = $('plan-insert-confirm');
  if (confirmBtn) confirmBtn.disabled = true;
  modal.style.display = 'flex';
  setTimeout(() => filter?.focus(), 0);
}

function renderInsertVideoList(query) {
  const list = $('plan-insert-list');
  if (!list) return;
  const q = String(query || '').trim().toLowerCase();
  const videos = (state.videos || []).filter((v) => v && v.index);
  const filtered = q
    ? videos.filter((v) => {
        const hay = `${v.index} ${v.title || ''} ${v.file || ''}`.toLowerCase();
        return hay.includes(q);
      })
    : videos;

  if (!filtered.length) {
    list.innerHTML = videos.length
      ? '<p class="muted">没有匹配的视频</p>'
      : '<p class="muted">当前项目没有可用视频</p>';
    return;
  }

  list.innerHTML = filtered.map((v) => {
    const idx = String(v.index);
    const title = v.title || v.file || '(无标题)';
    const selected = _insertSelectedIndex != null && String(_insertSelectedIndex) === idx;
    return `<button type="button" class="plan-insert-item${selected ? ' selected' : ''}" data-index="${escapeHtml(idx)}" title="${escapeHtml(v.file || '')}">
      <span class="plan-insert-item-idx">[${escapeHtml(idx)}]</span>
      <span class="plan-insert-item-title">${escapeHtml(title)}</span>
    </button>`;
  }).join('');

  list.querySelectorAll('.plan-insert-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      _insertSelectedIndex = btn.dataset.index;
      list.querySelectorAll('.plan-insert-item').forEach((el) => {
        el.classList.toggle('selected', el.dataset.index === _insertSelectedIndex);
      });
      const confirmBtn = $('plan-insert-confirm');
      if (confirmBtn) confirmBtn.disabled = false;
    });
    btn.addEventListener('dblclick', () => {
      _insertSelectedIndex = btn.dataset.index;
      confirmInsertSegment();
    });
  });
}

function confirmInsertSegment() {
  const p = state.plan;
  if (!p) return;
  const index = _insertSelectedIndex != null ? String(_insertSelectedIndex).trim() : '';
  if (!index) {
    addToast('请先选择一个视频', 'warning');
    return;
  }
  const v = (state.videos || []).find((x) => String(x.index) === index);
  if (!v) {
    addToast(`找不到视频 [${index}]`, 'warning');
    return;
  }
  const afterIndex = _insertAfterIndex;
  const title = v.title || v.file || '';
  closeInsertModal();
  const next = insertSegment(p.sequence, afterIndex, { index, title });
  _expandedSegIndex = nextExpandedAfterInsert(afterIndex);
  applySequence(next, { highlightIndex: _expandedSegIndex });
}

function promptInsertAfter(afterIndex) {
  if (!state.plan) return;
  openInsertModal(afterIndex);
}

function openRangePickerForSegment(segIndex) {
  const p = state.plan;
  if (!p?.sequence?.[segIndex]) return;
  const seg = p.sequence[segIndex];
  const v = (state.videos || []).find((x) => String(x.index) === String(seg.index));
  if (!v?.file) {
    addToast(`找不到视频 [${seg.index || '?'}]，无法打开选区`, 'warning');
    return;
  }
  openPlanRangePicker({
    segIndex,
    video: v,
    useTimeline: seg.use_timeline || '',
    title: seg.title || '',
    onApply: ({ segIndex: i, use_timeline }) => {
      if (!state.plan?.sequence?.[i]) return;
      state.plan.sequence[i] = patchSegment(state.plan.sequence[i], { use_timeline });
      markDirty();
      _refreshPreviewTimeline();
      const li = document.querySelector(`#plan-list [data-preview-index="${i}"]`);
      if (li) {
        const headerTl = li.querySelector('.plan-seg-tl');
        if (headerTl) headerTl.textContent = use_timeline || '—';
        const input = li.querySelector('input[data-k="use_timeline"]');
        if (input) input.value = use_timeline;
      } else {
        renderPlan();
      }
      scheduleReadinessCheck();
    },
  });
}

/**
 * Prefill a segment's subtitle textarea with the saved plan subtitle, or fall
 * back to the video's voiceover text as a starting point (read from it, never
 * written back). Editing only updates the plan's subtitle field.
 * @param {HTMLElement} li
 * @param {object} seg
 */
function _prefillSubtitle(li, seg) {
  const subEl = li.querySelector('[data-k="subtitle"]');
  if (!subEl) return;
  const planText = typeof seg.subtitle === 'string' ? seg.subtitle.trim() : '';
  if (planText) {
    subEl.value = planText;
    subEl.placeholder = '';
    return;
  }
  const v = state.videos.find((x) => String(x.index) === String(seg.index));
  if (!v) { subEl.placeholder = '无关联视频'; return; }
  if (!v.script_json) { subEl.placeholder = '无口播文案文件'; return; }
  api('GET', `/api/voiceover?file=${encodeURIComponent(v.script_json)}`)
    .then((d) => {
      const text = typeof d?.voiceover === 'string' ? d.voiceover : '';
      subEl.placeholder = text ? '从口播文案预填，编辑将保存到规划' : '无口播文案';
      if (subEl.value === '') {
        subEl.value = text;
        seg.subtitle = text;
        markDirty();
      }
    })
    .catch(() => { subEl.placeholder = '加载失败'; });
}

export function renderPlan() {
  const p = state.plan;
  const pane = $('tab-plan');
  $('player-pane').classList.add('plan-mode');
  renderPreviewBar();
  if (!p) {
    pane.innerHTML = `
      <h3>vlog 剪辑规划</h3>
      <p class="muted">当前项目没有编排文件。</p>
      <p class="hint">请先运行流水线中的「vlog 剪辑规划」步骤，或在 CLI 执行 <code>python main.py plan</code>。</p>
    `;
    return;
  }
  if (!Array.isArray(p.sequence)) p.sequence = [];

  pane.innerHTML = `
    <h3>编排元信息</h3>
    ${state.availablePlans.length >= 1 ? `
    <label>分集
      <select id="plan-day-select">
        ${state.availablePlans.map(dp =>
          `<option value="${escapeHtml(dp.day_label)}" ${dp.day_label === state.currentDay ? 'selected' : ''}>${escapeHtml(dp.day_label)}</option>`
        ).join('')}
      </select>
    </label>
    ` : ''}
    <label>标题 <input data-field="day_title"></label>
    <label>主题 <input data-field="theme"></label>
    <label>开场提示 <textarea data-field="opening_tip" rows="2"></textarea></label>
    <label>收尾提示 <textarea data-field="ending_tip" rows="2"></textarea></label>
    <h3>顺序 (sequence) — ${p.sequence.length} 项</h3>
    <p class="hint">拖动手柄或使用 ↑↓ 调整顺序；点击片段跳转预览</p>
    <ol id="plan-list"></ol>
    <div id="plan-readiness" class="plan-readiness cut-section"></div>
  `;
  const daySelect = pane.querySelector('#plan-day-select');
  if (daySelect) {
    daySelect.onchange = async () => {
      const day = daySelect.value;
      if (day === state.currentDay) return;
      if (state.dirty && !confirm('切换分集将丢弃当前修改，确定吗？')) { daySelect.value = state.currentDay; return; }
      state.currentDay = day;
      state.plan = null;
      clearDirty();
      updateSidebarDay();
      await import('./sidebar.js').then(mod => mod.saveProject());
      await import('./sidebar.js').then(mod => mod.selectPlan());
    };
  }
  for (const k of ['day_title', 'theme', 'opening_tip', 'ending_tip']) {
    const el = pane.querySelector(`[data-field="${k}"]`);
    if (!el) continue;
    el.value = p[k] || '';
    el.oninput = () => {
      p[k] = el.value;
      markDirty();
      scheduleReadinessCheck();
    };
  }
  const ol = pane.querySelector('#plan-list');
  if (_expandedSegIndex != null) {
    if (!p.sequence.length) _expandedSegIndex = null;
    else if (_expandedSegIndex >= p.sequence.length) _expandedSegIndex = p.sequence.length - 1;
  }
  p.sequence.forEach((seg, i) => {
    const li = document.createElement('li');
    const expanded = _expandedSegIndex === i;
    const titleText = seg.title || '';
    const tlText = seg.use_timeline || '';
    const vid = seg.index || '?';
    li.className = 'plan-seg'
      + (state.previewActive && state.previewIndex === i ? ' preview-active' : '')
      + (expanded ? ' plan-seg-expanded' : '');
    li.dataset.previewIndex = String(i);
    li.innerHTML = `
      <div class="plan-seg-header">
        <span class="plan-drag-handle" title="拖拽排序" aria-hidden="true">⠿</span>
        <span class="plan-seg-ord">${i + 1}.</span>
        <span class="plan-seg-title-text" title="${escapeHtml(titleText)}">${escapeHtml(titleText || '(无标题)')}</span>
        <span class="plan-seg-tl">${escapeHtml(tlText || '—')}</span>
        <span class="plan-seg-vid">[${escapeHtml(String(vid))}]</span>
        <button type="button" class="plan-seg-chevron" data-chevron
          aria-expanded="${expanded ? 'true' : 'false'}"
          aria-label="${expanded ? '收起片段' : '展开片段'}">${expanded ? '▾' : '▸'}</button>
      </div>
      ${expanded ? `
      <div class="plan-seg-panel">
        <label class="plan-title-field">标题
          <input class="plan-title-input" value="${escapeHtml(titleText)}" data-k="title">
        </label>
        <label class="plan-timeline-row">时间轴
          <input value="${escapeHtml(tlText)}" data-k="use_timeline" placeholder="00:10-00:45">
          <button type="button" class="plan-ghost-btn" data-range-pick title="在源视频上拖选起止时间">选区</button>
        </label>
        <div class="plan-seg-fields">
          <label>理由
            <textarea rows="3" data-k="reason">${escapeHtml(seg.reason || '')}</textarea>
          </label>
          <label>口播提示（仅预览/旁白，不导出成片字幕）
            <textarea rows="3" data-k="voiceover_hint">${escapeHtml(seg.voiceover_hint || '')}</textarea>
          </label>
        </div>
        <div class="plan-seg-subtitles">
          <label>成片字幕（导出到剪映字幕轨）
            <textarea rows="3" data-k="subtitle" placeholder="加载中…"></textarea>
          </label>
        </div>
        <div class="plan-seg-actions">
          <button type="button" class="plan-icon-btn" data-move="up" title="上移" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button type="button" class="plan-icon-btn" data-move="down" title="下移" ${i === p.sequence.length - 1 ? 'disabled' : ''}>↓</button>
          <button type="button" class="plan-ghost-btn" data-ins title="在此后插入片段">+ 插入</button>
          <button type="button" class="plan-ghost-btn plan-ghost-btn--danger" data-del title="删除片段">删除</button>
        </div>
      </div>` : ''}
    `;
    // Panel chrome clicks must not seek preview / rebuild (only header does).
    li.querySelector('.plan-seg-panel')?.addEventListener('click', (e) => {
      if (e.target.closest('input, textarea, button')) return;
      e.stopPropagation();
    });
    li.querySelector('.plan-seg-header')?.addEventListener('click', (e) => {
      if (e.target.closest('button, .plan-drag-handle')) return;
      const sameExpanded = _expandedSegIndex === i;
      const samePreview = state.previewActive && state.previewIndex === i;
      // Already open on this segment while it is the preview target: no-op
      // (avoids wiping caret if user clicks header chrome mid-edit).
      if (sameExpanded && samePreview) return;

      _expandedSegIndex = i;
      const v = state.videos.find(x => String(x.index) === String(seg.index));
      if (!v) {
        setStatus(`找不到视频 [${seg.index}]，请重新生成规划`, 'warn');
        renderPlan();
        return;
      }
      if (state.previewActive) {
        if (state.previewIndex === i) {
          // Expand-only (preview already here)
          if (!sameExpanded) renderPlan();
          return;
        }
        state.previewIndex = i;
        _playPreviewSegment();
        renderPlan();
      } else {
        startPreview(i);
      }
    });
    li.querySelector('[data-chevron]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      _expandedSegIndex = _expandedSegIndex === i ? null : i;
      renderPlan();
    });
    li.querySelectorAll('[data-k]').forEach(inp => {
      inp.oninput = (e) => {
        p.sequence[i] = patchSegment(p.sequence[i], { [e.target.dataset.k]: e.target.value });
        // Keep collapsed header title/timeline in sync without full re-render on every keystroke
        if (e.target.dataset.k === 'title') {
          const t = li.querySelector('.plan-seg-title-text');
          if (t) {
            const val = e.target.value || '';
            t.textContent = val || '(无标题)';
            t.title = val;
          }
        } else if (e.target.dataset.k === 'use_timeline') {
          const t = li.querySelector('.plan-seg-tl');
          if (t) t.textContent = e.target.value || '—';
          _refreshPreviewTimeline();
        }
        markDirty();
        scheduleReadinessCheck();
      };
    });
    _prefillSubtitle(li, seg);
    li.querySelector('[data-move="up"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (i <= 0) return;
      _expandedSegIndex = nextExpandedAfterMove(i, i - 1, i);
      applySequence(reorderSequence(p.sequence, i, i - 1), { highlightIndex: i - 1 });
    });
    li.querySelector('[data-move="down"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (i >= p.sequence.length - 1) return;
      _expandedSegIndex = nextExpandedAfterMove(i, i + 1, i);
      applySequence(reorderSequence(p.sequence, i, i + 1), { highlightIndex: i + 1 });
    });
    li.querySelector('[data-ins]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      promptInsertAfter(i);
    });
    li.querySelector('[data-range-pick]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      openRangePickerForSegment(i);
    });
    li.querySelector('[data-del]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!confirm(`删除第 ${i + 1} 段「${seg.title || seg.index || ''}」？`)) return;
      const nextSeq = removeSegment(p.sequence, i);
      _expandedSegIndex = nextExpandedAfterDelete(_expandedSegIndex, i, nextSeq.length);
      applySequence(nextSeq);
    });
    const dragHandle = li.querySelector('.plan-drag-handle');
    if (dragHandle) {
      dragHandle.draggable = true;
      dragHandle.addEventListener('dragstart', (e) => {
        _dragFromIndex = i;
        _dropToIndex = null;
        li.classList.add('plan-seg-dragging');
        e.dataTransfer.effectAllowed = 'move';
        try {
          e.dataTransfer.setData('text/plain', String(i));
        } catch { /* ignore */ }
      });
      dragHandle.addEventListener('dragend', () => {
        li.classList.remove('plan-seg-dragging');
        clearDropIndicator();
        _dragFromIndex = null;
        if (document.getElementById('plan-list')) {
          const open = document.querySelector('#plan-list .plan-seg-expanded');
          const openIdx = open ? parseInt(open.dataset.previewIndex, 10) : null;
          if (openIdx !== _expandedSegIndex) renderPlan();
        }
      });
    }
    li.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (_dragFromIndex == null) return;
      autoScrollDuringDrag(e.clientY);
      const rect = li.getBoundingClientRect();
      const placeAfter = e.clientY > rect.top + rect.height / 2;
      const to = computeDropToIndex(_dragFromIndex, i, placeAfter, p.sequence.length);
      if (to == null) {
        clearDropIndicator();
        return;
      }
      paintDropIndicator(_dragFromIndex, to, p.sequence.length);
    });
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (_dragFromIndex == null) return;
      const rect = li.getBoundingClientRect();
      const placeAfter = e.clientY > rect.top + rect.height / 2;
      const to = computeDropToIndex(_dragFromIndex, i, placeAfter, p.sequence.length);
      const from = _dragFromIndex;
      clearDropIndicator();
      _dragFromIndex = null;
      li.classList.remove('plan-seg-dragging');
      if (to == null) return;
      _expandedSegIndex = nextExpandedAfterMove(from, to, _expandedSegIndex);
      applySequence(reorderSequence(p.sequence, from, to), { highlightIndex: to });
    });
    ol.appendChild(li);
  });

  ol.addEventListener('dragleave', (e) => {
    if (!ol.contains(e.relatedTarget)) {
      clearDropIndicator();
    }
  });

  // Pane-level dragover: keep scrolling when pointer is in empty chrome / padding
  pane.addEventListener('dragover', (e) => {
    if (_dragFromIndex == null) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    autoScrollDuringDrag(e.clientY);
  });

  // Prepend control when sequence empty or for first insert
  const insertBar = document.createElement('div');
  insertBar.className = 'plan-insert-bar';
  insertBar.innerHTML = `<button type="button" id="btn-plan-insert-end" class="btn-secondary">在末尾插入片段</button>`;
  ol.parentNode?.insertBefore(insertBar, ol.nextSibling);
  insertBar.querySelector('#btn-plan-insert-end')?.addEventListener('click', () => {
    const n = p.sequence?.length || 0;
    promptInsertAfter(n - 1); // -1 when empty → prepend
  });

  const cutSection = document.createElement('div');
  cutSection.className = 'cut-section';
  cutSection.innerHTML = `
    <h3>裁剪此分集</h3>
    <p class="hint">基于规划 ${escapeHtml(state.currentDay)} 裁剪所有片段</p>
    <label>视频来源
      <select id="cut-source">
        <option value="compressed" selected>压缩版 (compressed)</option>
        <option value="original">原片 (original)</option>
      </select>
    </label>
    <label><input type="checkbox" id="cut-reencode"> 重新编码 (默认 -c copy 快速)</label>
    <label>输出目录 (留空则 output/cuts/&lt;day&gt;)
      <span class="input-with-browse"><input id="cut-outdir" placeholder="例如 E:/剪辑素材/第一天"><button class="browse-btn" data-target="cut-outdir" type="button">浏览</button></span></label>
    <div class="cut-actions" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button id="btn-cut-exec" class="btn-primary" type="button">${icon('cut', 16)} 执行裁剪</button>
      <button id="btn-cut-open-dir" class="btn-secondary" type="button" title="在资源管理器中打开裁剪输出目录">${icon('folder', 16)} 打开目录</button>
    </div>
    <div id="cut-result" style="margin-top:12px"></div>
  `;
  pane.appendChild(cutSection);

  const exportSection = document.createElement('div');
  exportSection.className = 'cut-section';
  exportSection.style.marginTop = '16px';
  exportSection.innerHTML = `
    <h3>导出到剪映</h3>
    <p class="hint">生成剪映专业版可直接打开的草稿文件</p>
    <button id="btn-jianying-export" class="btn-primary">${icon('export', 16)} 导出到剪映</button>
    <div id="export-result" style="margin-top:8px;font-size:var(--text-sm)"></div>
  `;
  pane.appendChild(exportSection);

  $('cut-source')?.addEventListener('change', () => scheduleReadinessCheck());

  $('btn-cut-open-dir')?.addEventListener('click', async () => {
    const custom = $('cut-outdir')?.value?.trim();
    let path = custom;
    if (!path) {
      const base = state.config?.output_dir || '';
      const day = state.currentDay || 'day1';
      if (!base) {
        setStatus('无法解析默认裁剪目录（output_dir 未知）', 'warn');
        return;
      }
      path = `${String(base).replace(/[\\/]+$/, '')}/cuts/${day}`;
    }
    try {
      const r = await api('POST', '/api/fs/reveal', { path });
      if (r.ok) setStatus(`已打开: ${r.path || path}`, 'ok');
      else throw new Error(r.error || '打开失败');
    } catch (e) {
      setStatus('打开目录失败: ' + e.message, 'err');
    }
  });

  $('btn-jianying-export')?.addEventListener('click', async () => {
    const btn = $('btn-jianying-export');
    if (btn?.disabled) return;
    if (!ensureSavedBeforeAction()) return;
    await refreshReadinessPanel();
    if (!confirmWarningsIfNeeded()) return;
    const force = (_lastReadiness.warnings || []).length > 0;
    const resultDiv = $('export-result');
    resultDiv.innerHTML = '<span class="muted">导出中…</span>';
    if (btn) {
      btn.disabled = true;
      btn.dataset.prevLabel = btn.innerHTML;
      btn.textContent = '导出中...';
    }
    try {
      const { artifact } = await exportJianyingDraft(state.currentDay || 'day1', { force });
      resultDiv.innerHTML = `<span style="color:var(--ok,#484)">✓ 已导出到 ${escapeHtml(artifact)}</span>`;
      addToast('已导出到剪映', 'success', undefined, { persist: false });
    } catch (e) {
      resultDiv.innerHTML = `<span style="color:var(--err,#c44)">✗ 导出失败: ${escapeHtml(e.message || e)}</span>`;
      addToast('导出失败: ' + (e.message || e), 'error', 6000);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = btn.dataset.prevLabel || `${icon('export', 16)} 导出到剪映`;
      }
      updateActionButtonsGate();
    }
  });
  $('btn-cut-exec').onclick = executeCut;
  scheduleReadinessCheck();
}


export async function executeCut() {
  const btn = $('btn-cut-exec');
  const result = $('cut-result');
  if (btn?.disabled) return;
  if (!ensureSavedBeforeAction()) return;
  await refreshReadinessPanel();
  if (!confirmWarningsIfNeeded()) return;
  const force = (_lastReadiness.warnings || []).length > 0;
  btn.disabled = true;
  btn.textContent = '裁剪中...';
  result.innerHTML = '<p class="muted">请等待，正在裁剪视频片段...</p>';
  try {
    const dayLabel = state.currentDay;
    try {
      await api('GET', `/api/plan?day=${dayLabel}`);
    } catch (e) {
      result.innerHTML = `<p class="err">规划文件不存在: 请先运行 CLI 命令 <code>python main.py plan --day ${escapeHtml(dayLabel)}</code> 生成规划。</p>`;
      setStatus('裁剪失败: 规划文件不存在', 'err');
      btn.disabled = false;
      btn.textContent = '执行裁剪';
      updateActionButtonsGate();
      return;
    }
    const body = {
      day_label: dayLabel,
      source: $('cut-source').value,
      reencode: $('cut-reencode').checked,
      output_dir: $('cut-outdir').value.trim() || null,
      force,
      overwrite: false,
    };
    let r;
    try {
      r = await api('POST', '/api/cut', body);
    } catch (e) {
      if (e.status === 409 && e.body?.code === 'cut_output_exists') {
        const count = e.body.count || e.body.files?.length || '?';
        const dir = e.body.output_dir || body.output_dir || '';
        const preview = e.body.preview || (e.body.files || []).slice(0, 8).join('、') || '';
        const ok = confirm(
          `输出目录已有 ${count} 个裁剪视频，是否覆盖重剪？\n\n` +
          `目录: ${dir}\n` +
          (preview ? `示例: ${preview}\n\n` : '\n') +
          '将先备份旧文件，生成新裁剪成功后再删除备份。'
        );
        if (!ok) {
          result.innerHTML = '<p class="muted">已取消覆盖裁剪</p>';
          setStatus('已取消裁剪', 'warn');
          return;
        }
        r = await api('POST', '/api/cut', { ...body, overwrite: true });
      } else {
        throw e;
      }
    }
    result.innerHTML = `<p class="ok">裁剪完成</p><p>输出目录: ${escapeHtml(r.output_dir)}</p>`;
    setStatus('裁剪完成', 'ok', { persist: false });
    addToast('裁剪完成', 'success', undefined, { persist: false });
    import('./sidebar.js').then(mod => mod.saveProject());
  } catch (e) {
    result.innerHTML = `<p class="err">错误: ${escapeHtml(e.message)}</p>`;
    const msg = '裁剪失败: ' + e.message;
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = '执行裁剪';
    updateActionButtonsGate();
  }
}


let _saveGeneration = 0;

function _bumpSaveGeneration() {
  _saveGeneration += 1;
  return _saveGeneration;
}

export async function save() {
  if (!state.dirty) { setStatus('没有改动需要保存', 'warn'); return; }
  const generation = _bumpSaveGeneration();
  const entity = state.currentEntity;
  const day = state.currentDay;
  const tab = state.currentTab;
  const videoFile = state.currentVideo;
  const planData = state.plan;
  const textsData = state.texts;
  const voiceoverData = state.voiceover;
  const target = resolveEditorSaveTarget({
    entity,
    tab,
    configTab: state.configTab,
  });
  if (target.action === 'noop') {
    setStatus(target.reason || '当前页无可保存内容', 'warn');
    return;
  }
  try {
    if (target.action === 'config') {
      const ct = target.configTab || 'global';
      let r;
      if (ct === 'global') {
        r = await api('PUT', '/api/config/global', state.configGlobal);
      } else {
        r = await api('PUT', '/api/config/project', state.configProject);
      }
      if (r.error) throw new Error(r.error);
      if (generation !== _saveGeneration) return;
      clearDirty();
      const status = configSaveStatusForTab(ct);
      setStatus(status.message, status.level);
      addToast(status.message, status.level === 'ok' ? 'success' : 'warning');
      try {
        if (typeof window.refreshFfmpegDepsUi === 'function') {
          await window.refreshFfmpegDepsUi();
        } else {
          const { loadFfmpegDeps, renderVideoList } = await import('./sidebar-data.js');
          await loadFfmpegDeps();
          renderVideoList();
        }
      } catch { /* ignore */ }
      return;
    }
    if (target.action === 'plan') {
      await api('PUT', `/api/plan?day=${day}`, planData);
    } else if (target.action === 'texts') {
      const v = state.videos.find(x => x.file === videoFile);
      if (!v || !v.text_json) throw new Error('当前视频没有 texts JSON');
      await api('PUT', `/api/texts?file=${encodeURIComponent(v.text_json)}`, textsData);
    } else if (target.action === 'voiceover') {
      const v = state.videos.find(x => x.file === videoFile);
      if (!v || !v.script_json) throw new Error('当前视频没有 voiceover JSON');
      await api('PUT', `/api/voiceover?file=${encodeURIComponent(v.script_json)}`, voiceoverData);
    } else {
      setStatus('当前页无可保存内容', 'warn');
      return;
    }
    if (generation !== _saveGeneration) return;
    clearDirty();
    setStatus('已保存', 'ok');
    addToast('已保存', 'success');
    if (target.action === 'plan') scheduleReadinessCheck();
  } catch (e) {
    const msg = '保存失败: ' + e.message;
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000);
  }
}
