import { describe, it, expect, beforeEach } from 'vitest';
import {
  subtitleControlsModel,
  mergeSubtitleSettings,
  safeColor,
  renderSubtitleSettingsPanel,
  serializeLatestWrites,
} from '../subtitle-settings.js';

describe('subtitleControlsModel', () => {
  it('uses config values', () => {
    const m = subtitleControlsModel({
      preview: {
        subtitles: {
          font_size: 28, min_font_size: 12, font_color: '#f00',
          background: 'rgba(0,0,0,.8)', outline: '2px solid #000',
          font_family: 'sans-serif', mode: 'scroll',
          max_lines: 3, max_len_per_line: 20,
          scroll_speed: 80,
        },
      },
    });
    expect(m).toEqual({
      font_size: 28, min_font_size: 12, font_color: '#f00',
      background: 'rgba(0,0,0,.8)', outline: '2px solid #000',
      font_family: 'sans-serif', mode: 'scroll',
      max_lines: 3, max_len_per_line: 20,
      scroll_speed: 80,
    });
  });

  it('fills defaults when config missing', () => {
    const m = subtitleControlsModel(null);
    expect(m.font_size).toBe(22);
    expect(m.min_font_size).toBe(14);
    expect(m.font_color).toBe('#fff');
    expect(m.mode).toBe('auto');
    expect(m.max_lines).toBe(2);
    expect(m.max_len_per_line).toBe(16);
    expect(m.scroll_speed).toBe(40);
  });

  it('rejects unsafe CSS values with whitelist defaults', () => {
    const m = subtitleControlsModel({
      preview: {
        subtitles: {
          font_color: '" onfocus="alert(1)',
          background: '<img src=x onerror=alert(1)>',
          outline: 'red" autofocus"',
          font_family: "' style=position:fixed",
          mode: 'hack',
        },
      },
    });
    expect(m.font_color).toBe('#fff');
    expect(m.background).toBe('rgba(0,0,0,.55)');
    expect(m.outline).toBe('0 0 2px rgba(0,0,0,.8)');
    expect(m.font_family).toBe('');
    expect(m.mode).toBe('auto');
  });
});

describe('mergeSubtitleSettings', () => {
  it('preserves other preview/project fields, updates subtitles', () => {
    const merged = mergeSubtitleSettings(
      { preview: { subtitles: { font_size: 22 } } },
      { font_size: 30, mode: 'scroll' },
    );
    expect(merged.preview.subtitles).toEqual({ font_size: 30, mode: 'scroll' });
    expect(merged.preview.subtitles.font_size).toBe(30);
  });

  it('creates preview.subtitles when absent', () => {
    const merged = mergeSubtitleSettings(
      { some: 1 },
      { font_size: 26 },
    );
    expect(merged.preview.subtitles.font_size).toBe(26);
    expect(merged.some).toBe(1);
  });

  it('null/undefined project → fresh object', () => {
    const merged = mergeSubtitleSettings(null, { font_size: 26 });
    expect(merged.preview.subtitles.font_size).toBe(26);
  });

  it('drops undefined values so stale keys are removed', () => {
    const merged = mergeSubtitleSettings(
      { preview: { subtitles: { font_size: 22, mode: 'auto' } } },
      { font_size: 30, mode: undefined },
    );
    expect(merged.preview.subtitles).toEqual({ font_size: 30 });
  });
});

describe('safeColor', () => {
  it('passes through valid hex/color strings', () => {
    expect(safeColor('#ffffff')).toBe('#ffffff');
    expect(safeColor('#fff')).toBe('#fff');
    expect(safeColor('')).toBe(null);
    expect(safeColor(null)).toBe(null);
  });

  it('rejects non-color payloads', () => {
    expect(safeColor('url(javascript:alert(1))', '#000')).toBe('#000');
    expect(safeColor('red; background:url(x)', '#111')).toBe('#111');
  });
});

describe('serializeLatestWrites', () => {
  it('serializes out-of-order payloads and writes newest last', async () => {
    const written = [];
    let releaseFirst;
    const firstGate = new Promise((r) => { releaseFirst = r; });
    const write = (payload) => {
      if (payload === 'first') return firstGate.then(() => written.push(payload));
      written.push(payload);
      return undefined;
    };
    const enqueue = serializeLatestWrites(write);

    // Two rapid calls.  The second must be written only after the first
    // completes, so 'second' can never be overwritten by 'first'.
    const p1 = enqueue('first');
    const p2 = enqueue('second');
    releaseFirst();
    await Promise.all([p1, p2]);
    expect(written).toEqual(['first', 'second']);
  });

  it('coalesces bursts so only the latest pending payload is flushed', async () => {
    const writes = [];
    const write = (payload) => { writes.push(payload); };
    const enqueue = serializeLatestWrites(write);

    enqueue('a');
    enqueue('b');
    await enqueue('c');
    expect(writes).toEqual(['a', 'c']);
  });
});

describe('renderSubtitleSettingsPanel', () => {
  beforeEach(() => { document.querySelector('.test-subs-wrap')?.remove(); });

  function mountConfig(over = {}) {
    return {
      preview: {
        subtitles: {
          font_size: 26, min_font_size: 12, font_color: '#123456',
          mode: 'auto', ...over,
        },
      },
    };
  }

  it('renders controls populated from config', () => {
    const changed = [];
    const wrap = document.createElement('div');
    wrap.className = 'test-subs-wrap';
    document.body.appendChild(wrap);
    renderSubtitleSettingsPanel(wrap, {
      config: mountConfig(),
      onChange: (u) => changed.push(u),
    });

    const size = wrap.querySelector('[data-subtle="font_size"]');
    const color = wrap.querySelector('[data-subtle="font_color"]');
    const mode = wrap.querySelector('[data-subtle="mode"]');
    expect(size).toBeTruthy();
    expect(size.value).toBe('26');
    expect(color.value).toBe('#123456');
    expect(mode.value).toBe('auto');
  });

  it('emits onChange with numberized font_size', () => {
    const changed = [];
    const wrap = document.createElement('div');
    wrap.className = 'test-subs-wrap';
    document.body.appendChild(wrap);
    renderSubtitleSettingsPanel(wrap, {
      config: mountConfig(),
      onChange: (u) => changed.push(u),
    });
    const size = wrap.querySelector('[data-subtle="font_size"]');
    size.value = '30';
    size.dispatchEvent(new Event('change', { bubbles: true }));
    expect(changed.length).toBe(1);
    expect(changed[0].font_size).toBe(30);
  });

  it('emits onChange with string color', () => {
    const changed = [];
    const wrap = document.createElement('div');
    wrap.className = 'test-subs-wrap';
    document.body.appendChild(wrap);
    renderSubtitleSettingsPanel(wrap, {
      config: mountConfig(),
      onChange: (u) => changed.push(u),
    });
    const color = wrap.querySelector('[data-subtle="font_color"]');
    color.value = '#abcdef';
    color.dispatchEvent(new Event('change', { bubbles: true }));
    expect(changed[0].font_color).toBe('#abcdef');
  });

  it('renders scroll_speed control and emits as a number', () => {
    const changed = [];
    const wrap = document.createElement('div');
    wrap.className = 'test-subs-wrap';
    document.body.appendChild(wrap);
    renderSubtitleSettingsPanel(wrap, {
      config: mountConfig({ scroll_speed: 66 }),
      onChange: (u) => changed.push(u),
    });
    const speed = wrap.querySelector('[data-subtle="scroll_speed"]');
    expect(speed).toBeTruthy();
    expect(speed.value).toBe('66');
    speed.value = '90';
    speed.dispatchEvent(new Event('change', { bubbles: true }));
    expect(changed[0].scroll_speed).toBe(90);
  });
});