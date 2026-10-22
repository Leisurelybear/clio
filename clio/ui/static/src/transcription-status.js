import { t } from './i18n.js';

const LOCAL_ENGINE = 'local';

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
  return isLocalTranscription(check) ? t('transcription.engine_local') : t('transcription.engine_cloud');
}

export function transcriptionFailureNotice(check) {
  const label = transcriptionEngineLabel(check);
  const error = typeof check?.readiness_error === 'string'
    ? check.readiness_error.trim()
    : '';
  const detail = error ? t('transcription.readiness_detail', { detail: error }) : '';
  if (isLocalTranscription(check)) {
    return {
      title: t('transcription.failed_title'),
      message: t('transcription.failed_local', { engine: label, detail }),
      settingsLabel: t('transcription.settings_local'),
    };
  }
  return {
    title: t('transcription.failed_title'),
    message: t('transcription.failed_cloud', { engine: label, detail }),
    settingsLabel: t('transcription.settings_cloud'),
  };
}

export function transcriptionReadinessPrompt(check) {
  if (!check || typeof check !== 'object') return null;
  const label = transcriptionEngineLabel(check);

  if (!isLocalTranscription(check)) {
    if (check.ready === true || (check.ready === undefined && !check.readiness_error)) return null;
    const error = typeof check.readiness_error === 'string' && check.readiness_error.trim()
      ? check.readiness_error.trim()
      : t('transcription.cloud_missing_creds', { engine: label });
    return {
      title: t('transcription.not_ready_title', { engine: label }),
      before: error,
      settingsLabel: t('transcription.settings_cloud'),
      after: t('transcription.cloud_after'),
    };
  }

  if (check.model_cached === true) return null;
  if (check.frozen === true) {
    return {
      title: t('transcription.frozen_title'),
      before: t('transcription.frozen_before'),
      settingsLabel: null,
      after: '',
    };
  }
  return {
    title: t('transcription.model_missing_title'),
    before: t('transcription.model_missing_before', {
      target: check.installed
        ? t('transcription.model_target_files')
        : t('transcription.model_target_deps'),
    }),
    settingsLabel: t('transcription.settings_local'),
    after: t('transcription.model_missing_after'),
  };
}

export function transcriptionModelManagementNotice(check) {
  if (!check || isLocalTranscription(check)) return '';
  return t('transcription.model_notice', { engine: transcriptionEngineLabel(check) });
}
