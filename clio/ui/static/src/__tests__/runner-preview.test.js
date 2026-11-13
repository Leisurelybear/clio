import { describe, it, expect } from 'vitest';
import {
  formatRunStartError,
  needsArtifactOverwriteConfirmation,
  needsLegacyAudioRemovalConfirmation,
  needsPlanOverwriteConfirmation,
  renderRunPreviewHtml,
} from '../runner-preview.js';

describe('renderRunPreviewHtml', () => {
  it('renders totals and per-step counts', () => {
    const html = renderRunPreviewHtml({
      input: { mode: 'directory', path: 'D:/trip/videos', count: 3 },
      totals: { selected_steps: 2, will_run: 4, will_skip: 1, warnings: 0 },
      steps: [
        { name: 'compress', label: '压缩视频', total: 3, will_run: 2, will_skip: 1, warnings: [] },
        { name: 'analyze', label: 'AI 分析', total: 2, will_run: 2, will_skip: 0, warnings: [] },
      ],
    });

    expect(html).toContain('运行预览');
    expect(html).toContain('D:/trip/videos');
    expect(html).toContain('压缩视频');
    expect(html).toContain('AI 分析');
    expect(html).toContain('待执行');
    expect(html).toContain('4');
    expect(html).toContain('跳过');
    expect(html).toContain('1');
  });

  it('renders warnings and escapes dynamic strings', () => {
    const html = renderRunPreviewHtml({
      input: { mode: 'directory', path: '<script>', count: 0 },
      totals: { selected_steps: 1, will_run: 0, will_skip: 0, warnings: 1 },
      steps: [
        {
          name: 'unknown',
          label: '<b>bad</b>',
          total: 0,
          will_run: 0,
          will_skip: 0,
          warnings: ['未知步骤：<x>'],
        },
      ],
    });

    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;b&gt;bad&lt;/b&gt;');
    expect(html).toContain('未知步骤：&lt;x&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<b>bad</b>');
  });

  it('renders a neutral state without preview data', () => {
    expect(renderRunPreviewHtml(null)).toContain('选择步骤后显示预览');
  });

  it('renders backend media preflight failures before the run can start', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 1 },
      totals: { selected_steps: 1, will_run: 1, will_skip: 0, warnings: 0 },
      steps: [],
      preflight: {
        ok: false,
        required: ['ffmpeg', 'ffprobe'],
        missing: ['ffmpeg'],
        detail: '未找到 ffmpeg，请先安装',
      },
    });

    expect(html).toContain('媒体依赖未就绪');
    expect(html).toContain('未找到 ffmpeg，请先安装');
    expect(html).toContain('缺少 ffmpeg');
  });

  it('renders the edited-artifact overwrite warning with escaped names', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 1 },
      totals: { selected_steps: 1, will_run: 1, will_skip: 0, warnings: 0 },
      steps: [],
      artifact_overwrite: { required: true, items: ['<a>.json', 'b.md'] },
    });

    expect(html).toContain('run-artifact-overwrite-warning');
    expect(html).toContain('已人工编辑的文件');
    expect(html).toContain('&lt;a&gt;.json');
    expect(html).not.toContain('<a>.json');
  });

  it('renders the partial-plan overwrite warning with an escaped day label', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 1 },
      totals: { selected_steps: 1, will_run: 1, will_skip: 0, warnings: 0 },
      steps: [],
      plan_overwrite: { required: true, day_label: '<day2>' },
    });

    expect(html).toContain('会替换 &lt;day2&gt; 的现有计划');
    expect(html).not.toContain('<day2>');
  });

  it('renders the legacy audio-removal warning with all rerun counts', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 2 },
      totals: { selected_steps: 2, will_run: 4, will_skip: 0, warnings: 0 },
      steps: [],
      legacy_audio_removal: {
        required: true,
        analyze_count: 2,
        voiceover_count: 1,
        compress_count: 3,
      },
    });

    expect(html).toContain('run-legacy-audio-removal-warning');
    expect(html).toContain('旧配置曾保留音频');
    expect(html).toContain('分析 2 项');
    expect(html).toContain('口播 1 项');
    expect(html).toContain('压缩 3 项');
  });

  it('does not render the legacy audio-removal warning when confirmation is not required', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 1 },
      totals: { selected_steps: 1, will_run: 1, will_skip: 0, warnings: 0 },
      steps: [],
      legacy_audio_removal: { required: false, analyze_count: 2 },
    });

    expect(html).not.toContain('run-legacy-audio-removal-warning');
  });

  it('coerces legacy audio-removal counts to non-executable text', () => {
    const html = renderRunPreviewHtml({
      input: { path: 'D:/trip/videos', count: 1 },
      totals: { selected_steps: 1, will_run: 1, will_skip: 0, warnings: 0 },
      steps: [],
      legacy_audio_removal: {
        required: true,
        analyze_count: '<img src=x>',
        voiceover_count: 0,
        compress_count: 0,
      },
    });

    expect(html).not.toContain('<img src=x>');
    expect(html).toContain('分析 NaN 项');
  });
});

describe('needsPlanOverwriteConfirmation', () => {
  const riskyPreview = { plan_overwrite: { required: true, day_label: 'day2' } };

  it('requires confirmation only for selected files plus plan', () => {
    expect(needsPlanOverwriteConfirmation(riskyPreview, {
      steps: ['plan'],
      files: ['A.mp4'],
    })).toBe(true);
  });

  it('does not require confirmation for full-plan or non-plan runs', () => {
    expect(needsPlanOverwriteConfirmation(riskyPreview, { steps: ['plan'] })).toBe(false);
    expect(needsPlanOverwriteConfirmation(riskyPreview, {
      steps: ['analyze'],
      files: ['A.mp4'],
    })).toBe(false);
    expect(needsPlanOverwriteConfirmation({}, { steps: ['plan'], files: ['A.mp4'] })).toBe(false);
  });
});

describe('needsArtifactOverwriteConfirmation', () => {
  const riskyPreview = { artifact_overwrite: { required: true, items: ['day1_plan.json'] } };

  it('requires confirmation for plan, voiceover, or plan voiceover when artifacts are at risk', () => {
    expect(needsArtifactOverwriteConfirmation(riskyPreview, { steps: ['plan'] })).toBe(true);
    expect(needsArtifactOverwriteConfirmation(riskyPreview, { steps: ['voiceover'] })).toBe(true);
    expect(needsArtifactOverwriteConfirmation(riskyPreview, { steps: ['plan_voiceover'] })).toBe(true);
  });

  it('does not require confirmation for other steps or risk-free previews', () => {
    expect(needsArtifactOverwriteConfirmation(riskyPreview, { steps: ['analyze'] })).toBe(false);
    expect(needsArtifactOverwriteConfirmation({}, { steps: ['plan'] })).toBe(false);
    expect(needsArtifactOverwriteConfirmation(null, { steps: ['voiceover'] })).toBe(false);
  });
});

describe('needsLegacyAudioRemovalConfirmation', () => {
  const riskyPreview = {
    legacy_audio_removal: { required: true, analyze_count: 2, voiceover_count: 1, compress_count: 0 },
  };

  it('requires confirmation when the preview reports legacy audio removal', () => {
    expect(needsLegacyAudioRemovalConfirmation(riskyPreview, { steps: ['analyze'] })).toBe(true);
    expect(needsLegacyAudioRemovalConfirmation(riskyPreview, { steps: ['compress'] })).toBe(true);
  });

  it('does not require confirmation without selected steps or a risk flag', () => {
    expect(needsLegacyAudioRemovalConfirmation(riskyPreview, {})).toBe(false);
    expect(needsLegacyAudioRemovalConfirmation(
      { legacy_audio_removal: { required: false } },
      { steps: ['analyze'] },
    )).toBe(false);
    expect(needsLegacyAudioRemovalConfirmation(null, { steps: ['analyze'] })).toBe(false);
  });
});

describe('formatRunStartError', () => {
  it('prefers structured backend preflight details', () => {
    expect(formatRunStartError({
      status: 424,
      message: 'HTTP 424: media dependency missing',
      body: {
        code: 'media_dependency_missing',
        error: '媒体依赖不可用',
        preflight: { detail: '未找到 ffprobe', missing: ['ffprobe'] },
      },
    })).toBe('未找到 ffprobe（缺少 ffprobe）');
  });

  it('keeps generic API errors readable', () => {
    expect(formatRunStartError({ message: 'HTTP 500: server error' })).toBe('HTTP 500: server error');
  });
});