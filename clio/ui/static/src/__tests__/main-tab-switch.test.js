import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

/**
 * Boot-level regression guard for the editor tab strip.
 *
 * The tab click handler used to take its element parameter as `t`, shadowing the
 * i18n `t()` helper imported at the top of main.js. The dirty guard therefore
 * threw `t is not a function` and the tab silently refused to switch whenever
 * there were unsaved edits. `state.currentTab` is the observable contract.
 */

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => (String(h).toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

const VIDEOS = [
  { file: 'a.mp4', source: 'compressed', index: '001', title: 'A', text_json: 'a.json', match: { source: 'original', file: 'A.MP4' } },
  { file: 'b.mp4', source: 'compressed', index: '002', title: 'B', script_json: 'b.json', match: { source: 'original', file: 'B.MP4' } },
];

const API_GET = {
  '/api/projects': { projects: [{ name: 'P', project_dir: 'C:/p', is_current: true }], last_project: { name: 'P', project_dir: 'C:/p' } },
  '/api/config': { project_dir: 'C:/p', output_dir: 'C:/p/output', ui: { language: 'en' } },
  '/api/config/global': { ui: { language: 'en', desktop_notifications: false }, paths: {}, ai: { providers: {}, default_tasks: {} } },
  '/api/config/project': {},
  '/api/config/raw': { needs_init: false },
  '/api/deps/ffmpeg': { ok: true },
  '/api/deps/keys': { missing: [], unbound_tasks: [] },
  '/api/cut/orphaned-backups': { items: [] },
  '/api/project': { currentDay: 'day1', source: 'compressed', name: 'P', steps: {}, lastEntity: 'video', lastVideo: 'a.mp4' },
  '/api/plans': { plans: [] },
  '/api/videos': { videos: VIDEOS, groups: {} },
  '/api/videos/selected': { videos: ['a.mp4', 'b.mp4'] },
  '/api/texts': { title: 'T' },
  '/api/voiceover': { title: 'V' },
  '/api/plan': { day: 'day1', segments: [] },
  '/api/tasks': { tasks: [] },
  '/api/notifications': { notifications: [], unread: 0 },
};

const tick = (ms = 150) => new Promise((done) => setTimeout(done, ms));

function indexBody() {
  const html = readFileSync(resolve(process.cwd(), 'clio/ui/static/index.html'), 'utf8');
  const match = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  return match ? match[1].replace(/<script[\s\S]*?<\/script>/gi, '') : '';
}

describe('editor tab switching', () => {
  let state;
  let errors;

  beforeAll(async () => {
    errors = [];
    window.addEventListener('error', (event) => errors.push(event.error || event.message));
    document.body.innerHTML = indexBody();
    Element.prototype.scrollIntoView = function scrollIntoView() {};
    HTMLCanvasElement.prototype.getContext = () => null;
    window.matchMedia = () => ({
      matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.EventSource = class { close() {} addEventListener() {} };
    sessionStorage.clear();
    localStorage.clear();
    globalThis.fetch = vi.fn(async (url, options = {}) => {
      const raw = String(url);
      if (raw.startsWith('/static/locales/')) {
        const file = resolve(process.cwd(), 'clio/ui/static', raw.replace(/^\//, ''));
        return jsonResponse(JSON.parse(readFileSync(file, 'utf8')));
      }
      if ((options.method || 'GET') !== 'GET') return jsonResponse({ ok: true });
      return jsonResponse(API_GET[raw.split('?')[0]] ?? {});
    });
    window.confirm = () => true;
    await import('../main.js');
    await tick(400);
    ({ state } = await import('../state.js'));
  }, 30000);

  afterAll(() => {
    vi.restoreAllMocks();
  });

  it('switches tabs while the editor is clean', async () => {
    state.currentEntity = 'video';
    state.dirty = false;
    document.querySelector('.tab[data-tab="voiceover"]').click();
    await tick(50);
    expect(state.currentTab).toBe('voiceover');
  });

  it('switches tabs while there are unsaved edits', async () => {
    state.currentEntity = 'video';
    state.currentTab = 'texts';
    state.dirty = true;
    document.querySelector('.tab[data-tab="transcript"]').click();
    await tick(50);
    expect(state.currentTab).toBe('transcript');
    expect(errors).toEqual([]);
  });
});
