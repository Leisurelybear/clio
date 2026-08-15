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
  dirty: false,
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
  selectionMode: false,
  selectedFiles: [],
  tasks: [],
  taskFilters: { status: 'all', kind: 'all', project: 'all' },
  selectedTaskId: null,
  taskDetail: null,
  taskLatestSeq: 0,
refining: null,  // {type: 'texts'|'scripts', file: string} when AI refine in progress
  deps: null,  // { ok, ffmpeg, ffprobe, missing, detail } from GET /api/deps/ffmpeg
  videoFilter: { q: '', stage: '', mode: 'missing' },  // stage: '' | compress/analyze/voiceover/transcribe/offline; mode: 'missing' (chips) | 'done' (count bar)
  _filterDebounce: null,
};

function clearSelection() {
  state.selectionMode = false;
  state.selectedFiles = [];
}

export { state, clearSelection };
