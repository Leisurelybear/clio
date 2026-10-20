const LOCAL_ENGINE = 'local';
const LOCAL_SETTINGS_LABEL = '设置 → Whisper 模型管理';
const CLOUD_SETTINGS_LABEL = '设置 → 全局 → 编辑 .env 文件';

export function isLocalTranscription(check) {
  if (!check || typeof check !== 'object') return true;
  if (check.requires_local_model === false) return false;
  if (check.requires_local_model === true) return true;
  const engine = typeof check.engine === 'string' ? check.engine.trim().toLowerCase() : '';
  return !engine || engine === LOCAL_ENGINE;
}

export function transcriptionEngineLabel(check) {
  const displayName = typeof check?.engine_display_name === 'string'
    ? check.engine_display_name.trim()
    : '';
  if (displayName) return displayName;
  return isLocalTranscription(check) ? '本地 faster-whisper' : '云端 ASR';
}

export function transcriptionFailureNotice(check) {
  const label = transcriptionEngineLabel(check);
  const error = typeof check?.readiness_error === 'string'
    ? check.readiness_error.trim()
    : '';
  const detail = error ? ` 就绪检查：${error}` : '';
  if (isLocalTranscription(check)) {
    return {
      title: '部分视频转录失败',
      message: `当前使用${label}，转录失败。请确认 faster-whisper 依赖与模型文件状态。${detail}`,
      settingsLabel: LOCAL_SETTINGS_LABEL,
    };
  }
  return {
    title: '部分视频转录失败',
    message: `当前使用${label}，转录失败。请检查 API Key、网络与云端服务状态。${detail}`,
    settingsLabel: CLOUD_SETTINGS_LABEL,
  };
}

export function transcriptionReadinessPrompt(check) {
  if (!check || typeof check !== 'object') return null;
  const label = transcriptionEngineLabel(check);

  if (!isLocalTranscription(check)) {
    if (check.ready === true || (check.ready === undefined && !check.readiness_error)) return null;
    const error = typeof check.readiness_error === 'string' && check.readiness_error.trim()
      ? check.readiness_error.trim()
      : `${label} 凭据尚未配置。`;
    return {
      title: `${label} 尚未就绪`,
      before: error,
      settingsLabel: CLOUD_SETTINGS_LABEL,
      after: ' 配置对应 API Key；云端引擎无需下载本地模型。',
    };
  }

  if (check.model_cached === true) return null;
  if (check.frozen === true) {
    return {
      title: '打包版不内置本地 Whisper 转录',
      before: 'faster-whisper（约 4 GB）未打包进 clio.exe。如需本地转录，请改用源码版运行 python main.py whisper install。',
      settingsLabel: null,
      after: '',
    };
  }
  return {
    title: '本地 Whisper 模型未下载',
    before: `需要下载 ${check.installed ? '模型文件（约 1-2 GB）' : 'faster-whisper 依赖及模型文件'}，请前往 `,
    settingsLabel: LOCAL_SETTINGS_LABEL,
    after: ' 手动下载。',
  };
}

export function transcriptionModelManagementNotice(check) {
  if (!check || isLocalTranscription(check)) return '';
  return `当前使用 ${transcriptionEngineLabel(check)} 云端引擎，无需下载本地模型；以下设置仅影响本地 Whisper。`;
}
