import { describe, it, expect } from 'vitest';
import { resolveEditorSaveTarget, shouldConfirmDirtyTabSwitch } from '../editor-save.js';

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
