import { state } from './state.js';
import { $, $$, escapeHtml, parseTimecode, fmtTime, markDirty, setStatus } from './utils.js';
import { api, icon } from './api.js';
import { playVideoSegment } from './viewer.js';
import { renderVideoList } from './sidebar.js';
import { renderRefineUI, refineCurrentFile } from './editor-refine.js';
import { renderEmptyArtifactHtml, bindEmptyArtifactActions } from './empty-states.js';


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
    setStatus('请先选择视频', 'warn');
    return;
  }
  const v = state.videos.find(x => x.file === file);
  setStatus(`正在重跑 ${task} (${file})...`, 'ok');
  try {
    const r = await api('POST', '/api/rerun', {
      video: file,
      task,
      source: state.source,
      index: v?.index || undefined,
      abspath: v?.abs_path || undefined,
    });
    if (r.ok) {
      setStatus(r.message || `${task} 已启动`, 'ok');
      import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file, r.task_id));
    } else {
      throw new Error(r.error || '重跑失败');
    }
  } catch (e) {
    setStatus('重跑失败: ' + e.message, 'err');
  }
}

export function renderTexts() {
  const t = state.texts;
  const pane = $('tab-texts');
  if (!state.currentVideo) {
    pane.innerHTML = renderEmptyArtifactHtml('texts', { hasVideo: false });
    return;
  }
  if (!t) {
    pane.innerHTML = renderEmptyArtifactHtml('texts', { hasVideo: true });
    bindEmptyArtifactActions(pane, {
      onRun: () => import('./sidebar.js').then(m => m.goToRunTab()),
      onRerun: (task) => _rerunCurrentVideo(task),
    });
    return;
  }

  const coverTs = String(t.cover_timestamp || '').trim();
  const coverFile = String(t.cover_file || '').trim();
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
      ? `<img class="texts-cover-img" alt="AI 封面帧" src="${escapeHtml(coverUrl)}" loading="lazy">`
      : '';
    coverHtml = `
      <div class="texts-cover">
        ${img}
        <div class="texts-cover-meta">
          <div><strong>AI 封面参考</strong></div>
          <div>时间点：${escapeHtml(coverTs || '—')}</div>
          ${coverName ? `<div>文件：<code>${escapeHtml(coverName)}</code></div>` : ''}
          <p class="hint" style="margin:6px 0 0">分析时 AI 建议的封面帧（非叠字成品）。可跳到播放器对应位置。</p>
          ${coverTs ? `<button type="button" class="btn-secondary" id="btn-cover-seek" style="margin-top:8px">跳到封面时间</button>` : ''}
        </div>
      </div>`;
  }

  pane.innerHTML = `
    <h3>基础信息</h3>
    ${coverHtml}
    <label>标题 <input data-field="title"></label>
    <label>位置 <input data-field="location"></label>
    <label>情绪 <input data-field="mood"></label>
    <label>建议用途 <input data-field="suggested_use"></label>
    <label>摘要 <textarea data-field="summary" rows="3"></textarea></label>
    <h3>时间轴 (timeline) — ${(t.timeline || []).length} 段</h3>
    <p class="hint">点击 segment 跳到该时间；点击文字框可编辑</p>
    <ol id="timeline-list"></ol>
  `;
  for (const k of ['title', 'location', 'mood', 'suggested_use']) {
    const el = pane.querySelector(`[data-field="${k}"]`);
    el.value = t[k] || '';
    el.oninput = () => { t[k] = el.value; markDirty(); };
  }
  const sumEl = pane.querySelector('[data-field="summary"]');
  sumEl.value = t.summary || '';
  sumEl.oninput = () => { t.summary = sumEl.value; markDirty(); };

  pane.querySelector('#btn-cover-seek')?.addEventListener('click', () => {
    if (coverTs) _jumpToTranscriptTime(parseTimecode(coverTs));
  });

  const ol = pane.querySelector('#timeline-list');
  for (const seg of (t.timeline || [])) {
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
  const t = state.transcript;
  const pane = $('tab-transcript');
  if (!state.currentVideo) {
    pane.innerHTML = renderEmptyArtifactHtml('transcript', { hasVideo: false });
    return;
  }
  if (!t || !t.ok) {
    pane.innerHTML = renderEmptyArtifactHtml('transcript', { hasVideo: true });
    bindEmptyArtifactActions(pane, {
      onRun: () => import('./sidebar.js').then(m => m.goToRunTab()),
      onRerun: (task) => _rerunCurrentVideo(task),
    });
    pane.insertAdjacentHTML(
      'beforeend',
      `<p style="margin-top:12px">
        <button id="btn-create-transcript" type="button" class="btn-primary">${icon('plus', 14)} 创建手动转录</button>
      </p>
      <p class="hint" style="margin-top:8px">手动创建后可自由编辑时间轴；也可上方重跑 Whisper 或去运行流水线自动生成。</p>`
    );
    pane.querySelector('#btn-create-transcript').onclick = async () => {
      const v = state.videos.find(x => x.file === state.currentVideo);
      if (!v) { setStatus('找不到当前视频', 'err'); return; }
      try {
        const r = await api('POST', `/api/transcripts?video=${encodeURIComponent(v.file)}`, { create: true });
        if (r.ok) {
          state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
          renderTranscript();
        } else {
          setStatus('创建失败: ' + (r.error || ''), 'err');
        }
      } catch (e) {
        setStatus('创建失败: ' + e.message, 'err');
      }
    };
    renderWhisperInstallPrompt(pane);
    return;
  }
  const segments = t.segments || [];
  pane.innerHTML = `
    <h3>语音转录 (ASR) — ${segments.length} 段</h3>
    <p class="hint">点击 segment 跳到对应时间；双击文字框可编辑</p>
    <button id="btn-toggle-add-transcript" class="btn-secondary" style="font-size:var(--text-sm);margin-bottom:8px">${icon('plus', 14)} 添加手动转录</button>
    <div id="add-transcript-form" style="display:none;margin-bottom:12px;padding:10px;background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-md)"></div>
    <ol id="transcript-list"></ol>
  `;

  const addForm = pane.querySelector('#add-transcript-form');
  addForm.innerHTML = `
    <p style="margin:0 0 8px;font-weight:600">手动添加转录条目</p>
    <div style="display:flex;gap:8px;margin-bottom:6px">
      <label style="flex:1">开始时间
        <div style="display:flex;gap:4px;align-items:center">
          <input id="add-ts-start" type="text" placeholder="MM:SS 或秒数" style="flex:1">
          <button class="btn-secondary btn-ts-now" data-target="add-ts-start" title="使用当前视频时间" type="button" style="font-size:var(--text-xs);padding:4px 8px">← 当前</button>
        </div>
      </label>
      <label style="flex:1">结束时间
        <div style="display:flex;gap:4px;align-items:center">
          <input id="add-ts-end" type="text" placeholder="MM:SS 或秒数" style="flex:1">
          <button class="btn-secondary btn-ts-now" data-target="add-ts-end" title="使用当前视频时间" type="button" style="font-size:var(--text-xs);padding:4px 8px">← 当前</button>
        </div>
      </label>
    </div>
    <label style="margin-bottom:6px;display:block">转录文字
      <textarea id="add-ts-text" rows="2" style="width:100%;margin-top:2px" placeholder="输入这段语音对应的文字..."></textarea>
    </label>
    <div style="display:flex;gap:8px">
      <button id="btn-add-transcript" class="btn-primary">${icon('plus', 14)} 添加</button>
      <span id="add-ts-msg" class="muted" style="font-size:var(--text-xs);align-self:center"></span>
    </div>
  `;

  const toggleBtn = pane.querySelector('#btn-toggle-add-transcript');
  toggleBtn.onclick = () => {
    const visible = addForm.style.display !== 'none';
    addForm.style.display = visible ? 'none' : 'block';
    toggleBtn.innerHTML = visible ? `${icon('plus', 14)} 添加手动转录` : `${icon('x', 14)} 收起`;
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
      addMsg.textContent = '请输入开始和结束时间（格式 MM:SS 或秒数）';
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (!text) {
      addMsg.textContent = '请输入转录文字';
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (text.length > 5000) {
      addMsg.textContent = '文字过长（超过 5000 字符）';
      addMsg.style.color = 'var(--error)';
      return;
    }
    const start = parseTimecode(startStr);
    const end = parseTimecode(endStr);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      addMsg.textContent = '时间格式无效，请使用 MM:SS 或秒数（如 12.5）';
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (start < 0) {
      addMsg.textContent = '开始时间不能为负数';
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (end <= start) {
      addMsg.textContent = '结束时间必须大于开始时间';
      addMsg.style.color = 'var(--error)';
      return;
    }
    if (end > 86400) {
      addMsg.textContent = '时间值过大（超过 24 小时）';
      addMsg.style.color = 'var(--error)';
      return;
    }
    addBtn.disabled = true;
    addMsg.textContent = '添加中...';
    addMsg.style.color = '';
    const v = state.videos.find(x => x.file === state.currentVideo);
    if (!v) { addMsg.textContent = '找不到当前视频'; addBtn.disabled = false; return; }
    try {
      const r = await api('POST', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
        start, end, text, revision: state.transcript && state.transcript.revision,
      });
      if (r.ok) {
        state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
        addMsg.textContent = '✓ 已添加';
        addMsg.style.color = 'var(--success)';
        $('add-ts-start').value = '';
        $('add-ts-end').value = '';
        $('add-ts-text').value = '';
        renderTranscript();
      } else {
        addMsg.textContent = '添加失败: ' + (r.error || '');
        addMsg.style.color = 'var(--error)';
      }
    } catch (e) {
      if (e.status === 409) {
        addMsg.textContent = '内容已被其他窗口修改，已刷新: ' + (e.message || '');
        addMsg.style.color = 'var(--error)';
        state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
        renderTranscript();
      } else {
        addMsg.textContent = '添加失败: ' + e.message;
        addMsg.style.color = 'var(--error)';
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
    const lowIcon = seg.low_confidence ? '<span class="low-conf-icon" title="低置信度，建议复核">⚠</span>' : '';
    li.innerHTML = `
      <div class="seg-time">${escapeHtml(startStr)} - ${escapeHtml(endStr)} ${confidence} ${lowIcon}
        <button class="seg-del" data-index="${i}" title="删除此段">×</button>
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
        if (!v) { setStatus('找不到当前视频', 'err'); return; }
        try {
          const r = await api('PUT', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
            segment_index: i,
            text: newText,
            revision: state.transcript && state.transcript.revision,
          });
          if (r.ok) {
            seg.text = newText;
            setStatus('转录文本已保存', 'ok');
          } else {
            setStatus('保存失败: ' + (r.error || '未知错误'), 'err');
          }
        } catch (e) {
          if (e.status === 409) {
            setStatus('内容已被其他窗口修改，已刷新', 'err');
            state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
            renderTranscript();
          } else {
            setStatus('保存失败: ' + e.message, 'err');
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
      if (!confirm(`确定删除第 ${i + 1} 段转录？`)) return;
      const v = state.videos.find(x => x.file === state.currentVideo);
      if (!v) { setStatus('找不到当前视频', 'err'); return; }
      try {
        const r = await api('PUT', `/api/transcripts?video=${encodeURIComponent(v.file)}`, {
          segment_index: i, delete: true, revision: state.transcript && state.transcript.revision,
        });
        if (r.ok) {
          segments.splice(i, 1);
          setStatus('已删除', 'ok');
          renderTranscript();
        } else {
          setStatus('删除失败: ' + (r.error || '未知错误'), 'err');
        }
      } catch (e) {
        if (e.status === 409) {
          setStatus('内容已被其他窗口修改，已刷新', 'err');
          state.transcript = await api('GET', `/api/transcripts?video=${encodeURIComponent(v.file)}`);
          renderTranscript();
        } else {
          setStatus('删除失败: ' + e.message, 'err');
        }
      }
    };
    ol.appendChild(li);
  }
}


export async function renderWhisperInstallPrompt(pane) {
  let check;
  try { check = await api('GET', '/api/whisper/check'); } catch { return; }
  if (!check) return;
  if (check.model_cached) return;
  const installed = check.installed;
  const div = document.createElement('div');
  div.id = 'whisper-install-prompt';
  div.style.cssText = 'margin-top:12px;padding:12px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px';
  if (check.frozen) {
    div.innerHTML = `
      <p style="margin:0 0 8px;font-weight:600">⚠ 打包版不内置 Whisper 转录</p>
      <p style="margin:0;font-size:var(--text-sm);color:var(--text-secondary)">faster-whisper（约 4 GB）未打包进 clio.exe。如需转录，请改用源码版运行 <code>python main.py whisper install</code>。</p>
    `;
    pane.appendChild(div);
    return;
  }
  div.innerHTML = `
    <p style="margin:0 0 8px;font-weight:600">⚠ Whisper 模型未下载</p>
    <p style="margin:0 0 8px;font-size:var(--text-sm);color:var(--text-secondary)">需要下载 ${installed ? '模型文件（约 1-2 GB）' : 'faster-whisper 依赖及模型文件'}，请前往 <a href="#" id="whisper-go-settings" style="text-decoration:underline;color:var(--accent)">设置 → Whisper 模型管理</a> 手动下载。</p>
  `;
  pane.appendChild(div);
  var settingsLink = $('whisper-go-settings');
  if (settingsLink) {
    settingsLink.onclick = function(e) { e.preventDefault(); import('./sidebar.js').then(function(s) { s.selectConfig(); }); };
  }
}

export { _transcriptTimeFromPlayer };
