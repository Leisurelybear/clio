import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { state } from '../state.js';
import {
  escapeHtml,
  fmtTime,
  parseTimecode,
  getDeep,
  setDeep,
  markDirty,
  clearDirty,
} from '../utils.js';

describe('escapeHtml', () => {
  it('escapes & < > " \'', () => {
    expect(escapeHtml('&<>"\'')).toBe('&amp;&lt;&gt;&quot;&#39;');
  });

  it('passes through safe strings', () => {
    expect(escapeHtml('hello world')).toBe('hello world');
  });

  it('handles null and undefined', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
  });

  it('coerces numbers to strings', () => {
    expect(escapeHtml(42)).toBe('42');
  });
});

describe('fmtTime', () => {
  it('formats 0 as 00:00', () => {
    expect(fmtTime(0)).toBe('00:00');
  });

  it('formats 60 as 01:00', () => {
    expect(fmtTime(60)).toBe('01:00');
  });

  it('formats 90.5 as 01:30', () => {
    expect(fmtTime(90.5)).toBe('01:30');
  });

  it('formats 3661 as 61:01', () => {
    expect(fmtTime(3661)).toBe('61:01');
  });

  it('returns 00:00 for non-finite values', () => {
    expect(fmtTime(Infinity)).toBe('00:00');
    expect(fmtTime(NaN)).toBe('00:00');
  });

  it('handles negative values', () => {
    expect(fmtTime(-1)).toBe('-1:-1');
  });
});

describe('parseTimecode', () => {
  it('parses MM:SS', () => {
    expect(parseTimecode('01:30')).toBe(90);
  });

  it('parses HH:MM:SS', () => {
    expect(parseTimecode('01:30:00')).toBe(5400);
  });

  it('parses plain number string', () => {
    expect(parseTimecode('45.5')).toBe(45.5);
  });

  it('keeps fractional seconds (0.12 must not become 12)', () => {
    expect(parseTimecode('0.12')).toBe(0.12);
    expect(parseTimecode('12.5')).toBe(12.5);
  });

  it('rejects invalid MM:SS ranges', () => {
    expect(Number.isNaN(parseTimecode('01:60'))).toBe(true);
    expect(Number.isNaN(parseTimecode('1:2:70'))).toBe(true);
    expect(Number.isNaN(parseTimecode('1:2:3:4'))).toBe(true);
  });

  it('returns 0 for empty string', () => {
    expect(parseTimecode('')).toBe(0);
  });

  it('returns 0 for null/undefined', () => {
    expect(parseTimecode(null)).toBe(0);
    expect(parseTimecode(undefined)).toBe(0);
  });

  it('returns NaN for unparseable string', () => {
    expect(Number.isNaN(parseTimecode('abc'))).toBe(true);
  });
});

describe('getDeep', () => {
  const obj = { a: { b: { c: 42 } }, x: [1, 2] };

  it('gets nested value by dot path', () => {
    expect(getDeep(obj, 'a.b.c')).toBe(42);
  });

  it('returns undefined for missing path', () => {
    expect(getDeep(obj, 'a.b.z')).toBe(undefined);
  });

  it('handles null/undefined root', () => {
    expect(getDeep(null, 'a.b')).toBe(undefined);
    expect(getDeep(undefined, 'a.b')).toBe(undefined);
  });

  it('accesses array indices', () => {
    expect(getDeep(obj, 'x.0')).toBe(1);
    expect(getDeep(obj, 'x.1')).toBe(2);
  });
});

describe('setDeep', () => {
  it('sets nested value by dot path', () => {
    const obj = {};
    setDeep(obj, 'a.b.c', 42);
    expect(obj.a.b.c).toBe(42);
  });

  it('overwrites existing values', () => {
    const obj = { a: { b: 1 } };
    setDeep(obj, 'a.b', 2);
    expect(obj.a.b).toBe(2);
  });

  it('creates intermediate objects', () => {
    const obj = {};
    setDeep(obj, 'x.y.z', 'deep');
    expect(obj.x.y.z).toBe('deep');
  });
});

describe('markDirty / clearDirty', () => {
  let btn;
  let prevDirty;

  beforeEach(() => {
    prevDirty = state.dirty;
    btn = document.createElement('button');
    btn.id = 'btn-save';
    btn.textContent = '保存';
    document.body.appendChild(btn);
  });

  afterEach(() => {
    state.dirty = prevDirty;
    btn.remove();
  });

  it('markDirty sets dirty and labels Save as changed', () => {
    clearDirty();
    markDirty();
    expect(state.dirty).toBe(true);
    expect(btn.textContent).toBe('保存 (有改动)');
    expect(btn.classList.contains('dirty')).toBe(true);
  });

  it('clearDirty resets dirty and Save label (discard / leave page)', () => {
    markDirty();
    clearDirty();
    expect(state.dirty).toBe(false);
    expect(btn.textContent).toBe('保存');
    expect(btn.classList.contains('dirty')).toBe(false);
  });
});
