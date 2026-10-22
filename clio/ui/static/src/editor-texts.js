import { state } from './state.js';
import { $, $$, escapeHtml, parseTimecode, fmtTime, markDirty, setStatus } from './utils.js';
import { api, icon } from './api.js';
import { playVideoSegment } from './viewer.js';
import { renderVideoList } from './sidebar.js';
import { renderRefineUI, refineCurrentFile } from './editor-refine.js';
import { renderEmptyArtifactHtml, bindEmptyArtifactActions } from './empty-states.js';
import { transcriptionReadinessPrompt } from './transcription-status.js';
import { t } from './i18n.js';


function _jumpToTranscriptTime(timeSec) {
  const player = $('player');
  const currentVideo = state.videos.find(x => x.file === state.currentVideo);
  if (!currentVideo) return;

  if (state.source === 'original') {
    player.currentTime = timeSec + (currentVideo.offset_sec || 0);
    player.play().catch(() => {});
    return;
  }

  // compressed mode: find which split segment contains the time
  const groupKey = currentVideo.group_key;
  if (!groupKey) {
    player.currentTime = timeSec;
    player.play().catch(() => {});
    return;
  }

  const candidates = state.videos.filter(
    v => v.group_key === groupKey && v.offset_sec != null && v.duration_sec > 0
  );
  if (candidates.length <= 1) {
    player.currentTime = timeSec;
    player.play().catch(() => {});
    return;
  }

  candidates.sort((a, b) => a.offset_sec - b.offset_sec);
  let target = candidates[0];
  for (let i = 0; i < candidates.length; i++) {
    const v = candidates[i];
    const end = v.offset_sec + v.duration_sec;
    if (timeSec >= v.offset_sec && timeSec < end) {
      target = v;
      break;
    }
    if (timeSec >= v.offset_sec) target = v;
  }

  const seekTo = timeSec - target.offset_sec;
  if (target.file !== state.currentVideo) {
    state.currentVideo = target.file;
    renderVideoList();
    playVideoSegment(target.file, seekTo);
  } else {
    player.currentTime = seekTo;
    player.play().catch(() => {});
  }
}

function _transcriptTimeFromPlayer(playerTime, offsetSec = 0) {
  return Math.max(0, Number(playerTime || 0) - Number(offsetSec || 0));
}

async function _rerunCurrentVideo(task) {
  const file = state.currentVideo;
  if (!file) {
    setStatus(t('editor.pick_video_first'), 'warn');
    return;
  }
  const v = state.videos.find(x => x.file === file);
  setStatus(t('sidebar.rerunning', { task, file }), 'ok', { persist: false });
  try {
    const r = await api('POST', '/api/rerun', {
      video: file,
      task,
      source: state.source,
      index: v?.index || undefined,
      abspath: v?.abs_path || undefined,
    });
    if (r.ok) {
        setStatus(r.message || t('sidebar.rerun_started', { task }), 'ok', { persist: false });
      import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file, r.task_id));
    } else {
        throw new Error(r.error || t('sidebar.rerun_failed'));
    }
  } catch (e) {
    setStatus(t('sidebar.rerun_failed_detail', { detail: e.message }), 'err');
  }
}

export function renderTexts() {
  const data = state.texts;
  const pane = $('tab-texts');
  if (!state.currentVideo) {
    pane.innerHTML = renderEmptyArtifactHtml('texts', { hasVideo: false });
    return;
  }
  if (!data) {
    pane.innerHTML = renderEmptyArtifactHtml('texts', { hasVideo: true });
    bindEmptyArtifactActions(pane, {
      onRun: () => import('./sidebar.js').then(m => m.goToRunTab()),
      onRerun: (task) => _rerunCurrentVideo(task),
    });
    return;
  }

  const coverTs = String(data.cover_timestamp || '').trim();
  const coverFile = String(data.cover_file || '').trim();
  const coverName = coverFile ? coverFile.replace(/\\/g, '/').split('/').pop() : '';
  let coverHtml = '';
  if (coverName || coverTs) {
    let coverUrl = '';
    if (coverName) {
      const params = new URLSearchParams();
      params.set('file', coverName);
      if (state.currentProjectName) params.set('project', state.currentProjectName);
      if (state.currentProjectDir) params.set('project_dir', state.currentProjectDir);
      const tok = sessionStorage.getItem('api_token');
      if (tok) params.set('token', tok);
      coverUrl = `/api/cover?${params.toString()}`;
    }
    const img = coverUrl
      ? `<img class="texts-cover-img" alt="${t('editor.cover_alt')}" src="${escapeHtml(coverUrl)}" loading="lazy">`
      : '';
    coverHtml = `
      <div class="texts-cover">
        ${img}
        <div class="texts-cover-meta">
          <div><strong>${t('editor.cover_title')}</strong></div>
          <div>${t('editor.cover_time', { ts: escapeHtml(coverTs || '—') })}</div>
          ${coverName ? `<div>${t('editor.cover_file', { name: escapeHtml(coverName) })}</div>` : ''}
          <p class="hint" style="margin:6px 0 0">${t('editor.cover_hint')}</p>
          ${coverTs ? `<button type="button" class="btn-secondary" id="btn-cover-seek" style="margin-top:8px">${t('editor.cover_seek')}</button>` : ''}
        </div>
      </div>`;
  }

  pane.innerHTML = `
    <h3>${t('editor.section.basic')}</h3>
    ${coverHtml}
    <label>${t('plan.field.title')} <input data-field="title"></label>
    <label>${t('editor.field.location')} <input data-field="location"></label>
    <label>${t('editor.field.mood')} <input data-field="mood"></label>
    <label>${t('editor.field.suggested_use')} <input data-field="suggested_use"></label>
    <label>${t('editor.field.summary')} <textarea data-field="summary" rows="3"></textarea></label>
    <h3>${t('editor.section.timeline', { count: (data.timeline || []).length })}</h3>
    <p class="hint">${t('editor.timeline_hint')}</p>
    <ol id="timeline-list"></ol>
  `;
  for (const k of ['title', 'location', 'mood', 'suggested_use']) {
    const el = pane.querySelector(`[data-field="${k}"]`);
    el.value = data[k] || '';
    el.oninput = () => { data[k] = el.value; markDirty(); };
  }
  const sumEl = pane.querySelector('[data-field="summary"]');
  sumEl.value = data.summary || '';
  sumEl.oninput = () => { data.summary = sumEl.value; markDirty(); };

  pane.querySelector('#btn-cover-seek')?.addEventListener('click', () => {
    if (coverTs) _jumpToTranscriptTime(parseTimecode(coverTs));
  });

  const ol = pane.querySelector('#timeline-list');
  for (const seg of (data.timeline || [])) {
    const li = document.createElement('li');
    li.className = 'timeline-seg';
    li.innerHTML = `
      <div class="seg-time">${escapeHtml(seg.start)} - ${escapeHtml(seg.end)}</div>
      <textarea data-seg-desc rows="2">${escapeHtml(seg.description || '')}</textarea>
    `;
    li.onclick = (e) => {
      if (e.target.tagName === 'TEXTAREA') return;
      _jumpToTranscriptTime(parseTimecode(seg.start));
    };
    li.querySelector('textarea').oninput = (e) => {
      seg.description = e.target.value;
      markDirty();
    };
    ol.appendChild(li);
  }
  pane.insertAdjacentHTML('beforeend', renderRefineUI('texts'));
  pane.querySelector(`#btn-refine-texts`).onclick = () => refineCurrentFile('texts');
}


export function renderTranscript() {
  const data = state.transcript;
  const pane = $('tab-transcript');
  if (!state.currentVideo) {
    pane.innerHTML = renderEmptyArtifactHtml('transcript', { hasVideo: false });
    return;
  }
  if (!data || !data.ok) {
    pane.innerHTML = renderEmptyArtifactHtml('transcript', { hasVideo: true });
    bindEmptyArtifactActions(pane, {
      onRun: () => import('./sidebar.js').then(m => m.goToRunTab()),
      onRerun: (task) => _rerunCurrentVideo(task),
    });
    pane.insertAdjacentHTML(
      'beforeend',
      `<p style="margin-top:12px">
        <button id="btn-create-transcript" type="button" class="btn-primary">${icon('plus', 14)} ${t('editor.create_transcript')}</button>
      </p>
      <p class="hint" style="margin-top:8px">${t('editor.create_transcript_hint')}</p>`
    );
    pane.querySelector('#btn-create-transcript').onclick = async () => {
      const v = state.videos.find(x => x.file === state.currentVideo);
      if (!v) { setStatus(t('editor.video_not_found'), 'err'); return; }
      try {
        const r = await api('POST', `/api/transcripts?video=${encodeURIComponent(v.file)}`, { create: true });
        if (r.ok) {
          state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
          renderTranscript();
        } else {
          setStatus(t('editor.create_failed', { detail: r.error || '' }), 'err');
        }
      } catch (e) {
        setStatus(t('editor.create_failed', { detail: e.message }), 'err');
      }
    };
    renderWhisperInstallPrompt(pane);
    return;
  }
  const segments = data.segments || [];
  pane.innerHTML = `
    <h3>${t('editor.section.transcript', { count: segments.length })}</h3>
    <p class="hint">${t('editor.transcript_hint')}</p>
    <button id="btn-toggle-add-transcript" class="btn-secondary" style="font-size:var(--text-sm);margin-bottom:8px">${icon('plus', 14)} ${t('editor.add_transcript')}</button>
    <div id="add-transcript-form" style="display:none;margin-bottom:12px;padding:10px;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-md)"></div>
    <ol id="transcript-list"></ol>
  `;

  const addForm = pane.querySelector('#add-transcript-form');
  addForm.innerHTML = `
    <p style="margin:0 0 8px;font-weight:600">${t('editor.add_transcript_title')}</p>
    <div style="display:flex;gap:8px;margin-bottom:6px">
      <label style="flex:1">${t('editor.field.start_time')}
        <div style="display:flex;gap:4px;align-items:center">
          <input id="add-ts-start" type="text" placeholder="${t('editor.time_placeholder')}" style="flex:1">
          <button class="btn-secondary btn-ts-now" data-target="add-ts-start" title="${t('editor.use_current_time')}" type="button" style="font-size:var(--text-xs);padding:4px 8px">${t('editor.use_current')}</button>
        </div>
      </label>
      <label style="flex:1">${t('editor.field.end_time')}
        <div style="display:flex;gap:4px;align-items:center">
          <input id="add-ts-end" type="text" placeholder="${t('editor.time_placeholder')}" style="flex:1">
          <button class="btn-secondary btn-ts-now" data-target="add-ts-end" title="${t('editor.use_current_time')}" type="button" style="font-size:var(--text-xs);padding:4px 8px">${t('editor.use_current')}</button>
        </div>
      </label>
    </div>
    <label style="margin-bottom:6px;display:block">${t('editor.field.transcript_text')}
      <textarea id="add-ts-text" rows="2" style="width:100%;margin-top:2px" placeholder="${t('editor.transcript_placeholder')}"></textarea>
    </label>
    <div style="display:flex;gap:8px">
      <button id="btn-add-transcript" class="btn-primary">${icon('plus', 14)} ${t('common.add')}</button>
      <span id="add-ts-msg" class="muted" style="font-size:var(--text-xs);align-self:center"></span>
    </div>
  `;

  const toggleBtn = pane.querySelector('#btn-toggle-add-transcript');
  toggleBtn.onclick = () => {
    const visible = addForm.style.display !== 'none';
    addForm.style.display = visible ? 'none' : 'block';
    toggleBtn.innerHTML = visible ? `${icon('plus', 14)} ${t('editor.add_transcript')}` : `${icon('x', 14)} ${t('common.collapse')}`;
  };

  addForm.querySelectorAll('.btn-ts-now').forEach(btn => {
    btn.onclick = () => {
      const player = $('player');
      const currentVideo = state.videos.find(v => v.file === state.currentVideo);
      const sec = _transcriptTimeFromPlayer(player.currentTime, state.source === 'original' ? currentVideo?.offset_sec : 0);
      const target = $(btn.dataset.target);
      if (target) target.value = fmtTime(sec);
    };
  });

  const addBtn = pane.querySelector('#btn-add-transcript');
  const addMsg = pane.querySelector('#add-ts-msg');
  addBtn.onclick = async () => {
    const startStr = $('add-ts-start').value.trim();
    const endStr = $('add-ts-end').value.trim();
    const text = $('add-ts-text').value.trim();
    if (!startStr || !endStr) {
      addMsg.textContent = t('editor.err.need_times');
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (!text) {
      addMsg.textContent = t('editor.err.need_text');
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (text.length > 5000) {
      addMsg.textContent = t('editor.err.text_too_long', { limit: 5000 });
      addMsg.style.color = 'var(--error)';
      return;
    }
    const start = parseTimecode(startStr);
    const end = parseTimecode(endStr);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      addMsg.textContent = t('editor.err.bad_time');
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (start < 0) {
      addMsg.textContent = t('editor.err.negative_start');
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (end <= start) {
      addMsg.textContent = t('editor.err.end_before_start');
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (end > 86400) {
      addMsg.textContent = t('editor.err.time_too_large');
      addMsg.style.color = 'var(--error)';
      return;
    }
    addBtn.disabled = true;
    addMsg.textContent = t('common.adding');
    addMsg.style.color = '';
    const v = state.videos.find(x => x.file === state.currentVideo);
    if (!v) { addMsg.textContent = t('editor.video_not_found'); addBtn.disabled = false; return; }
    try {
      const r = await api('POST', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
        start, end, text, revision: state.transcript && state.transcript.revision,
      });
      if (r.ok) {
        state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
        addMsg.textContent = t('editor.added');
        addMsg.style.color = 'var(--success)';
        setStatus(t('editor.added_status'), 'ok');
        $('add-ts-start').value = '';
        $('add-ts-end').value = '';
        $('add-ts-text').value = '';
        renderTranscript();
      } else {
        const message = t('editor.add_failed', { detail: r.error || '' });
        addMsg.textContent = message;
        addMsg.style.color = 'var(--error)';
        setStatus(message, 'err');
      }
    } catch (e) {
      if (e.status === 409) {
        const message = t('editor.stale_refreshed_detail', { detail: e.message || '' });
        addMsg.textContent = message;
        addMsg.style.color = 'var(--error)';
        setStatus(message, 'err');
        state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
        renderTranscript();
      } else {
        const message = t('editor.add_failed', { detail: e.message });
        addMsg.textContent = message;
        addMsg.style.color = 'var(--error)';
        setStatus(message, 'err');
      }
    } finally {
      addBtn.disabled = false;
    }
  };

  const ol = pane.querySelector('#transcript-list');
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const li = document.createElement('li');
    li.className = 'transcript-seg';
    const startStr = fmtTime(seg.start || 0);
    const endStr = fmtTime(seg.end || 0);
    const confidence = seg.avg_logprob != null
      ? `<span class="muted" title="avg_logprob=${seg.avg_logprob.toFixed(2)}">${Math.round(Math.max(0, Math.min(100, (1 + (seg.avg_logprob || 0)) * 100)))}%</span>`
      : '';
    const lowIcon = seg.low_confidence ? `<span class="low-conf-icon" title="${t('editor.low_confidence')}">⚠</span>` : '';
    li.innerHTML = `
      <div class="seg-time">${escapeHtml(startStr)} - ${escapeHtml(endStr)} ${confidence} ${lowIcon}
        <button class="seg-del" data-index="${i}" title="${t('editor.delete_segment')}">×</button>
      </div>
      <div class="seg-text" data-seg-index="${i}">${escapeHtml(seg.text || '')}</div>
    `;
    li.onclick = () => {
      _jumpToTranscriptTime(seg.start);
    };
    const textDiv = li.querySelector('.seg-text');
    textDiv.ondblclick = (e) => {
      e.stopPropagation();
      const origV = state.videos.find(x => x.file === state.currentVideo);
      const inp = document.createElement('textarea');
      inp.className = 'seg-text-edit';
      inp.value = seg.text || '';
      inp.rows = 2;
      textDiv.replaceWith(inp);
      inp.focus();
      inp.onblur = async () => {
        const newText = inp.value;
        if (newText === seg.text) {
          const newDiv = document.createElement('div');
          newDiv.className = 'seg-text';
          newDiv.dataset.segIndex = i;
          newDiv.textContent = seg.text;
          inp.replaceWith(newDiv);
          newDiv.ondblclick = textDiv.ondblclick;
          return;
        }
        const v = origV || state.videos.find(x => x.file === state.currentVideo);
        if (!v) { setStatus(t('editor.video_not_found'), 'err'); return; }
        try {
          const r = await api('PUT', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
            segment_index: i,
            text: newText,
            revision: state.transcript && state.transcript.revision,
          });
          if (r.ok) {
            seg.text = newText;
            setStatus(t('editor.transcript_saved'), 'ok');
          } else {
            setStatus(t('plan.save_failed', { detail: r.error || t('common.unknown_error') }), 'err');
          }
        } catch (e) {
          if (e.status === 409) {
            setStatus(t('editor.stale_refreshed'), 'err');
            state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
            renderTranscript();
          } else {
            setStatus(t('plan.save_failed', { detail: e.message }), 'err');
          }
        }
        const newDiv = document.createElement('div');
        newDiv.className = 'seg-text';
        newDiv.dataset.segIndex = i;
        newDiv.textContent = seg.text;
        inp.replaceWith(newDiv);
        newDiv.ondblclick = textDiv.ondblclick;
      };
      inp.onkeydown = (e) => {
        if (e.key === 'Escape') { inp.blur(); }
      };
    };
    const delBtn = li.querySelector('.seg-del');
    delBtn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t('editor.confirm_delete_segment', { index: i + 1 }))) return;
      const v = state.videos.find(x => x.file === state.currentVideo);
      if (!v) { setStatus(t('editor.video_not_found'), 'err'); return; }
      try {
        const r = await api('PUT', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
          segment_index: i, delete: true, revision: state.transcript && state.transcript.revision,
        });
        if (r.ok) {
          segments.splice(i, 1);
          setStatus(t('editor.deleted'), 'ok');
          renderTranscript();
        } else {
          setStatus(t('editor.delete_failed', { detail: r.error || t('common.unknown_error') }), 'err');
        }
      } catch (e) {
        if (e.status === 409) {
          setStatus(t('editor.stale_refreshed'), 'err');
          state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
          renderTranscript();
        } else {
          setStatus(t('editor.delete_failed', { detail: e.message }), 'err');
        }
      }
    };
    ol.appendChild(li);
  }
}


export async function renderWhisperInstallPrompt(pane) {
  let check;
  try { check = await api('GET', '/api/whisper/check'); } catch { return; }
  const prompt = transcriptionReadinessPrompt(check);
  if (!prompt) return;
  const div = document.createElement('div');
  div.id = 'whisper-install-prompt';
  div.style.cssText = 'margin-top:12px;padding:12px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px';
  const settingsLink = prompt.settingsLabel
    ? ` <a href="#" id="whisper-go-settings" style="text-decoration:underline;color:var(--accent)">${escapeHtml(prompt.settingsLabel)}</a>`
    : '';
  div.innerHTML = `
    <p style="margin:0 0 8px;font-weight:600">⚠ ${escapeHtml(prompt.title)}</p>
    <p style="margin:0;font-size:var(--text-sm);color:var(--text-secondary)">${escapeHtml(prompt.before)}${settingsLink}${escapeHtml(prompt.after)}</p>
  `;
  pane.appendChild(div);
  var link = $('whisper-go-settings');
  if (link) {
    link.onclick = function(e) { e.preventDefault(); import('./sidebar.js').then(function(s) { s.selectConfig(); }); };
  }
}

export { _transcriptTimeFromPlayer };
