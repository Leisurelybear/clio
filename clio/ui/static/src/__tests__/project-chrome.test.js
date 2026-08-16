import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { state } from '../state.js';
import { updateProjectSidebar } from '../utils.js';

describe('project chrome', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="btn-project-switcher"><span id="proj-name"></span></button>
      <button id="btn-reveal-project"></button>
      <div id="project-menu-path"></div>
    `;
    state.currentProject = null;
    state.currentProjectName = null;
    state.currentProjectDir = null;
    state.projectName = '';
    state.config = null;
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('shows the project name and keeps the directory as supporting metadata', () => {
    state.currentProject = { name: '巴黎秋日漫游', project_dir: 'D:/Travel/Paris' };
    state.currentProjectName = '巴黎秋日漫游';
    state.currentProjectDir = 'D:/Travel/Paris';

    updateProjectSidebar();

    expect(document.querySelector('#proj-name').textContent).toBe('巴黎秋日漫游');
    expect(document.querySelector('#proj-name').title).toContain('D:/Travel/Paris');
    expect(document.querySelector('#project-menu-path').textContent).toContain('巴黎秋日漫游');
    expect(document.querySelector('#btn-reveal-project').disabled).toBe(false);
  });

  it('shows a clear empty state and disables opening a missing directory', () => {
    updateProjectSidebar();

    expect(document.querySelector('#proj-name').textContent).toBe('选择项目');
    expect(document.querySelector('#project-menu-path').textContent).toBe('尚未打开项目');
    expect(document.querySelector('#btn-reveal-project').disabled).toBe(true);
    expect(document.querySelector('#btn-reveal-project').getAttribute('aria-disabled')).toBe('true');
  });
});
