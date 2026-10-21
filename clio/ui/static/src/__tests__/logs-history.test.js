import { describe, expect, it } from 'vitest';
import {
  formatLogFileSize,
  formatLogHour,
  historyStatusText,
  logFileLabel,
  sortLogFiles,
} from '../logs-history.js';

describe('formatLogHour', () => {
  it('formats hourly names without timezone conversion', () => {
    expect(formatLogHour('2026-07-20-22.log')).toBe('2026-07-20 22:00');
  });

  it('rejects malformed or out-of-range names', () => {
    expect(formatLogHour('notes.log')).toBe('notes.log');
    expect(formatLogHour('2026-13-20-22.log')).toBe('2026-13-20-22.log');
    expect(formatLogHour('2026-07-20-24.log')).toBe('2026-07-20-24.log');
  });
});

describe('log file labels', () => {
  it('formats sizes and combines them with the hour', () => {
    expect(formatLogFileSize(512)).toBe('512 B');
    expect(formatLogFileSize(2048)).toBe('2.0 KB');
    expect(formatLogFileSize(2 * 1024 * 1024)).toBe('2.0 MB');
    expect(formatLogFileSize(-1)).toBe('');
    expect(logFileLabel({ name: '2026-07-20-22.log', size: 2048 })).toBe(
      '2026-07-20 22:00 · 2.0 KB',
    );
  });

  it('sorts newest-first and ignores malformed entries', () => {
    const files = sortLogFiles([
      { name: '2026-07-20-21.log' },
      null,
      { name: '2026-07-20-22.log' },
      { name: 42 },
    ]);
    expect(files.map((file) => file.name)).toEqual([
      '2026-07-20-22.log',
      '2026-07-20-21.log',
    ]);
  });
});

describe('historyStatusText', () => {
  it('reports entry count and truncation', () => {
    expect(historyStatusText({
      name: '2026-07-20-22.log',
      logs: [{}, {}, {}],
      truncated: false,
    })).toBe('2026-07-20 22:00 · 3 行');
    expect(historyStatusText({
      name: '2026-07-20-22.log',
      logs: [],
      truncated: true,
    })).toBe('2026-07-20 22:00 · 0 行 · 仅显示文件末尾');
  });
});
