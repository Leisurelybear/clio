import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applyLocale } from '../i18n.js';
import { configSaveStatusForTab, renderPlan, setPlanExpandedIndex } from '../editor-plan.js';

describe('plan pane localization', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-plan"></div><div id="player-pane"></div>';
  });

  afterEach(() => applyLocale('zh-CN'));

  it('localizes the config save status', () => {
    applyLocale('en');
    expect(configSaveStatusForTab('project').message).toContain('Project config');
    expect(configSaveStatusForTab('global').message).toContain('hot reloaded');
    expect(configSaveStatusForTab('merged').message).toContain('read-only');

    applyLocale('zh-CN');
    expect(configSaveStatusForTab('project').message).toContain('\u9879\u76ee\u914d\u7f6e');
    expect(configSaveStatusForTab('global').message).toContain('\u70ed\u52a0\u8f7d');
  });

  it('renders editable narrative beat and transition fields', async () => {
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    state.plan = {
      day_title: 'Day',
      theme: '',
      total_estimated_sec: 8,
      opening_tip: '',
      ending_tip: '',
      sequence: [{
        index: '001',
        title: 'Walk',
        reason: 'context',
        use_timeline: '00:10-00:18',
        voiceover_hint: 'Start walking',
        narrative_beat: 'hook',
        transition: 'hard_cut',
      }],
    };
    setPlanExpandedIndex(0, { render: false });
    renderPlan();

    const pane = document.getElementById('tab-plan');
    const beat = pane.querySelector('[data-k="narrative_beat"]');
    const transition = pane.querySelector('[data-k="transition"]');
    expect(beat?.value).toBe('hook');
    expect(transition?.value).toBe('hard_cut');
  });

  it('re-renders the plan pane when the locale changes', async () => {
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    state.plan = null;

    renderPlan();
    expect(document.getElementById('tab-plan').innerHTML).toContain('vlog \u526a\u8f91\u89c4\u5212');

    applyLocale('en');
    expect(document.getElementById('tab-plan').innerHTML).toContain('Vlog edit plan');

    applyLocale('zh-CN');
    expect(document.getElementById('tab-plan').innerHTML).toContain('vlog \u526a\u8f91\u89c4\u5212');
  });
});
