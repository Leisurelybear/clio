import { describe, it, expect } from 'vitest';
import {
  isLocalTranscription,
  transcriptionEngineLabel,
  transcriptionFailureNotice,
  transcriptionModelManagementNotice,
  transcriptionReadinessPrompt,
} from '../transcription-status.js';

const CLOUD_CHECK = {
  engine: 'aliyun',
  engine_display_name: '阿里云 Paraformer',
  ready: true,
  readiness_error: null,
  requires_local_model: false,
  installed: false,
  frozen: false,
  model_cached: false,
};

describe('transcription engine detection', () => {
  it('treats legacy and incomplete responses as local', () => {
    expect(isLocalTranscription(null)).toBe(true);
    expect(isLocalTranscription({ installed: true })).toBe(true);
    expect(isLocalTranscription({ engine: 'local' })).toBe(true);
  });

  it('detects cloud engines from explicit fields', () => {
    expect(isLocalTranscription(CLOUD_CHECK)).toBe(false);
    expect(isLocalTranscription({ engine: 'aliyun' })).toBe(false);
  });

  it('uses the display name with sane fallbacks', () => {
    expect(transcriptionEngineLabel(CLOUD_CHECK)).toBe('阿里云 Paraformer');
    expect(transcriptionEngineLabel({ engine: 'local' })).toBe('本地 faster-whisper');
    expect(transcriptionEngineLabel({ engine: 'other' })).toBe('云端 ASR');
  });
});

describe('transcriptionReadinessPrompt', () => {
  it('does not show a local model prompt for a ready cloud engine', () => {
    expect(transcriptionReadinessPrompt(CLOUD_CHECK)).toBeNull();
  });

  it('shows cloud credential guidance when the cloud engine is not ready', () => {
    const prompt = transcriptionReadinessPrompt({
      ...CLOUD_CHECK,
      ready: false,
      readiness_error: '阿里云 ASR 未配置 DASHSCOPE_API_KEY。',
    });

    expect(prompt.title).toContain('阿里云 Paraformer');
    expect(prompt.before).toContain('DASHSCOPE_API_KEY');
    expect(prompt.settingsLabel).toBe('设置 → 全局 → 编辑 .env 文件');
    expect(prompt.after).toContain('无需下载本地模型');
  });

  it('keeps the local model download prompt for local engines', () => {
    const prompt = transcriptionReadinessPrompt({
      engine: 'local',
      ready: false,
      installed: false,
      frozen: false,
      model_cached: false,
    });

    expect(prompt.title).toBe('本地 Whisper 模型未下载');
    expect(prompt.before).toContain('faster-whisper 依赖及模型文件');
    expect(prompt.settingsLabel).toBe('设置 → Whisper 模型管理');
  });

  it('hides the prompt when the local model is cached', () => {
    expect(transcriptionReadinessPrompt({
      engine: 'local',
      ready: true,
      model_cached: true,
    })).toBeNull();
  });

  it('explains that frozen builds cannot install local models', () => {
    const prompt = transcriptionReadinessPrompt({
      engine: 'local',
      frozen: true,
      model_cached: false,
    });

    expect(prompt.title).toContain('打包版');
    expect(prompt.before).toContain('python main.py whisper install');
    expect(prompt.settingsLabel).toBeNull();
  });
});

describe('transcriptionFailureNotice', () => {
  it('points local failures at model management without claiming a cloud key issue', () => {
    const notice = transcriptionFailureNotice({ engine: 'local' });

    expect(notice.message).toContain('faster-whisper');
    expect(notice.message).not.toContain('API Key');
    expect(notice.settingsLabel).toBe('设置 → Whisper 模型管理');
  });

  it('points cloud failures at credentials and includes readiness details', () => {
    const notice = transcriptionFailureNotice({
      ...CLOUD_CHECK,
      ready: false,
      readiness_error: 'DASHSCOPE_API_KEY 缺失',
    });

    expect(notice.message).toContain('阿里云 Paraformer');
    expect(notice.message).toContain('API Key');
    expect(notice.message).toContain('DASHSCOPE_API_KEY 缺失');
    expect(notice.settingsLabel).toBe('设置 → 全局 → 编辑 .env 文件');
  });
});

describe('transcriptionModelManagementNotice', () => {
  it('clarifies that cloud engines do not need local model downloads', () => {
    expect(transcriptionModelManagementNotice(CLOUD_CHECK)).toContain('无需下载本地模型');
  });

  it('stays hidden for local and legacy responses', () => {
    expect(transcriptionModelManagementNotice({ engine: 'local' })).toBe('');
    expect(transcriptionModelManagementNotice(null)).toBe('');
  });
});
