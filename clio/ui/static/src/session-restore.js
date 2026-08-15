const VALID_ENTITIES = new Set(['video', 'plan', 'run', 'config', 'logs', 'tokens', 'tasks']);

/**
 * Build PUT /api/project body for session fields.
 * Omits lastVideo when currentVideo is null/undefined so a transient clear
 * (e.g. setSource mid-switch) does not wipe the stored lastVideo via merge.
 * Pass extra.lastVideo explicitly to force a value (including null).
 */
export function buildProjectSavePayload({
  currentDay,
  source,
  currentEntity,
  currentVideo,
  projectName,
} = {}, extra = {}) {
  const payload = {
    currentDay,
    source,
    lastEntity: currentEntity,
  };
  if (currentVideo != null && currentVideo !== '') {
    payload.lastVideo = currentVideo;
  }
  if (projectName) {
    payload.name = projectName;
  }
  return Object.assign(payload, extra || {});
}

/**
 * Decide which entity/video to open after project load.
 * Pure helper — no DOM / network.
 */
export function resolveSessionRestore({ lastEntity, lastVideo, videos = [] } = {}) {
  const list = Array.isArray(videos) ? videos : [];
  const available = list.filter(v => v && !v.missing && v.file);
  const byFile = (name) => list.find(v => v && v.file === name && !v.missing) || null;

  const entity = VALID_ENTITIES.has(lastEntity) ? lastEntity : 'video';

  let video = null;
  if (lastVideo) {
    const match = byFile(lastVideo);
    if (match) video = match.file;
  }
  if (!video && available.length) {
    video = available[0].file;
  }

  return { entity, video };
}
