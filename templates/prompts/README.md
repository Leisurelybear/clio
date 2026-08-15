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

`.txt` 输出会渲染为 `[start] description`。覆盖该 prompt 时可自由选择格式，代码均兼容。
