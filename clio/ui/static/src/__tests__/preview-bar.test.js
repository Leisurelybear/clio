import { beforeEach, describe, it, expect, vi } from 'vitest';

vi.mock('../waveform.js', () => ({
  loadWaveformForCurrentVideo: vi.fn(),
  loadPlanWaveform: vi.fn(),
  recomposePlanWaveformFromCache: vi.fn(),
  setWaveformPlanBridge: vi.fn(),
  updateWaveformPlayhead: vi.fn(),
  bindWaveformScrub: vi.fn(),
  getPlanWaveformTotal: vi.fn(() => 0),
  isPlanWaveformMode: vi.fn(() => false),
}));

beforeEach(async () => {
  vi.resetModules();
  const { seedCatalogs } = await import('./catalogs.js');
  seedCatalogs();
  document.body.innerHTML = `
    <div id="preview-bar" style="display:none"></div>
    <div id="preview-seg-bar"></div>
    <span id="preview-seg-name"></span>
    <span id="player-time"></span>
  `;
});

describe('renderPreviewBar segment blocks', () => {
  it('does not bind click handlers that jump to segment start', async () => {
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    state.previewIndex = 0;
    state.previewGlobalSec = 5;
    state.plan = {
      sequence: [
        { index: '001', title: 'A', use_timeline: '00:00-00:10' },
        { index: '002', title: 'B', use_timeline: '00:00-00:20' },
      ],
    };

    const { renderPreviewBar } = await import('../viewer.js');
    renderPreviewBar();

    const blocks = document.querySelectorAll('.preview-seg-block');
    expect(blocks.length).toBe(2);
    blocks.forEach((el) => {
      expect(el.onclick).toBeNull();
      // No inline handler either
      expect(el.getAttribute('onclick')).toBeNull();
    });
    // Scrub chrome still present
    expect(document.getElementById('preview-progress-fill')).toBeTruthy();
    expect(document.getElementById('preview-playhead')).toBeTruthy();
  });

  it('renders empty placeholder when plan has no sequence', async () => {
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    state.plan = { sequence: [] };
    const { renderPreviewBar } = await import('../viewer.js');
    renderPreviewBar();
    expect(document.getElementById('preview-seg-bar').innerHTML).toContain('暂无可预览内容');
  });
});
