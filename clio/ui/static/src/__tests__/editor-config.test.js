import { beforeEach, describe, it, expect, vi } from 'vitest';
import { api } from '../api.js';
import { _attachProviderListHandlers, _renderPromptManagement, _renderTagInput, _renderProviderList, _renderTaskBinding } from '../editor-config.js';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return {
    ...actual,
    api: vi.fn(),
  };
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  api.mockReset();
});

function renderTagInput(values = [], onChange = () => {}) {
  const container = document.createElement('div');
  _renderTagInput(container, values, onChange);
  return { container, wrapper: container.querySelector('.tag-input-wrapper'), chips: container.querySelector('.tag-chips'), input: container.querySelector('.tag-input') };
}

describe('_renderTagInput', () => {
  it('renders wrapper with correct class', () => {
    const { wrapper } = renderTagInput();
    expect(wrapper).not.toBeNull();
    expect(wrapper.className).toBe('tag-input-wrapper');
  });

  it('renders input field with placeholder', () => {
    const { input } = renderTagInput();
    expect(input).not.toBeNull();
    expect(input.type).toBe('text');
    expect(input.placeholder).toBe('输入模型名，按回车添加');
  });

  it('renders no chips for empty values', () => {
    const { chips } = renderTagInput();
    expect(chips.children.length).toBe(0);
  });

  it('renders chip for each value', () => {
    const { chips } = renderTagInput(['model-a', 'model-b']);
    const chipEls = chips.querySelectorAll('.tag-chip');
    expect(chipEls.length).toBe(2);
    expect(chipEls[0].textContent).toContain('model-a');
    expect(chipEls[1].textContent).toContain('model-b');
  });

  it('escapes HTML in chip text', () => {
    const { chips } = renderTagInput(['<script>alert(1)</script>']);
    const chip = chips.querySelector('.tag-chip');
    expect(chip.innerHTML).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(chip.innerHTML).not.toContain('<script>');
  });

  it('chip has remove button', () => {
    const { chips } = renderTagInput(['test-model']);
    const remove = chips.querySelector('.tag-chip-remove');
    expect(remove).not.toBeNull();
    expect(remove.textContent).toBe('×');
  });

  it('adds value on Enter key', () => {
    const values = [];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.value = 'new-model';
    const ev = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    input.dispatchEvent(ev);

    expect(values).toEqual(['new-model']);
    expect(onChange).toHaveBeenCalledWith(['new-model']);
  });

  it('adds value on comma key', () => {
    const values = [];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.value = 'model-x';
    const ev = new KeyboardEvent('keydown', { key: ',', cancelable: true });
    input.dispatchEvent(ev);

    expect(values).toEqual(['model-x']);
    expect(onChange).toHaveBeenCalledWith(['model-x']);
  });

  it('prevents default on Enter/comma', () => {
    const onChange = vi.fn();
    const { input } = renderTagInput([], onChange);
    input.value = 'm';

    const ev = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    const prevented = !input.dispatchEvent(ev);
    expect(prevented).toBe(true);
  });

  it('does not add duplicate values', () => {
    const values = ['existing'];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.value = 'existing';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(values).toEqual(['existing']);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('does not add empty/whitespace values', () => {
    const values = [];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.value = '   ';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(values).toEqual([]);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('clears input after adding', () => {
    const { input } = renderTagInput([], () => {});
    input.value = 'model';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(input.value).toBe('');
  });

  it('removes chip on remove button click', () => {
    const values = ['keep', 'remove-me'];
    const onChange = vi.fn();
    const { container, chips } = renderTagInput(values, onChange);

    const removeBtns = container.querySelectorAll('.tag-chip-remove');
    removeBtns[1].click();

    expect(values).toEqual(['keep']);
    expect(onChange).toHaveBeenCalledWith(['keep']);
    expect(chips.querySelectorAll('.tag-chip').length).toBe(1);
  });

  it('removes last value on Backspace with empty input', () => {
    const values = ['first', 'second'];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }));

    expect(values).toEqual(['first']);
    expect(onChange).toHaveBeenCalledWith(['first']);
  });

  it('does not remove on Backspace when input has text', () => {
    const values = ['item'];
    const onChange = vi.fn();
    const { input } = renderTagInput(values, onChange);

    input.value = 'text';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }));

    expect(values).toEqual(['item']);
    expect(onChange).not.toHaveBeenCalled();
  });
});

const GEMINI_PROVIDER = { type: 'gemini', api_key_env: 'GEMINI_KEY', base_url: '', models: ['gemini-2.5-flash', 'gemini-2.0-flash'] };
const OPENAI_PROVIDER = { type: 'openai', api_key_env: 'DS_KEY', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'] };
const PROVIDER_NO_MODELS = { type: 'openai', api_key_env: 'KEY', base_url: '', models: [] };

describe('_renderProviderList', () => {
  it('renders fieldset with legend', () => {
    const html = _renderProviderList({});
    expect(html).toContain('<fieldset');
    expect(html).toContain('AI 模型列表');
    expect(html).toContain('</fieldset>');
  });

  it('shows empty state when no providers', () => {
    const html = _renderProviderList({});
    expect(html).toContain('config-empty-state');
    expect(html).toContain('还没有注册任何 AI 模型');
    expect(html).not.toContain('provider-card');
  });

  it('renders provider card with name and type badge', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).toContain('provider-card');
    expect(html).toContain('gemini');
    expect(html).toContain('Gemini');
    expect(html).not.toContain('config-empty-state');
  });

  it('renders provider test button and status area', () => {
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ gemini: GEMINI_PROVIDER });

    const btn = div.querySelector('.btn-provider-test');
    const status = div.querySelector('.provider-test-status');

    expect(btn).not.toBeNull();
    expect(btn.textContent).toBe('测试');
    expect(status).not.toBeNull();
    expect(status.textContent).toBe('');
  });

  it('shows OpenAI type label for openai providers', () => {
    const html = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    expect(html).toContain('OpenAI 兼容');
  });

  it('renders API key masked with show button', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).toContain('••••••••••');
    expect(html).toContain('btn-provider-show-key');
    expect(html).toContain('显示');
  });

  it('renders edit and delete buttons for non-default providers', () => {
    const html = _renderProviderList({ 'my-custom-provider': GEMINI_PROVIDER });
    expect(html).toContain('btn-provider-edit');
    expect(html).toContain('btn-provider-delete');
  });

  it('hides delete button for default providers', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).toContain('btn-provider-edit');
    expect(html).not.toContain('btn-provider-delete');
  });

  it('renders model chips for providers with models', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).toContain('gemini-2.5-flash');
    expect(html).toContain('gemini-2.0-flash');
    expect(html).toContain('tag-chip');
    expect(html).not.toContain('未注册模型');
  });

  it('shows warning when provider has no models', () => {
    const html = _renderProviderList({ nope: PROVIDER_NO_MODELS });
    expect(html).toContain('未注册模型');
    expect(html).toContain('warn');
  });

  it('shows base URL for non-gemini providers', () => {
    const html = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    expect(html).toContain('https://api.deepseek.com/v1');
  });

  it('hides base URL for gemini providers', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).not.toContain('接口地址');
  });

  it('renders multiple provider cards', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    const matches = html.match(/class="provider-card"/g);
    expect(matches.length).toBe(2);
  });

  it('includes add provider button', () => {
    const html = _renderProviderList({ gemini: GEMINI_PROVIDER });
    expect(html).toContain('btn-add-provider');
    expect(html).toContain('添加 Provider');
  });

  it('escapes HTML in provider name', () => {
    const html = _renderProviderList({ '<script>xss</script>': GEMINI_PROVIDER });
    expect(html).toContain('&lt;script&gt;xss&lt;/script&gt;');
    expect(html).not.toContain('<script>xss</script>');
  });

  it('posts first model when testing a single-model provider', async () => {
    api.mockResolvedValue({ ok: true, elapsed_ms: 123 });
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    _attachProviderListHandlers(div, { deepseek: OPENAI_PROVIDER });

    div.querySelector('.btn-provider-test').click();
    await vi.waitFor(() => {
      expect(api).toHaveBeenCalledWith('POST', '/api/ai/test', {
        provider: 'deepseek',
        model: 'deepseek-chat',
      });
    });
  });

  it('prompts for a multi-model provider and posts the selected model', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('gemini-2.0-flash');
    api.mockResolvedValue({ ok: true, elapsed_ms: 41 });
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ gemini: GEMINI_PROVIDER });
    _attachProviderListHandlers(div, { gemini: GEMINI_PROVIDER });

    div.querySelector('.btn-provider-test').click();
    await vi.waitFor(() => {
      expect(promptSpy).toHaveBeenCalledWith(
        expect.stringContaining('gemini-2.0-flash'),
        'gemini-2.5-flash',
      );
      expect(api).toHaveBeenCalledWith('POST', '/api/ai/test', {
        provider: 'gemini',
        model: 'gemini-2.0-flash',
      });
    });
  });

  it('does not call api and restores the button when model prompt is canceled', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue(null);
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ gemini: GEMINI_PROVIDER });
    _attachProviderListHandlers(div, { gemini: GEMINI_PROVIDER });

    const btn = div.querySelector('.btn-provider-test');
    btn.click();

    expect(btn.disabled).toBe(true);
    await vi.waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(api.mock.calls.some(([method, url]) => method === 'POST' && url === '/api/ai/test')).toBe(false);
      expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
        message: '已取消测试', severity: 'warning', source_type: 'ui_status',
      }));
      expect(div.querySelector('.provider-test-status').textContent).toBe('已取消测试');
    });
  });

  it('blocks providers without models before calling api', async () => {
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ nope: PROVIDER_NO_MODELS });
    _attachProviderListHandlers(div, { nope: PROVIDER_NO_MODELS });

    const btn = div.querySelector('.btn-provider-test');
    btn.click();

    await vi.waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(api.mock.calls.some(([method, url]) => method === 'POST' && url === '/api/ai/test')).toBe(false);
      expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
        message: '测试失败：请先添加模型', severity: 'error', source_type: 'ui_status',
      }));
      expect(div.querySelector('.provider-test-status').textContent).toBe('测试失败：请先添加模型');
    });
  });

  it('disables the test button while the request is pending and restores it after resolve', async () => {
    let resolveApi;
    api.mockReturnValue(new Promise(resolve => { resolveApi = resolve; }));
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    _attachProviderListHandlers(div, { deepseek: OPENAI_PROVIDER });

    const btn = div.querySelector('.btn-provider-test');
    btn.click();

    expect(btn.disabled).toBe(true);
    expect(div.querySelector('.provider-test-status').textContent).toBe('测试中...');

    resolveApi({ ok: true, elapsed_ms: 12 });
    await vi.waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(div.querySelector('.provider-test-status').textContent).toBe('测试成功：12 ms');
    });
  });

  it('shows successful test result using textContent', async () => {
    api.mockResolvedValue({ ok: true, elapsed_ms: 88 });
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    _attachProviderListHandlers(div, { deepseek: OPENAI_PROVIDER });

    div.querySelector('.btn-provider-test').click();
    await vi.waitFor(() => {
      const status = div.querySelector('.provider-test-status');
      expect(status.textContent).toBe('测试成功：88 ms');
      expect(status.innerHTML).toBe('测试成功：88 ms');
    });
  });

  it('shows failed test result using textContent', async () => {
    api.mockResolvedValue({ ok: false, error: '<img src=x onerror=alert(1)>' });
    const div = document.createElement('div');
    div.innerHTML = _renderProviderList({ deepseek: OPENAI_PROVIDER });
    _attachProviderListHandlers(div, { deepseek: OPENAI_PROVIDER });

    div.querySelector('.btn-provider-test').click();
    await vi.waitFor(() => {
      const status = div.querySelector('.provider-test-status');
      expect(status.textContent).toBe('测试失败：<img src=x onerror=alert(1)>');
      expect(status.innerHTML).toBe('测试失败：&lt;img src=x onerror=alert(1)&gt;');
    });
  });
});

const TASKS_WITH_VA = { video_analyze: { provider: 'gemini', model: 'gemini-2.5-flash' } };
const ALL_TASKS = {
  video_analyze: { provider: 'gemini', model: 'gemini-2.5-flash' },
  voiceover: { provider: 'deepseek', model: 'deepseek-chat' },
  vlog_plan: { provider: 'deepseek', model: 'deepseek-chat' },
  refine_text: { provider: 'deepseek', model: 'deepseek-chat' },
};

describe('_renderTaskBinding', () => {
  it('renders fieldset with legend', () => {
    const html = _renderTaskBinding({}, { gemini: GEMINI_PROVIDER });
    expect(html).toContain('<fieldset');
    expect(html).toContain('AI 任务绑定');
    expect(html).toContain('</fieldset>');
  });

  it('shows empty state when no providers', () => {
    const html = _renderTaskBinding({}, {});
    expect(html).toContain('config-empty-state');
    expect(html).toContain('还没有注册任何 AI 模型');
    expect(html).toContain('goto-global-providers');
    expect(html).not.toContain('task-binding-card');
  });

  it('renders 4 task cards with correct labels', () => {
    const html = _renderTaskBinding(ALL_TASKS, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    const cards = html.match(/task-binding-card/g);
    expect(cards.length).toBe(4);
    expect(html).toContain('视频分析');
    expect(html).toContain('口播文案');
    expect(html).toContain('vlog 剪辑规划');
    expect(html).toContain('文本精修');
  });

  it('shows provider dropdown with all providers for non-video tasks', () => {
    const html = _renderTaskBinding(ALL_TASKS, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER, extra: OPENAI_PROVIDER });
    expect(html).toContain('gemini (Gemini)');
    expect(html).toContain('deepseek (OpenAI 兼容)');
  });

  it('filters video_analyze provider dropdown to gemini only', () => {
    const html = _renderTaskBinding(TASKS_WITH_VA, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    expect(html).toContain('gemini (Gemini)');
    expect(html).toContain('deepseek (OpenAI 兼容)');
    const div = document.createElement('div');
    div.innerHTML = html;
    const vaCard = div.querySelector('.task-binding-card');
    const vaSelect = vaCard.querySelector('.task-provider-select');
    expect(vaSelect.textContent).not.toContain('deepseek');
    expect(vaSelect.textContent).toContain('gemini');
  });

  it('shows model dropdown when provider has models', () => {
    const html = _renderTaskBinding(ALL_TASKS, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    expect(html).toContain('gemini-2.5-flash');
    expect(html).toContain('deepseek-chat');
    expect(html).toContain('task-model-select');
  });

  it('shows warning when provider has no registered models', () => {
    const html = _renderTaskBinding({ video_analyze: { provider: 'nope' } }, { nope: PROVIDER_NO_MODELS });
    expect(html).toContain('该 Provider 没有注册可用模型');
  });

  it('shows muted text when no provider selected', () => {
    const html = _renderTaskBinding({ video_analyze: {} }, { gemini: GEMINI_PROVIDER });
    expect(html).toContain('请先选择 Provider');
  });

  it('shows refine_text follow checkbox checked when no refine_text config', () => {
    const html = _renderTaskBinding(TASKS_WITH_VA, { gemini: GEMINI_PROVIDER });
    expect(html).toContain('refine-follow-check');
    expect(html).toContain('checked');
    expect(html).toContain('跟随视频分析');
  });

  it('shows inherited display when refine_text follows', () => {
    const html = _renderTaskBinding(TASKS_WITH_VA, { gemini: GEMINI_PROVIDER });
    expect(html).toContain('继承自 video_analyze');
    expect(html).toContain('gemini');
    expect(html).toContain('gemini-2.5-flash');
  });

  it('shows provider/model selects when refine_text is not following', () => {
    const html = _renderTaskBinding(ALL_TASKS, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    expect(html).toContain('task-provider-select');
    expect(html).toContain('task-model-select');
    expect(html).not.toContain('继承自 video_analyze');
  });

  it('shows unchecked follow checkbox when refine_text has config', () => {
    const html = _renderTaskBinding(ALL_TASKS, { gemini: GEMINI_PROVIDER, deepseek: OPENAI_PROVIDER });
    expect(html).toContain('refine-follow-check');
    expect(html).not.toContain('refine-follow-check" checked');
  });

  it('escapes HTML in provider names and model names', () => {
    const evil = { '<script>p</script>': { type: 'gemini', api_key_env: '', models: ['<script>m</script>'] } };
    const html = _renderTaskBinding({ video_analyze: { provider: '<script>p</script>', model: '<script>m</script>' } }, evil);
    expect(html).toContain('&lt;script&gt;p&lt;/script&gt;');
    expect(html).toContain('&lt;script&gt;m&lt;/script&gt;');
    expect(html).not.toContain('<script>p</script>');
  });
});

describe('_renderPromptManagement', () => {
  const payload = {
    prompts: [
      {
        name: 'ANALYZE_PROMPT',
        default: 'default analyze',
        content: 'custom analyze',
        has_override: true,
        override_path: 'project/templates/prompts/ANALYZE_PROMPT.md',
        source_path: 'project/templates/prompts/ANALYZE_PROMPT.md',
      },
      {
        name: 'SCRIPT_PROMPT',
        default: 'default script',
        content: 'default script',
        has_override: false,
        override_path: 'project/templates/prompts/SCRIPT_PROMPT.md',
        source_path: null,
      },
    ],
  };

  it('renders prompt list and selected editor', () => {
    const html = _renderPromptManagement(payload, 'ANALYZE_PROMPT');
    expect(html).toContain('prompt-management');
    expect(html).toContain('ANALYZE_PROMPT');
    expect(html).toContain('SCRIPT_PROMPT');
    expect(html).toContain('custom analyze');
    expect(html).toContain('保存覆盖');
    expect(html).toContain('恢复默认');
  });

  it('marks overridden prompts and enables restore', () => {
    const html = _renderPromptManagement(payload, 'ANALYZE_PROMPT');
    expect(html).toContain('prompt-badge override');
    expect(html).toContain('已覆盖');
    expect(html).not.toContain('id="btn-prompt-restore" class="btn-secondary" disabled');
  });

  it('disables restore for default prompts', () => {
    const html = _renderPromptManagement(payload, 'SCRIPT_PROMPT');
    expect(html).toContain('id="btn-prompt-restore" class="btn-secondary" disabled');
    expect(html).toContain('系统默认');
  });

  it('escapes prompt content and paths', () => {
    const html = _renderPromptManagement({
      prompts: [{
        name: 'ANALYZE_PROMPT',
        default: '<default>',
        content: '<script>alert(1)</script>',
        has_override: true,
        override_path: '<path>',
        source_path: '<source>',
      }],
    });
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(html).toContain('&lt;path&gt;');
    expect(html).not.toContain('<script>alert(1)</script>');
  });

  it('renders empty state without prompts', () => {
    const html = _renderPromptManagement({ prompts: [] });
    expect(html).toContain('config-empty-state');
  });
});
