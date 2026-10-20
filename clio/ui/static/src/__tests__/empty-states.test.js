import { describe, it, expect } from 'vitest';
import { renderEmptyArtifactHtml } from '../empty-states.js';

describe('renderEmptyArtifactHtml', () => {
  it('renders texts empty state with run and rerun CTAs', () => {
    const html = renderEmptyArtifactHtml('texts');
    expect(html).toContain('没有分析结果');
    expect(html).toContain('data-empty-action="run"');
    expect(html).toContain('data-empty-action="rerun-analyze"');
    expect(html).toContain('去运行流水线');
    expect(html).toContain('重跑 AI 分析');
  });

  it('renders voiceover empty state with run and rerun CTAs when analysis exists', () => {
    const html = renderEmptyArtifactHtml('voiceover', { hasTexts: true });
    expect(html).toContain('没有口播文案');
    expect(html).toContain('data-empty-action="run"');
    expect(html).toContain('data-empty-action="rerun-voiceover"');
    expect(html).toContain('去运行流水线');
    expect(html).toContain('重跑口播');
  });

  it('voiceover without texts only offers analyze path', () => {
    const html = renderEmptyArtifactHtml('voiceover', { hasTexts: false });
    expect(html).toContain('需要先完成 AI 分析');
    expect(html).toContain('data-empty-action="rerun-analyze"');
    expect(html).not.toContain('data-empty-action="rerun-voiceover"');
  });

  it('no-video placeholder for all artifact tabs', () => {
    const html = renderEmptyArtifactHtml('texts', { hasVideo: false });
    expect(html).toContain('请先选择左侧视频');
    expect(html).not.toContain('data-empty-action="rerun-analyze"');
  });

  it('transcript empty state with CTAs', () => {
    const html = renderEmptyArtifactHtml('transcript', { hasVideo: true });
    expect(html).toContain('没有转录');
    expect(html).toContain('语音转录');
    expect(html).not.toContain('Whisper');
    expect(html).toContain('data-empty-action="rerun-transcribe"');
  });

  it('escapes nothing dangerous for known kinds and rejects unknown', () => {
    expect(renderEmptyArtifactHtml('nope')).toContain('未知面板');
  });
});
