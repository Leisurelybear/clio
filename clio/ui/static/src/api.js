import { state } from './state.js';

let _showingAuth = false;

async function api(method, url, body, options = {}) {
  const opts = { method, headers: {} };
  if (options.signal) {
    opts.signal = options.signal;
  }
  const token = sessionStorage.getItem('api_token');
  if (token) {
    opts.headers['Authorization'] = `Bearer ${token}`;
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  // 自动附加 project + project_dir 查询参数
  let sep = url.includes('?') ? '&' : '?';
  if (state.currentProjectName) {
    url += `${sep}project=${encodeURIComponent(state.currentProjectName)}`;
    sep = '&';
  }
  if (state.currentProjectDir) {
    url += `${sep}project_dir=${encodeURIComponent(state.currentProjectDir)}`;
  }
  const r = await fetch(url, opts);
  if (r.status === 401) {
    if (!_showingAuth) {
      _showingAuth = true;
      document.getElementById('modal-auth').style.display = 'flex';
    }
    throw new Error('HTTP 401: 需要 API Token');
  }
  if (!r.ok) {
    const txt = await r.text();
    let detail = txt;
    let body = null;
    try {
      body = JSON.parse(txt);
      detail = body.error || txt;
    } catch { /* plain text */ }
    const err = new Error(`HTTP ${r.status}: ${detail}`);
    err.status = r.status;
    err.body = body;
    throw err;
  }
  if (r.status === 204) return null;
  const ct = r.headers.get('Content-Type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

// SVG icons (Lucide-style, zero external deps)
function icon(name, size = 18) {
  const icons = {
    play: '<polygon points="5 3 19 12 5 21 5 3"/>',
    folder: '<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>',
    file: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    circle: '<circle cx="12" cy="12" r="10"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>',
    clipboard: '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>',
    file_text: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
    music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    video: '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>',
    save: '<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    export: '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    cut: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><line x1="8.12" y1="8.12" x2="15.88" y2="15.88"/><line x1="15.88" y1="8.12" x2="8.12" y2="15.88"/>',
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    chevron_right: '<polyline points="9 18 15 12 9 6"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  };
  const d = icons[name] || icons.file;
  return `<span class="icon"><svg viewBox="0 0 24 24">${d}</svg></span>`;
}

function submitToken() {
  const input = document.getElementById('auth-token-input');
  const token = input.value.trim();
  if (!token) return;
  sessionStorage.setItem('api_token', token);
  _showingAuth = false;
  location.reload();
}

export { api, icon, submitToken };
