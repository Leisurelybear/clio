import { describe, expect, it } from 'vitest';
import en from '../../locales/en.json';
import zhCN from '../../locales/zh-CN.json';
import { descFromPath, FIELD_LABELS, optionLabel, taskDescription, taskLabel } from '../editor-config.js';
import { setLocale } from '../i18n.js';

describe('shipped catalog coverage', () => {
  it('ships the same key set for every locale', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zhCN).sort());
  });

  it('mirrors every Chinese config field label', () => {
    for (const [path, label] of Object.entries(FIELD_LABELS)) {
      const key = `settings.field.${path}`;
      expect(zhCN[key], key).toBe(label);
      expect(en[key], key).toBeTruthy();
    }
  });
});

describe('settings localization helpers', () => {
  const descs = { 'compress.target_size_mb': '压缩后目标文件大小（MB）' };

  it('localizes select options and falls back to the raw value', () => {
    setLocale('zh-CN');
    expect(optionLabel('ui.language', 'en')).toBe('English');
    expect(optionLabel('ui.language', 'zh-CN')).toBe('中文');
    expect(optionLabel('whisper.language', 'zh')).toBe('zh');
  });

  it('follows the active locale for tooltips and task copy', () => {
    setLocale('zh-CN');
    expect(descFromPath('compress.target_size_mb', descs)).toBe('压缩后目标文件大小（MB）');
    expect(taskLabel('video_analyze')).toBe('视频分析');

    setLocale('en');
    expect(descFromPath('compress.target_size_mb', descs)).toBe('Target file size after compression (MB)');
    expect(taskLabel('video_analyze')).toBe('Video analysis');
    expect(taskDescription('video_analyze')).toBeTruthy();

    setLocale('zh-CN');
  });

  it('keeps the backend Chinese description when the catalog has no key', () => {
    setLocale('zh-CN');
    expect(descFromPath('legacy.path', { 'legacy.path': '旧描述' })).toBe('旧描述');
    expect(descFromPath('legacy.path', {})).toBe('');
  });
});
