import { beforeEach, describe, expect, it, vi } from 'vitest';

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

vi.mock('../plan-subtitle.js', () => ({
  renderPlanSubtitleFromState: vi.fn(),
  hidePlanSubtitle: vi.fn(),
  initSubtitleDrag: vi.fn(),
}));

vi.mock('../video-fade.js', () => ({
  phaseNewSource: vi.fn((_player, _canvas, options) => {
    options?.setSrc?.();
    return () => {};
  }),
}));

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = `
    <video id="player"></video>
    <canvas id="video-fade"></canvas>
    <span id="player-name"></span>
    <span id="player-time"></span>
    <span id="status"></span>
    <div id="preview-seg-bar"></div>
    <div id="preview-bar"></div>
  `;
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
  window.HTMLMediaElement.prototype.load = vi.fn();
});

describe('cut preview fallback', () => {
  it('retries the same global time from the source video when a cut fails to load', async () => {
    const { state } = await import('../state.js');
    const { setupPlayer, seekToGlobal } = await import('../viewer.js');
    state.currentEntity = 'plan';
    state.currentProjectName = 'trip';
    state.source = 'compressed';
    state.previewActive = false;
    state.previewIndex = -1;
    state.previewGlobalSec = 0;
    state.plan = {
      sequence: [{ index: '001', title: 'A', use_timeline: '00:00-00:05' }],
    };
    state.videos = [{ file: '001_A.mp4', index: '001', offset_sec: 0 }];
    state.planPreviewMedia = {
      day: 'day1',
      segments: [{
        file: '001_A_seg_001.mp4',
        duration_sec: 5,
        start_sec: 0,
        end_sec: 5,
        source: { video_index: '001' },
      }],
    };
    state.previewCutFallbacks.clear();
    setupPlayer();
    const player = document.getElementById('player');

    seekToGlobal(2, { play: false });
    expect(state._previewMedia.mode).toBe('cut');
    expect(player.src).toContain('source=cut');
    player.onerror();

    expect(state.previewCutFallbacks.has(0)).toBe(true);
    expect(state._previewMedia.mode).toBe('source');
    expect(player.src).toContain('source=compressed');
    expect(state.previewGlobalSec).toBe(2);
  });
});
