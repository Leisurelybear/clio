// Global application state
const state = {
  config: null,
  configRaw: null,
  configGlobal: null,
  configProject: null,
  configTab: 'project',  // 'project' | 'global' | 'merged' | 'prompts'
  promptPayload: null,
  currentPromptName: null,
  source: 'compressed',
  videos: [],
  currentEntity: 'video',  // 'video' | 'plan' | 'run' | 'config' | 'tasks'
  currentVideo: null,
  currentDay: 'day1',
  availablePlans: [],
  currentTab: 'texts',
  texts: null,
  voiceover: null,
  transcript: null,
  plan: null,
  planPreviewMedia: null,
  dirty: false,
  artifactVersions: { plan: {}, texts: {}, voiceover: {} },
  projectName: null,
  projects: [],
  currentProject: null,
  currentProjectName: null,
  currentProjectDir: null,
  lastProject: null,
  lastProjectDir: null,
  lastEntity: null,  // restored from project.json after loadProject
  lastVideo: null,
  groups: {},
  expandedGroups: {},
  // preview playback
  previewActive: false,
  previewIndex: -1,
  previewGlobalSec: 0,
  _previewEndTime: null,
  _previewMedia: null,
  previewCutFallbacks: new Set(),
  selectionMode: false,
  selectedFiles: [],
  tasks: [],
  taskFilters: { status: 'all', kind: 'all', project: 'all' },
  selectedTaskId: null,
  taskDetail: null,
  taskLatestSeq: 0,
  notifications: [],
  notificationUnread: 0,
  notificationLatestSeq: 0,
refining: null,  // {type: 'texts'|'scripts', file: string} when AI refine in progress
  deps: null,  // { ok, ffmpeg, ffprobe, missing, detail } from GET /api/deps/ffmpeg
  videoFilter: { q: '', stage: '', mode: 'missing' },  // stage: '' | compress/analyze/voiceover/transcribe/offline; mode: 'missing' (chips) | 'done' (count bar)
  _filterDebounce: null,
};

function clearSelection() {
  state.selectionMode = false;
  state.selectedFiles = [];
}

function artifactVersion(kind, key) {
  const bucket = state.artifactVersions?.[kind];
  if (!bucket) return null;
  return bucket[String(key ?? '')] || null;
}

function setArtifactVersion(kind, key, version) {
  if (!state.artifactVersions) return;
  if (!state.artifactVersions[kind]) state.artifactVersions[kind] = {};
  const normalized = String(key ?? '');
  if (!normalized || !version) return;
  state.artifactVersions[kind][normalized] = version;
}

function clearArtifactVersion(kind, key) {
  const bucket = state.artifactVersions?.[kind];
  if (!bucket) return;
  delete bucket[String(key ?? '')];
}

export { state, clearSelection, artifactVersion, setArtifactVersion, clearArtifactVersion };
