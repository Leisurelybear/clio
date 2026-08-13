import { describe, it, expect } from 'vitest';
import { LOGS_BUFFER_MAX, appendLogEntries } from '../logs-buffer.js';

describe('appendLogEntries', () => {
  it('appends without trimming under capacity', () => {
    const buf = [{ text: 'a' }];
    appendLogEntries(buf, [{ text: 'b' }, { text: 'c' }], 10);
    expect(buf).toEqual([{ text: 'a' }, { text: 'b' }, { text: 'c' }]);
  });

  it('drops oldest when exceeding max', () => {
    const buf = [{ text: '1' }, { text: '2' }, { text: '3' }];
    appendLogEntries(buf, [{ text: '4' }, { text: '5' }], 3);
    expect(buf).toEqual([{ text: '3' }, { text: '4' }, { text: '5' }]);
  });

  it('uses LOGS_BUFFER_MAX by default', () => {
    expect(LOGS_BUFFER_MAX).toBe(2000);
    const buf = Array.from({ length: 1999 }, (_, i) => ({ text: String(i) }));
    appendLogEntries(buf, [{ text: 'x' }, { text: 'y' }]);
    expect(buf).toHaveLength(2000);
    expect(buf[0].text).toBe('1');
    expect(buf[buf.length - 1].text).toBe('y');
  });

  it('no-ops on empty entries', () => {
    const buf = [{ text: 'a' }];
    appendLogEntries(buf, []);
    expect(buf).toEqual([{ text: 'a' }]);
  });
});
