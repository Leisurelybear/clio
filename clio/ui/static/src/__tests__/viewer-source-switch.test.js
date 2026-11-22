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

beforeEach(async () => {
  vi.resetModules();
  const { seedCatalogs } = await import('./catalogs.js');
  seedCatalogs();
  document.body.innerHTML = `
    <video id="player"></video>
    <span id="player-name"></span>
    <span id="player-time"></span>
    <span id="status"></span>
  `;
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
  window.HTMLMediaElement.prototype.load = vi.fn();
});

describe('source switching', () => {
  it('drops the pending metadata handler of a superseded source', async () => {
    const { state } = await import('../state.js');
    const { playVideoSegment } = await import('../viewer.js');
    state.currentEntity = 'video';
    state.source = 'compressed';
    state.videos = [{ file: 'a.mp4' }, { file: 'b.mp4' }];
    const player = document.getElementById('player');
    const seeks = [];
    Object.defineProperty(player, 'currentTime', {
      configurable: true,
      get: () => 0,
      set: value => { seeks.push(value); },
    });

    playVideoSegment('a.mp4', 5);
    playVideoSegment('b.mp4', 9);
    player.dispatchEvent(new Event('loadedmetadata'));

    expect(seeks).toEqual([9]);
    expect(player.src).toContain('file=b.mp4');
  });
});
