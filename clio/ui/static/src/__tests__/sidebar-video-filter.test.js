import { describe, expect, it } from 'vitest';
import {
  STAGE_CELLS,
  videoStageDone,
  videoMissingStage,
  buildVideoStageStatuses,
  chipDefsForSource,
  countChipStats,
  countStageSummary,
  filterVideos,
  matchVideoSearch,
} from '../sidebar-video-filter.js';
const onlineCompressed = {
  file: 'GX010195.mp4',
  index: '1',
  title: '凡尔赛',
  text_json: 't.json',
  script_json: 's.json',
  transcript_file: 'tr.json',
};

describe('videoStageDone', () => {
  it('compressed view: compress done unless missing', () => {
    expect(videoStageDone(onlineCompressed, 'compressed', 'compress')).toBe(true);
    expect(videoStageDone({ ...onlineCompressed, missing: true }, 'compressed', 'compress')).toBe(false);
  });
  it('original view: compress done only when matched', () => {
    expect(videoStageDone({ ...onlineCompressed, match: { file: 'x.mp4', missing: false } }, 'original', 'compress')).toBe(true);
    expect(videoStageDone({ ...onlineCompressed, match: null }, 'original', 'compress')).toBe(false);
  });
  it('analyze/voiceover/transcribe map from artifact fields', () => {
    expect(videoStageDone({ text_json: 't' }, 'compressed', 'analyze')).toBe(true);
    expect(videoStageDone({}, 'compressed', 'analyze')).toBe(false);
    expect(videoStageDone({ script_json: 's' }, 'compressed', 'voiceover')).toBe(true);
    expect(videoStageDone({ transcript_file: 'x' }, 'compressed', 'transcribe')).toBe(true);
    expect(videoStageDone({}, 'compressed', 'transcribe')).toBe(false);
  });
});

describe('videoMissingStage', () => {
  it('offline key reflects missing flag', () => {
    expect(videoMissingStage({ missing: true }, 'compressed', 'offline')).toBe(true);
    expect(videoMissingStage({ missing: false }, 'compressed', 'offline')).toBe(false);
  });
  it('inverts done for file stages', () => {
    expect(videoMissingStage({}, 'compressed', 'analyze')).toBe(true);
  });
});

describe('buildVideoStageStatuses', () => {
  it('contains one key per STAGE_CELL', () => {
    const s = buildVideoStageStatuses(onlineCompressed, 'compressed');
    expect(STAGE_CELLS.map((c) => c.key)).toEqual(['compress', 'analyze', 'voiceover', 'transcribe']);
    expect(s).toEqual({ compress: true, analyze: true, voiceover: true, transcribe: true });
  });
});

describe('chipDefsForSource / countChipStats', () => {
  it('non-compress chip (未压缩) only in original view', () => {
    const orig = chipDefsForSource('original').map((c) => c.key);
    expect(orig).toContain('compress');
    expect(chipDefsForSource('compressed').map((c) => c.key)).not.toContain('compress');
  });
  it('counts chips over a list', () => {
    const list = [
      { missing: false },
      { missing: true },
      { text_json: 1 },
      { script_json: 1, transcript_file: 1 },
    ];
    const stats = countChipStats(list, 'compressed');
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s.count]));
    expect(byKey.offline).toBe(1); // #1 only
    expect(byKey.analyze).toBe(3); // #0,#1,#2 missing text_json
    expect(byKey.voiceover).toBe(3); // #0,#1,#2
    expect(byKey.transcribe).toBe(3); // #0,#1,#2 missing transcript_file
  });
});

describe('countStageSummary', () => {
  it('per-stage done/total cells', () => {
    const sum = countStageSummary(
      [{ ...onlineCompressed }, { ...onlineCompressed, missing: true }],
      'compressed',
    );
    const by = Object.fromEntries(sum.map((s) => [s.key, s]));
    expect(by.compress.done).toBe(1);
    expect(by.compress.total).toBe(2);
    expect(by.analyze.done).toBe(1);
  });
});

describe('matchVideoSearch', () => {
  const v = { index: '001', file: 'GG063.HQ.mp4', title: 'test' };
  it('matches index / name / title case-insensitively', () => {
    expect(matchVideoSearch(v, '001')).toBe(true);
    expect(matchVideoSearch(v, 'gg063')).toBe(true);
    expect(matchVideoSearch(v, 'TEST')).toBe(true);
    expect(matchVideoSearch(v, 'no-such')).toBe(false);
  });
  it('treats empty query as match-all', () => {
    expect(matchVideoSearch(v, '')).toBe(true);
  });
});

describe('filterVideos', () => {
  const videos = [
    { file: '001_alpha.mp4', index: '001', title: 'Alpha', text_json: 'a.json' },
    { file: '002_beta.mp4', index: '002', title: 'Beta' },
    { file: '003_alphabet.mp4', index: '003', title: 'Alphabet', missing: true },
  ];

  it('keeps only name/index/title matches', () => {
    const out = filterVideos(videos, { q: 'alpha' }, 'original');
    expect(out.map((v) => v.file)).toEqual(['001_alpha.mp4', '003_alphabet.mp4']);
  });

  it('combines the search with a missing-stage chip', () => {
    const out = filterVideos(videos, { q: 'a', stage: 'analyze', mode: 'missing' }, 'compressed');
    expect(out.map((v) => v.file)).toEqual(['002_beta.mp4', '003_alphabet.mp4']);
  });

  it('combines the search with a done-stage chip', () => {
    const out = filterVideos(videos, { q: '', stage: 'analyze', mode: 'done' }, 'compressed');
    expect(out.map((v) => v.file)).toEqual(['001_alpha.mp4']);
  });

  it('returns the whole list for an empty filter', () => {
    expect(filterVideos(videos, {}, 'original')).toEqual(videos);
  });
});