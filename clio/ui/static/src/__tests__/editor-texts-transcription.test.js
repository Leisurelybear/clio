import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderWhisperInstallPrompt } from '../editor-texts.js';
import { api } from '../api.js';

vi.mock('../api.js', () => ({
  api: vi.fn(),
  icon: vi.fn(() => ''),
  submitToken: vi.fn(),
}));

describe('renderWhisperInstallPrompt', () => {
  let pane;

  beforeEach(() => {
    document.body.innerHTML = '<div id="pane"></div>';
    pane = document.getElementById('pane');
    vi.mocked(api).mockReset();
  });

  it('does not append local installation guidance for a ready cloud engine', async () => {
    vi.mocked(api).mockResolvedValue({
      engine: 'aliyun',
      engine_display_name: '阿里云 Paraformer',
      ready: true,
      readiness_error: null,
      requires_local_model: false,
      model_cached: false,
    });

    await renderWhisperInstallPrompt(pane);

    expect(pane.querySelector('#whisper-install-prompt')).toBeNull();
  });

  it('shows cloud credential guidance instead of a model download prompt', async () => {
    vi.mocked(api).mockResolvedValue({
      engine: 'aliyun',
      engine_display_name: '阿里云 Paraformer',
      ready: false,
      readiness_error: 'DASHSCOPE_API_KEY 缺失',
      requires_local_model: false,
      model_cached: false,
    });

    await renderWhisperInstallPrompt(pane);

    const prompt = pane.querySelector('#whisper-install-prompt');
    expect(prompt.textContent).toContain('DASHSCOPE_API_KEY 缺失');
    expect(prompt.textContent).toContain('编辑 .env 文件');
    expect(prompt.textContent).not.toContain('本地 Whisper 模型未下载');
  });

  it('keeps model download guidance for a legacy local response', async () => {
    vi.mocked(api).mockResolvedValue({
      installed: false,
      frozen: false,
      model_cached: false,
    });

    await renderWhisperInstallPrompt(pane);

    const prompt = pane.querySelector('#whisper-install-prompt');
    expect(prompt.textContent).toContain('本地 Whisper 模型未下载');
    expect(prompt.textContent).toContain('Whisper 模型管理');
  });
});
