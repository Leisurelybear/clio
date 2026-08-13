import { describe, it, expect, beforeEach } from 'vitest';
import {
  clampPositionPct,
  initSubtitleDrag,
} from '../plan-subtitle.js';

describe('clampPositionPct', () => {
  it('keeps in-range values', () => {
    expect(clampPositionPct(30, 60)).toEqual([30, 60]);
  });

  it('clamps below 0 / above 100', () => {
    expect(clampPositionPct(-5, 120)).toEqual([0, 100]);
  });

  it('non-finite falls back to defaults', () => {
    expect(clampPositionPct(NaN, undefined)).toEqual([50, 8]);
  });
});

describe('initSubtitleDrag', () => {
  beforeEach(() => { document.getElementById('plan-subtitle')?.remove(); });

  function mount() {
    const el = document.createElement('div');
    el.id = 'plan-subtitle'; el.hidden = true;
    el.innerHTML = '<span class="plan-subtitle-handle"></span><span class="plan-subtitle-text"></span>';
    document.body.appendChild(el);
    return el;
  }

  it('drag handle updates position CSS vars and calls onCommit', () => {
    const el = mount();
    const commits = [];
    const handle = el.querySelector('.plan-subtitle-handle');
    const stage = document.createElement('div');
    stage.getBoundingClientRect = () => ({ width: 200, height: 100, left: 0, top: 0, bottom: 100 });
    initSubtitleDrag({ handle, stage, onCommit: (p) => commits.push(p) });

    handle.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 100, clientY: 50 }));
    document.dispatchEvent(new MouseEvent('pointermove', { clientX: 140, clientY: 70 }));
    document.dispatchEvent(new MouseEvent('pointerup'));
    expect(el.style.getPropertyValue('--st-pos-x')).toBe('70%');
    // pos_y is bottom-offset: bottom=100, clientY=70 -> (100-70)/100*100=30
    expect(el.style.getPropertyValue('--st-pos-y')).toBe('30%');
    expect(commits.length).toBe(1);
    expect(commits[0]).toEqual({ x: 70, y: 30 });
  });

  it('release without move commits defaults (no NaN)', () => {
    const el = mount();
    const commits = [];
    const handle = el.querySelector('.plan-subtitle-handle');
    const stage = document.createElement('div');
    stage.getBoundingClientRect = () => ({ width: 200, height: 100, left: 0, top: 0, bottom: 100 });
    initSubtitleDrag({ handle, stage, onCommit: (p) => commits.push(p) });

    handle.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
    document.dispatchEvent(new MouseEvent('pointerup'));
    expect(commits.length).toBe(1);
    expect(Number.isFinite(commits[0].x)).toBe(true);
    expect(Number.isFinite(commits[0].y)).toBe(true);
  });

  it('pointercancel restores start position without committing', () => {
    const el = mount();
    el.dataset.posX = '20';
    el.dataset.posY = '10';
    el.style.setProperty('--st-pos-x', '20%');
    el.style.setProperty('--st-pos-y', '10%');
    const commits = [];
    const handle = el.querySelector('.plan-subtitle-handle');
    const stage = document.createElement('div');
    stage.getBoundingClientRect = () => ({ width: 200, height: 100, left: 0, top: 0, bottom: 100 });
    initSubtitleDrag({ handle, stage, onCommit: (p) => commits.push(p) });

    handle.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 40, clientY: 90 }));
    document.dispatchEvent(new MouseEvent('pointermove', { clientX: 150, clientY: 20 }));
    document.dispatchEvent(new MouseEvent('pointercancel'));
    expect(commits.length).toBe(0);
    expect(el.style.getPropertyValue('--st-pos-x')).toBe('20%');
    expect(el.style.getPropertyValue('--st-pos-y')).toBe('10%');
  });

  it('arrow keys nudge position and commit', () => {
    const el = mount();
    el.dataset.posX = '50';
    el.dataset.posY = '8';
    const commits = [];
    const handle = el.querySelector('.plan-subtitle-handle');
    const stage = document.createElement('div');
    initSubtitleDrag({ handle, stage, onCommit: (p) => commits.push(p) });

    handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(el.style.getPropertyValue('--st-pos-x')).toBe('51%');
    expect(commits.at(-1)).toEqual({ x: 51, y: 8 });
  });
});
