/**
 * Shared /api/video URL builder used by both the main player and the
 * preview preloader, so auth/project/abs-path params never diverge.
 */

/**
 * Build a /api/video URL for a video entity.
 * @param {{ file: string, abs_path?: string }} v
 * @param {string} source
 * @param {string|null} projectName
 * @returns {string}
 */
export function videoUrlFor(v, source, projectName) {
  const projParam = projectName ? ('&project=' + encodeURIComponent(projectName)) : '';
  const tokenParam = sessionStorage.getItem('api_token');
  const extraParam = tokenParam ? ('&token=' + encodeURIComponent(tokenParam)) : '';
  const absParam = v?.abs_path ? ('&abspath=' + encodeURIComponent(v.abs_path)) : '';
  return '/api/video?file=' + encodeURIComponent(v.file) + '&source=' + source + absParam + projParam + extraParam;
}
