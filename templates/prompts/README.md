# Prompt Overrides

Create a file here to override a built-in prompt from `clio/prompts.py`.

Supported names:

- `ANALYZE_PROMPT.md`
- `SCRIPT_PROMPT.md`
- `PLAN_PROMPT.md`
- `REFINE_TEXT_PROMPT.md`
- `REFINE_TEXT_FIX_PROMPT.md`
- `REFINE_SCRIPT_PROMPT.md`
- `REFINE_SCRIPT_FIX_PROMPT.md`

Lowercase names and `.txt` files also work. For formatted prompts, keep the same `{placeholder}` names used by the built-in prompt.

## Highlights 格式

`ANALYZE_PROMPT` 的 `highlights` 支持两种格式：

- 字符串：`"sunset shot"`
- 对象：`{"start": "00:08", "description": "sunset shot", "reason": "clear"}`

`.txt` 输出会渲染为 `[start] description (reason)`（`start`、`reason` 可选，省略时仅输出 `description` 或 `[start] description`）。覆盖该 prompt 时可自由选择格式，代码均兼容。

## 占位符契约

内置 prompt 经 `format_prompt_template` 格式化（`ANALYZE_PROMPT` 除外，它含字面 JSON 花括号，不经 `str.format`，因此不使用 `{{ }}` 转义）。覆盖 formatted prompt 时：

- 保留全部 `{placeholder}` 名称（见 `clio/prompt_overrides.py` 的 `PROMPT_PLACEHOLDERS`）
- JSON 示例里的花括号需用 `{{ }}` 转义
- 契约由 `clio/tests/test_prompts.py` 的 `test_builtin_prompts_match_placeholder_contract` 守护
