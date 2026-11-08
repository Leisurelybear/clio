import { describe, it, expect } from 'vitest';
import {
  artifactIfMatchHeader,
  isArtifactConflict,
  resolveEditorSaveTarget,
  shouldConfirmDirtyTabSwitch,
} from '../editor-save.js';

describe('resolveEditorSaveTarget', () => {
  it('saves texts / voiceover for video entity', () => {
    expect(resolveEditorSaveTarget({ entity: 'video', tab: 'texts' })).toEqual({ action: 'texts' });
    expect(resolveEditorSaveTarget({ entity: 'video', tab: 'voiceover' })).toEqual({ action: 'voiceover' });
  });

  it('does not claim save on transcript tab (no false success)', () => {
    const r = resolveEditorSaveTarget({ entity: 'video', tab: 'transcript' });
    expect(r.action).toBe('noop');
    expect(r.reasonKey).toBe('editor.save.nothing');
  });

  it('plans and config global/project are writable', () => {
    expect(resolveEditorSaveTarget({ entity: 'plan' })).toEqual({ action: 'plan' });
    expect(resolveEditorSaveTarget({ entity: 'config', configTab: 'global' })).toEqual({
      action: 'config',
      configTab: 'global',
    });
    expect(resolveEditorSaveTarget({ entity: 'config', configTab: 'project' }).action).toBe('config');
  });

  it('run / logs / merged config are noop', () => {
    expect(resolveEditorSaveTarget({ entity: 'run' }).reasonKey).toBe('editor.save.no_target');
    expect(resolveEditorSaveTarget({ entity: 'run' }).action).toBe('noop');
    expect(resolveEditorSaveTarget({ entity: 'config', configTab: 'merged' }).reasonKey).toBe('plan.config_readonly');
    expect(resolveEditorSaveTarget({ entity: 'config', configTab: 'merged' }).action).toBe('noop');
    expect(resolveEditorSaveTarget({ entity: 'config', configTab: 'prompts' }).action).toBe('noop');
  });
});

describe('shouldConfirmDirtyTabSwitch', () => {
  it('blocks only when dirty and tab actually changes', () => {
    expect(shouldConfirmDirtyTabSwitch({ dirty: true, fromTab: 'texts', toTab: 'transcript' })).toBe(true);
    expect(shouldConfirmDirtyTabSwitch({ dirty: true, fromTab: 'texts', toTab: 'texts' })).toBe(false);
    expect(shouldConfirmDirtyTabSwitch({ dirty: false, fromTab: 'texts', toTab: 'voiceover' })).toBe(false);
  });
});

describe('artifact save protocol helpers', () => {
  it('builds a quoted If-Match header only when a version exists', () => {
    expect(artifactIfMatchHeader('0123456789abcdef')).toEqual({
      'If-Match': '"0123456789abcdef"',
    });
    expect(artifactIfMatchHeader(null)).toEqual({});
    expect(artifactIfMatchHeader('')).toEqual({});
  });

  it('recognizes only artifact_conflict 409 responses', () => {
    expect(isArtifactConflict({ status: 409, body: { code: 'artifact_conflict' } })).toBe(true);
    expect(isArtifactConflict({ status: 409, body: { code: 'refine_busy' } })).toBe(false);
    expect(isArtifactConflict({ status: 400, body: { code: 'artifact_conflict' } })).toBe(false);
    expect(isArtifactConflict(new Error('plain'))).toBe(false);
  });
});
