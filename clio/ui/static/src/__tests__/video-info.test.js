import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return {
    ...actual,
    api: vi.fn(),
  };
});

import { api } from '../api.js';
import {
  bindVideoInfoHover,
  buildInfoSections,
  clearVideoInfoCache,
  closeVideoInfoModal,
  fetchVideoInfo,
  formatBitrate,
  formatBytes,
  formatDuration,
  formatFps,
  formatMtime,
  infoSectionsToHtml,
  openVideoInfoModal,
} from '../video-info.js';

const SAMPLE_INFO = {
  ok: true,
  source: 'compressed',
  file: '001_a.mp4',
  abs_path: 'D:/trip/out/compressed/001_a.mp4',
  exists: true,
  size_bytes: 5 * 1024 * 1024,
  mtime: new Date(2024, 0, 2, 3, 4).getTime() / 1000,
  duration_sec: 65,
  width: 640,
  height: 360,
  fps: 29.97,
  bit_rate_bps: 1200000,
  codec: 'h264',
  format_name: 'mov,mp4,m4a,3gp,3g2,mj2',
  compression: {
    source_size_bytes: 100 * 1024 * 1024,
    target_size_bytes: 5 * 1024 * 1024,
    ratio: 0.05,
    settings: { max_width: 640, fps: 30, target_size_mb: 5 },
  },
};

describe('video-info formatters', () => {
  it('formats byte sizes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatBytes(100 * 1024 * 1024)).toBe('100 MB');
    expect(formatBytes(1.5 * 1024 ** 3)).toBe('1.5 GB');
    expect(formatBytes(null)).toBe('—');
  });

  it('formats durations', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(65)).toBe('1:05');
    expect(formatDuration(3661)).toBe('1:01:01');
    expect(formatDuration(null)).toBe('—');
  });

  it('formats fps, bitrate and mtime', () => {
    expect(formatFps(29.97002997)).toBe('29.97 fps');
    expect(formatFps(0)).toBe('—');
    expect(formatBitrate(5000000)).toBe('5.00 Mbps');
    expect(formatBitrate(128000)).toBe('128 kbps');
    expect(formatBitrate(null)).toBe('—');
    expect(formatMtime(new Date(2024, 0, 2, 3, 4).getTime() / 1000)).toBe('2024-01-02 03:04');
    expect(formatMtime(null)).toBe('—');
  });
});

describe('buildInfoSections', () => {
  it('groups basic, media and compression fields', () => {
    const sections = buildInfoSections(SAMPLE_INFO);
    expect(sections.map((s) => s.title)).toEqual(['基本信息', '视频参数', '压缩对比']);

    const basic = sections[0].rows;
    expect(basic.find((r) => r.label === '路径')).toMatchObject({
      value: 'D:/trip/out/compressed/001_a.mp4',
      wrap: true,
    });
    expect(basic.find((r) => r.label === '大小').value).toBe('5.0 MB');
    expect(basic.find((r) => r.label === '时长').value).toBe('1:05');
    expect(basic.find((r) => r.label === '修改时间').value).toBe('2024-01-02 03:04');

    const media = sections[1].rows;
    expect(media.find((r) => r.label === '分辨率').value).toBe('640×360');
    expect(media.find((r) => r.label === '帧率').value).toBe('29.97 fps');
    expect(media.find((r) => r.label === '码率').value).toBe('1.20 Mbps');
    expect(media.find((r) => r.label === '编码').value).toBe('h264');

    const compression = sections[2].rows;
    expect(compression.find((r) => r.label === '原始大小').value).toBe('100 MB');
    expect(compression.find((r) => r.label === '压缩后大小').value).toBe('5.0 MB');
    expect(compression.find((r) => r.label === '体积比').value).toBe('5.0%');
    expect(compression.find((r) => r.label === '压缩参数').value).toBe(
      '最大宽度 640 · 帧率 30 · 目标体积 5'
    );
  });

  it('marks offline rows and omits empty sections', () => {
    const sections = buildInfoSections({
      ok: true,
      abs_path: 'D:/gone.mp4',
      exists: false,
      size_bytes: null,
      duration_sec: null,
      mtime: null,
    });
    expect(sections).toHaveLength(1);
    expect(sections[0].rows.find((r) => r.label === '状态').value).toBe('文件离线或不存在');
    expect(sections[0].rows.find((r) => r.label === '大小').value).toBe('—');
  });
});

describe('infoSectionsToHtml', () => {
  it('escapes labels and values', () => {
    const html = infoSectionsToHtml([
      { title: '<script>', rows: [{ label: 'a<b', value: 'c&d' }] },
    ]);
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('a&lt;b');
    expect(html).toContain('c&amp;d');
    expect(html).not.toContain('<script>');
  });
});

describe('fetchVideoInfo', () => {
  beforeEach(() => {
    api.mockReset();
  });

  it('sends source, file and absolute path', async () => {
    api.mockResolvedValue(SAMPLE_INFO);
    await fetchVideoInfo({ file: '001_a.mp4', abs_path: 'D:/x.mp4' }, 'compressed');
    expect(api).toHaveBeenCalledWith(
      'GET',
      '/api/video/info?source=compressed&file=001_a.mp4&abspath=D%3A%2Fx.mp4'
    );
  });

  it('uses the match path for original rows without abs_path', async () => {
    api.mockResolvedValue(SAMPLE_INFO);
    await fetchVideoInfo(
      { file: 'a.mp4', match: { abs_path: 'D:/trip/a.mp4' } },
      'original'
    );
    expect(api).toHaveBeenCalledWith(
      'GET',
      '/api/video/info?source=original&file=a.mp4&abspath=D%3A%2Ftrip%2Fa.mp4'
    );
  });
});

describe('hover tooltip', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.mockReset();
    clearVideoInfoCache();
    document.body.innerHTML = '<ul><li id="row"></li></ul>';
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('waits for the hover delay before fetching and shows details', async () => {
    api.mockResolvedValue(SAMPLE_INFO);
    const li = document.getElementById('row');
    bindVideoInfoHover(li, { file: '001_a.mp4', abs_path: 'D:/x.mp4' }, 'compressed');

    li.dispatchEvent(new MouseEvent('mouseenter'));
    expect(api).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(400);
    expect(api).toHaveBeenCalledTimes(1);

    const tip = document.querySelector('.video-info-tooltip');
    expect(tip.classList.contains('open')).toBe(true);
    expect(tip.textContent).toContain('D:/trip/out/compressed/001_a.mp4');
    expect(tip.textContent).toContain('640×360');
  });

  it('hides when the pointer leaves and reuses the cached result', async () => {
    api.mockResolvedValue(SAMPLE_INFO);
    const li = document.getElementById('row');
    bindVideoInfoHover(li, { file: '001_a.mp4' }, 'compressed');

    li.dispatchEvent(new MouseEvent('mouseenter'));
    await vi.advanceTimersByTimeAsync(400);
    li.dispatchEvent(new MouseEvent('mouseleave'));
    const tip = document.querySelector('.video-info-tooltip');
    expect(tip.classList.contains('open')).toBe(false);

    li.dispatchEvent(new MouseEvent('mouseenter'));
    await vi.advanceTimersByTimeAsync(400);
    expect(api).toHaveBeenCalledTimes(1);
    expect(tip.classList.contains('open')).toBe(true);
  });

  it('does not fetch when the pointer leaves before the delay', async () => {
    const li = document.getElementById('row');
    bindVideoInfoHover(li, { file: '001_a.mp4' }, 'compressed');
    li.dispatchEvent(new MouseEvent('mouseenter'));
    li.dispatchEvent(new MouseEvent('mouseleave'));
    await vi.advanceTimersByTimeAsync(400);
    expect(api).not.toHaveBeenCalled();
  });
});

describe('video info modal', () => {
  beforeEach(() => {
    api.mockReset();
    clearVideoInfoCache();
    document.body.innerHTML = `
      <div id="modal-video-info" class="modal" style="display:none;">
        <div class="modal-backdrop"></div>
        <h3><span id="video-info-name"></span></h3>
        <div id="video-info-body"></div>
        <button id="video-info-close"></button>
      </div>`;
  });

  it('opens, renders and closes', async () => {
    api.mockResolvedValue(SAMPLE_INFO);
    await openVideoInfoModal({ file: '001_a.mp4' }, 'compressed');
    const modal = document.getElementById('modal-video-info');
    expect(modal.style.display).toBe('flex');
    expect(document.getElementById('video-info-name').textContent).toBe('a.mp4');
    expect(document.getElementById('video-info-body').textContent).toContain('640×360');
    closeVideoInfoModal();
    expect(modal.style.display).toBe('none');
  });

  it('renders an error state when the request fails', async () => {
    api.mockRejectedValue(new Error('boom'));
    await openVideoInfoModal({ file: '001_a.mp4' }, 'compressed');
    expect(document.getElementById('video-info-body').textContent).toContain('读取视频信息失败');
  });
});
