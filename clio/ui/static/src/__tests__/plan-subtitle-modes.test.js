import { describe, it, expect, beforeEach } from 'vitest';
import {
  planSubtitleBatches,
  scheduleBatchTiming,
  packAtTime,
  computeFontShrink,
  renderPlanSubtitle,
} from '../plan-subtitle.js';
import { state } from '../state.js';

describe('planSubtitleBatches', () => {
  it('packs short sentences into a single batch', () => {
    const b = planSubtitleBatches('今天。出发。', { mode: 'auto', maxLines: 2, maxLen: 16 });
    expect(b).toHaveLength(1);
  });

  it('auto: long text packs to maxLines per batch', () => {
    const b = planSubtitleBatches('第一句很长很长很长很长很长啊。第二句也很长很长很长很长。', {
      mode: 'auto', maxLines: 2, maxLen: 10,
    });
    expect(b.length).toBeGreaterThan(1);
    expect(b[0].length).toBeLessThanOrEqual(2);
  });

  it('multi: lines packed into groups of maxLines', () => {
    const b = planSubtitleBatches('一。二。三。四。', { mode: 'multi', maxLines: 2, maxLen: 16 });
    expect(b).toEqual([['一。', '二。'], ['三。', '四。']]);
  });

  it('scroll: single batch with joined full text', () => {
    const b = planSubtitleBatches('一很长段。二很长段。', { mode: 'scroll', maxLines: 1, maxLen: 16 });
    expect(b).toHaveLength(1);
    expect(b[0]).toEqual(['一很长段。二很长段。']);
  });

  it('auto keeps a long sentence intact (does not split across batches)', () => {
    // 长句被 maxLen 拆成两行，auto 必须保持同一句完整，不跨 batch 拆散。
    const b = planSubtitleBatches('今天天气真好。感谢大家的到来我们下次再见吧。', {
      mode: 'auto', maxLines: 2, maxLen: 8,
    });
    expect(b.some((batch) => batch.some((l) => l.includes('感谢大家')))).toBe(true);
    const longLines = b.find((batch) => batch.some((l) => l.includes('感谢大家')));
    expect(longLines.join('')).toContain('感谢大家的到来');
    expect(longLines.join('')).toContain('下次再见');
  });

  it('multi splits flat lines at maxLines even across a long sentence', () => {
    const b = planSubtitleBatches('今天天气真好。第二句很长很长的句子内容。', {
      mode: 'multi', maxLines: 2, maxLen: 6,
    });
    // 拆句后第二句占两行，多行模式按最大行数直接切分
    const lines = b.flat();
    expect(lines.length).toBeGreaterThan(2);
  });

  it('auto and multi differ when a sentence spans multiple lines', () => {
    const opts = { maxLines: 2, maxLen: 6 };
    const text = '今天天气真好。第二句很长很长的句子内容。';
    const auto = planSubtitleBatches(text, { ...opts, mode: 'auto' });
    const multi = planSubtitleBatches(text, { ...opts, mode: 'multi' });
    expect(JSON.stringify(auto)).not.toBe(JSON.stringify(multi));
  });

  it('empty text → []', () => {
    expect(planSubtitleBatches('   ', { mode: 'auto' })).toEqual([]);
  });
});

describe('scheduleBatchTiming', () => {
  it('evenly distributes batches over duration', () => {
    expect(scheduleBatchTiming(30, 3)).toEqual([
      { startSec: 0, endSec: 10, index: 0 },
      { startSec: 10, endSec: 20, index: 1 },
      { startSec: 20, endSec: 30, index: 2 },
    ]);
  });

  it('last batch clamped to duration', () => {
    const s = scheduleBatchTiming(31, 2);
    expect(s[1].endSec).toBe(31);
  });

  it('returns [] for invalid input', () => {
    expect(scheduleBatchTiming(0, 2)).toEqual([]);
    expect(scheduleBatchTiming(30, 0)).toEqual([]);
    expect(scheduleBatchTiming(NaN, 2)).toEqual([]);
  });
});

describe('packAtTime', () => {
  const s = scheduleBatchTiming(30, 2);
  it('returns batch index at t', () => {
    expect(packAtTime(s, 5)).toBe(0);
    expect(packAtTime(s, 15)).toBe(1);
    expect(packAtTime(s, 30)).toBeNull();
  });

  it('empty schedule → null', () => {
    expect(packAtTime([], 5)).toBeNull();
  });
});

describe('computeFontShrink', () => {
  it('returns base when fits', () => {
    expect(computeFontShrink('短', 22, 16, 14)).toBe(22);
  });

  it('shrinks toward min when too long', () => {
    const r = computeFontShrink('x'.repeat(40), 22, 16, 14);
    expect(r).toBeLessThan(22);
    expect(r).toBeGreaterThanOrEqual(14);
  });

  it('never exceeds base or drops below min', () => {
    const r = computeFontShrink('abc', 22, 100, 14);
    expect(r).toBe(22);
  });
});

describe('renderPlanSubtitle (style + batched)', () => {
  beforeEach(() => {
    document.getElementById('plan-subtitle')?.remove();
    state.configProject = null;
    state.currentEntity = 'video';
    state.plan = null;
    state.videos = [];
    state.previewIndex = -1;
    state.previewGlobalSec = 0;
  });

  function mount() {
    const el = document.createElement('div');
    el.id = 'plan-subtitle'; el.hidden = true;
    el.innerHTML = '<span class="plan-subtitle-handle"></span><span class="plan-subtitle-text"></span>';
    document.body.appendChild(el);
    return el;
  }

  const ctx = {
    entity: 'plan', previewIndex: 0,
    plan: { sequence: [{ index: '001', use_timeline: '00:00-00:30' }] },
    videos: [{ index: '001', script_json: 'vy.json' }],
    previewGlobalSec: 5,
    config: { preview: { subtitles: { mode: 'auto', font_size: 18 } } },
  };

  it('applies style from config and writes into .plan-subtitle-text', async () => {
    const el = mount();
    await renderPlanSubtitle({ ctx, textFor: async () => '一行字幕。' });
    expect(el.style.getPropertyValue('--st-font-size')).toBe('18px');
    expect(el.querySelector('.plan-subtitle-text').textContent).toContain('一行字幕');
    expect(el.hidden).toBe(false);
  });

  it('multi mode renders multiple lines', async () => {
    const el = mount();
    await renderPlanSubtitle({
      ctx: { ...ctx, config: { preview: { subtitles: { mode: 'multi', max_lines: 2 } } } },
      textFor: async () => '第一句。第二句。',
    });
    const text = el.querySelector('.plan-subtitle-text').textContent;
    expect(text).toContain('第一句。');
    expect(text).toContain('第二句。');
  });

  it('disabled subtitles hide layer', async () => {
    const el = mount();
    await renderPlanSubtitle({
      ctx: { ...ctx, config: { preview: { subtitles: { enabled: false, mode: 'auto' } } } },
      textFor: async () => 'x',
    });
    expect(el.hidden).toBe(true);
  });

  it('scroll mode sets scroll speed and marquee vars', async () => {
    const el = mount();
    await renderPlanSubtitle({
      ctx: { ...ctx, config: { preview: { subtitles: { mode: 'scroll', scroll_speed: 60, font_size: 18 } } } },
      textFor: async () => '一很长段。二很长段。三。',
    });
    expect(el.dataset.mode).toBe('scroll');
    expect(el.style.getPropertyValue('--st-scroll-speed')).toBe('60px/s');
    expect(el.style.getPropertyValue('--st-scroll-duration')).toMatch(/s$/);
    // 滚动必须保留整段文本，而不是逐句小字
    expect(el.querySelector('.plan-subtitle-text').textContent).toContain('一很长段');
    expect(el.querySelector('.plan-subtitle-text').textContent).toContain('三');
  });

  it('auto mode does not set scroll vars', async () => {
    const el = mount();
    await renderPlanSubtitle({
      ctx: { ...ctx, config: { preview: { subtitles: { mode: 'auto' } } } },
      textFor: async () => '一行字幕。',
    });
    expect(el.dataset.mode).toBe('auto');
    expect(el.style.getPropertyValue('--st-scroll-speed')).toBe('');
  });
});

describe('renderPlanSubtitle (production config source)', () => {
  const origState = state;

  beforeEach(() => {
    document.getElementById('plan-subtitle')?.remove();
  });

  it('reads subtitle settings from state.configProject when no ctx given', async () => {
    const el = document.createElement('div');
    el.id = 'plan-subtitle'; el.hidden = true;
    el.innerHTML = '<span class="plan-subtitle-handle"></span><span class="plan-subtitle-text"></span>';
    document.body.appendChild(el);

    origState.currentEntity = 'plan';
    origState.previewIndex = 0;
    origState.previewGlobalSec = 5;
    origState.plan = { sequence: [{ index: '001', use_timeline: '00:00-00:30' }] };
    origState.videos = [{ index: '001', script_json: 'vy.json' }];
    // Production wiring: settings live under configProject (merged project config).
    origState.configProject = { preview: { subtitles: { font_size: 18, pos_x: 30 } } };

    await renderPlanSubtitle({ textFor: async () => '一行。' });
    expect(el.style.getPropertyValue('--st-font-size')).toBe('18px');
    expect(el.style.getPropertyValue('--st-pos-x')).toBe('30%');
  });
});
