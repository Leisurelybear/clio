import { describe, it, expect } from 'vitest';
import { _renderConfigForm, labelFromPath, _renderTooltip } from '../editor.js';

const DESCS = {
  'paths.input_dir': '原始视频所在目录',
  'compress.target_size_mb': '压缩后目标文件大小（MB）',
  'naming.index_width': '文件名中索引编号的位数',
};

describe('labelFromPath', () => {
  it('returns last segment of dot path', () => {
    expect(labelFromPath('paths.input_dir')).toBe('input_dir');
  });

  it('returns "config" for empty path', () => {
    expect(labelFromPath('')).toBe('config');
  });

  it('returns path itself for single segment', () => {
    expect(labelFromPath('compress')).toBe('compress');
  });

  it('handles null/undefined', () => {
    expect(labelFromPath(null)).toBe('config');
    expect(labelFromPath(undefined)).toBe('config');
  });
});

describe('_renderTooltip', () => {
  it('returns empty string when no description', () => {
    expect(_renderTooltip('some.path', '')).toBe('');
    expect(_renderTooltip('some.path', null)).toBe('');
    expect(_renderTooltip('some.path', undefined)).toBe('');
  });

  it('renders tooltip HTML with description text', () => {
    const html = _renderTooltip('paths.input_dir', '原始视频所在目录');
    expect(html).toContain('config-desc-icon');
    expect(html).toContain('config-desc-tooltip');
    expect(html).toContain('原始视频所在目录');
  });

  it('escapes HTML in description', () => {
    const html = _renderTooltip('test', '<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('includes data-desc-path attribute', () => {
    const html = _renderTooltip('paths.input_dir', 'desc');
    expect(html).toContain('data-desc-path="paths.input_dir"');
  });
});

describe('_renderConfigForm - null/undefined', () => {
  it('renders "(空)" for null', () => {
    const html = _renderConfigForm(null, 'test');
    expect(html).toBe('<span class="config-null">(空)</span>');
  });

  it('renders "(空)" for undefined', () => {
    const html = _renderConfigForm(undefined, 'test');
    expect(html).toBe('<span class="config-null">(空)</span>');
  });
});

describe('_renderConfigForm - boolean', () => {
  it('renders checkbox for true', () => {
    const html = _renderConfigForm(true, 'proxy.enabled');
    expect(html).toContain('type="checkbox"');
    expect(html).toContain('checked');
    expect(html).toContain('data-path="proxy.enabled"');
  });

  it('renders unchecked checkbox for false', () => {
    const html = _renderConfigForm(false, 'proxy.enabled');
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain('checked');
  });

  it('includes tooltip icon when description exists', () => {
    const html = _renderConfigForm(true, 'paths.input_dir', DESCS);
    expect(html).toContain('config-desc-icon');
    expect(html).toContain('原始视频所在目录');
  });

  it('no tooltip icon when description missing', () => {
    const html = _renderConfigForm(true, 'some.random');
    expect(html).not.toContain('config-desc-icon');
  });
});

describe('_renderConfigForm - number', () => {
  it('renders number input with int step', () => {
    const html = _renderConfigForm(42, 'naming.index_width');
    expect(html).toContain('type="number"');
    expect(html).toContain('step="1"');
    expect(html).toContain('value="42"');
  });

  it('renders number input with float step', () => {
    const html = _renderConfigForm(5.5, 'compress.target_size_mb');
    expect(html).toContain('step="any"');
    expect(html).toContain('value="5.5"');
  });
});

describe('_renderConfigForm - string', () => {
  it('renders text input for short strings', () => {
    const html = _renderConfigForm('hello', 'proxy.url');
    expect(html).toContain('type="text"');
    expect(html).toContain('value="hello"');
  });

  it('renders password input for api_key paths', () => {
    const html = _renderConfigForm('sk-test', 'ai.providers.gemini.api_key');
    expect(html).toContain('type="password"');
  });

  it('renders textarea for long strings (>80 chars)', () => {
    const long = 'x'.repeat(81);
    const html = _renderConfigForm(long, 'test');
    expect(html).toContain('textarea');
  });

  it('renders textarea for multiline strings', () => {
    const html = _renderConfigForm('line1\nline2', 'test');
    expect(html).toContain('textarea');
  });

  it('renders textarea for ai.context with hint', () => {
    const html = _renderConfigForm('', 'ai.context');
    expect(html).toContain('textarea');
    expect(html).toContain('data-path="ai.context"');
    expect(html).toContain('trip_context.md');
  });

  it('adds folder browse button for paths.output_dir', () => {
    const html = _renderConfigForm('D:/out', 'paths.output_dir');
    expect(html).toContain('browse-btn');
    expect(html).toContain('data-pick-kind="folder"');
    expect(html).toContain('data-target-path="paths.output_dir"');
    expect(html).toContain('input-with-browse');
    expect(html).toContain('value="D:/out"');
  });

  it('adds folder browse button for directory settings', () => {
    expect(_renderConfigForm('', 'paths.logs_dir')).toContain('data-pick-kind="folder"');
    expect(_renderConfigForm('', 'whisper.cache_dir')).toContain('data-pick-kind="folder"');
    expect(_renderConfigForm('', 'export.jianying_draft_dir')).toContain('data-pick-kind="folder"');
  });

  it('adds exe browse button for ffmpeg/ffprobe paths', () => {
    expect(_renderConfigForm('ffmpeg.exe', 'paths.ffmpeg')).toContain('data-pick-kind="exe"');
    expect(_renderConfigForm('ffprobe.exe', 'paths.ffprobe')).toContain('data-pick-kind="exe"');
  });

  it('adds any browse button for template files', () => {
    expect(_renderConfigForm('', 'script.template_file')).toContain('data-pick-kind="any"');
  });

  it('does not add browse button for random string keys or secrets', () => {
    expect(_renderConfigForm('http://x', 'proxy.url')).not.toContain('browse-btn');
    expect(_renderConfigForm('sk-test', 'ai.providers.gemini.api_key')).not.toContain('browse-btn');
    expect(_renderConfigForm('GEMINI_API_KEY', 'ai.providers.gemini.api_key_env')).not.toContain('browse-btn');
  });

  it('keeps password input and hint for api_key paths', () => {
    const html = _renderConfigForm('sk-test', 'ai.providers.gemini.api_key');
    expect(html).toContain('type="password"');
    expect(html).toContain('api_key_env');
  });
});

describe('_renderConfigForm - object', () => {
  it('renders fieldset with nested fields', () => {
    const obj = { input_dir: './videos', output_dir: './output' };
    const html = _renderConfigForm(obj, 'paths');
    expect(html).toContain('<fieldset');
    expect(html).toContain('input_dir');
    expect(html).toContain('output_dir');
    expect(html).toContain('./videos');
    expect(html).toContain('./output');
  });

  it('passes descriptions to nested fields', () => {
    const obj = { input_dir: './videos' };
    const html = _renderConfigForm(obj, 'paths', DESCS);
    expect(html).toContain('config-desc-icon');
    expect(html).toContain('原始视频所在目录');
  });

  it('hides context_file field', () => {
    const obj = { context: 'text', context_file: 'path/to/file' };
    const html = _renderConfigForm(obj, 'ai');
    expect(html).toContain('context');
    expect(html).not.toContain('context_file');
  });
});

describe('_renderConfigForm - array', () => {
  it('renders textarea for string array with hint', () => {
    const arr = ['item1', 'item2'];
    const html = _renderConfigForm(arr, 'test.list');
    expect(html).toContain('textarea');
    expect(html).toContain('item1\nitem2');
    expect(html).toContain('每行一项');
  });

  it('renders array-item divs for mixed array', () => {
    const arr = [{ name: 'a' }, { name: 'b' }];
    const html = _renderConfigForm(arr, 'test.list');
    expect(html).toContain('config-array-item');
    expect(html).toContain('name');
  });
});
