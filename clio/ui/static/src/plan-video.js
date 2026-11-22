/** Match plan indexes to current compressed media, including legacy analysis aliases. */

function normalizedIndex(value) {
  return value == null ? '' : String(value).trim();
}

function analysisIndexes(video) {
  if (!Array.isArray(video?.analysis_indexes)) return [];
  return video.analysis_indexes.map(normalizedIndex).filter(Boolean);
}

/**
 * Whether one current video can represent a plan index.
 * @param {object|null|undefined} video
 * @param {string|number|null|undefined} planIndex
 * @returns {boolean}
 */
export function videoMatchesPlanIndex(video, planIndex) {
  const target = normalizedIndex(planIndex);
  if (!video || !target) return false;
  if (normalizedIndex(video.index) === target) return true;
  return analysisIndexes(video).includes(target);
}

/**
 * Find the current video for a plan index. Exact current indexes win; legacy
 * aliases are accepted only when exactly one current video claims the alias.
 * @param {Array<object>|null|undefined} videos
 * @param {string|number|null|undefined} planIndex
 * @returns {object|null}
 */
export function findPlanVideo(videos, planIndex) {
  const target = normalizedIndex(planIndex);
  if (!target || !Array.isArray(videos)) return null;

  const exact = videos.find((video) => normalizedIndex(video?.index) === target);
  if (exact) return exact;

  const aliases = videos.filter((video) => analysisIndexes(video).includes(target));
  return aliases.length === 1 ? aliases[0] : null;
}

/**
 * Resolve a current video back to the unique plan index used by a timeline.
 * @param {object|null|undefined} video
 * @param {{segments?: Array<object>}|null|undefined} timeline
 * @returns {string|null}
 */
export function findTimelineVideoIndex(video, timeline) {
  if (!video || !Array.isArray(timeline?.segments)) return null;
  const indexes = [...new Set(
    timeline.segments.map((segment) => normalizedIndex(segment?.videoIndex)).filter(Boolean),
  )];
  const matches = indexes.filter((index) => videoMatchesPlanIndex(video, index));
  return matches.length === 1 ? matches[0] : null;
}
