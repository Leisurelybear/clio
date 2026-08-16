import { describe, it, expect } from 'vitest';
import {
  isCompressStepDone,
  buildVideoStepBadges,
  buildVideoMenuItems,
} from '../video-menu.js';

describe('isCompressStepDone', () => {
  it('true when viewing compressed and file is not missing', () => {
    expect(isCompressStepDone({ missing: false }, 'compressed')).toBe(true);
  });

  it('false when viewing compressed but entry is missing', () => {
    expect(isCompressStepDone({ missing: true }, 'compressed')).toBe(false);
  });

  it('true on original when match points to existing compressed', () => {
    expect(
      isCompressStepDone(
        { missing: false, match: { file: '001_x.mp4', missing: false } },
        'original'
      )
    ).toBe(true);
  });

  it('false on original when no match or match offline', () => {
    expect(isCompressStepDone({ match: null }, 'original')).toBe(false);
    expect(isCompressStepDone({ match: { missing: true } }, 'original')).toBe(false);
  });
});

describe('buildVideoStepBadges', () => {
  it('reflects real artifact presence not source view alone', () => {
    const badges = buildVideoStepBadges(
      {
        missing: false,
        text_json: 'a.json',
        script_json: null,
        transcript_file: 't.json',
        match: { file: '001_x.mp4', missing: false },
      },
      'original'
    );
    expect(badges).toEqual([
      { label: '压缩', done: true },
      { label: '分析', done: true },
      { label: '口播', done: false },
      { label: '转录', done: true },
    ]);
  });
});

describe('buildVideoMenuItems', () => {
  it('compressed online: analyze, voiceover, all, transcribe, remove — not relink', () => {
    const items = buildVideoMenuItems(
      { missing: false, file: '001_a.mp4', index: '001' },
      'compressed'
    );
    const actions = items.filter((i) => !i.divider).map((i) => i.action);
    expect(actions).toContain('analyze');
    expect(actions).toContain('voiceover');
    expect(actions).toContain('all');
    expect(actions).toContain('transcribe');
    expect(actions).toContain('remove');
    expect(actions).not.toContain('relink');
    expect(items.find((i) => i.action === 'compress')?.disabled).toBe(true);
  });

  it('original online: compress+transcribe enabled; analyze/voiceover/all disabled; remove', () => {
    const items = buildVideoMenuItems({ missing: false, file: 'a.mp4' }, 'original');
    expect(items.find((i) => i.action === 'compress')?.disabled).toBe(false);
    expect(items.find((i) => i.action === 'transcribe')?.disabled).toBe(false);
    expect(items.find((i) => i.action === 'analyze')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'voiceover')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'all')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'remove')?.disabled).toBe(false);
    expect(items.some((i) => i.action === 'relink')).toBe(false);
  });

  it('original offline: only relink and remove enabled among actions', () => {
    const items = buildVideoMenuItems({ missing: true, file: 'a.mp4' }, 'original');
    const enabled = items.filter((i) => !i.divider && !i.disabled).map((i) => i.action);
    expect(enabled.sort()).toEqual(['relink', 'remove'].sort());
  });

  it('compressed offline: pipeline actions disabled; only remove enabled', () => {
    const items = buildVideoMenuItems({ missing: true, file: '001_a.mp4' }, 'compressed');
    const enabled = items.filter((i) => !i.divider && !i.disabled).map((i) => i.action);
    expect(enabled).toEqual(['remove']);
    expect(items.find((i) => i.action === 'analyze')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'transcribe')?.disabled).toBe(true);
  });

  it('disabled analyze on original explains switch to compressed', () => {
    const item = buildVideoMenuItems({ missing: false }, 'original').find(
      (i) => i.action === 'analyze'
    );
    expect(item.title).toMatch(/压缩/);
  });

  it('disables compress and transcribe when ffmpeg deps missing', () => {
    const deps = { ok: false, detail: '未找到 ffmpeg' };
    const items = buildVideoMenuItems({ missing: false, file: 'a.mp4' }, 'original', deps);
    expect(items.find((i) => i.action === 'compress')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'transcribe')?.disabled).toBe(true);
    expect(items.find((i) => i.action === 'compress')?.title).toMatch(/ffmpeg|未找到/);
    expect(items.find((i) => i.action === 'remove')?.disabled).toBe(false);
  });

  it('leaves compress enabled when deps ok on original online', () => {
    const items = buildVideoMenuItems(
      { missing: false, file: 'a.mp4' },
      'original',
      { ok: true, missing: [] }
    );
    expect(items.find((i) => i.action === 'compress')?.disabled).toBe(false);
  });

  it('offers reveal for an online video with an absolute path', () => {
    const items = buildVideoMenuItems(
      { missing: false, file: 'a.mp4', abs_path: 'D:/trip/a.mp4' },
      'original'
    );
    expect(items.find((i) => i.action === 'reveal')).toMatchObject({
      label: '在文件管理器中显示',
    });
  });

  it('does not offer reveal for an offline video', () => {
    const items = buildVideoMenuItems(
      { missing: true, file: 'a.mp4', abs_path: 'D:/trip/a.mp4' },
      'original'
    );
    expect(items.some((i) => i.action === 'reveal')).toBe(false);
  });
});
