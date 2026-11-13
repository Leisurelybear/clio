# CLIO Open Items Status (2026-11-13)

Source: [`2026-11-13-clio-open-items.csv`](2026-11-13-clio-open-items.csv).

Status values: `Confirmed`, `In progress`, `Fixed`, `Satisfied`, `Environment verification`, `Duplicate`, `Not applicable`, `Pending audit`.

A `Fixed` row must include the commit and focused verification. `Satisfied` rows record existing implementation evidence rather than reimplementing stale findings.

## Summary

| Priority | Total | Fixed | Satisfied | In progress | Confirmed | Environment verification | Duplicate | Not applicable | Pending audit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| P0 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| P1 | 35 | 20 | 15 | 0 | 0 | 0 | 0 | 0 | 0 |
| P2 | 59 | 13 | 14 | 0 | 0 | 0 | 0 | 0 | 32 |
| P3 | 14 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |

## Item status

| # | Priority | ID | Issue | Status | Evidence / verification |
| ---: | --- | --- | --- | --- | --- |
| 001 | P0 | R001 | LAN 模式不会自动生成 token: 示例配置的 api_token: "" 是空串而不是 None, if TOKEN is None 不成立 | Fixed | Blank tokens now normalize to `None`, so LAN binds generate and pass an auth token while loopback stays tokenless. Focused: 5 pytest cases passed. |
| 002 | P0 | R004 | --force, analyze.skip_existing:false, 单视频重跑 (compress / all) 都只把 skip_existing 置为 False 而不传 overwrite, 于是不建 existing_map, 每次都分配新编号 | Fixed | Force/rerun mode now scans existing artifacts, re-compresses, and reuses the original numeric slot. Focused: 23 compress tests passed. |
| 003 | P0 | E1 | "全部重跑"把 ClipRecord.text_path (.txt) 当作分析 JSON 传给口播步骤 | Fixed | Rerun-all now derives the analysis JSON path from the recorded `.txt` path. Covered by a real `ClipRecord.text_path=.txt` behavioral test. Focused: 72 run-route tests passed. |
| 004 | P0 | R005 | 网络层错误从不重试: google-genai 抛出的是 httpx.ConnectError/ReadTimeout, 它们不是 OSError, 被包装成"不可重试错误" | Fixed | `httpx.TransportError` is now a retryable type and is accepted by the retry predicate; ConnectError and ReadTimeout are covered. Focused: 66 Gemini provider tests passed. |
| 005 | P0 | E2 | 结构校验重试把"上次模型输出"当作 prompt 前缀, 原始任务说明、上下文、转录全部丢失 | Fixed | Validation retries now append the previous JSON and field errors to the original task prompt for video analysis, voiceover, and planned voiceover. Focused: 80 analyze tests passed. |
| 006 | P0 | E3 | 总时长偏离目标超过 25% 时重试一次后直接失败 | Fixed | Duration deviation no longer enters blocking plan validation. It remains a non-blocking warning while the validated plan and actual total duration are returned. Focused: 80 analyze tests passed. |
| 007 | P0 | E6 | 默认值变更没有迁移: 旧配置保留 remove_audio: true; 升级后全部分析、口播静默重跑 | Fixed | `080b131` preserves the explicit legacy setting and adds a shared preview/start gate. The preview reports analysis, voiceover, and compression rerun counts; unconfirmed starts return 409 and the UI asks before sending the confirmation flag. Focused: 117 pytest and 48 Vitest cases passed; full suites: 3216 pytest and 921 Vitest cases passed. |
| 008 | P0 | R032 | rerun all 的单测把 ClipRecord.text_path mock 成 .json 路径; 真实对象里它是 .txt | Fixed | The regression test now uses a `.txt` `ClipRecord.text_path` and verifies the derived `.json` artifact is passed to voiceover generation. Fixed together with E1. Focused: 72 run-route tests passed. |
| 009 | P0 | R033 | 结构重试的单测只断言调用次数和"含上次 JSON", 不断言重试 prompt 保留原始任务; 另有用例把"时长偏差两次后失败"写成期望行为 | Fixed | Tests now assert original wrapped prompt/context retention on validation retry and expect a duration-deviating plan to succeed with its actual total. Focused: 80 analyze tests passed across E2/E3 verification. |
| 010 | P1 | R024 | 没有"真实 ffmpeg + 假 Provider"的端到端冒烟测试 | Fixed | `b8d3200` adds a real-ffmpeg smoke test that generates synthetic A/V media and drives `run_pipeline_steps(["compress", "analyze"])` with only the provider faked. It verifies compression, `.vmeta`/`.vindex` identity, compressed input to the provider, analysis JSON, and the pipeline summary. Focused: 71 tests passed; full suite: 3223 passed, 12 skipped. |
| 011 | P1 | OPT-031 | CI 没有真实 ffmpeg 的任务; pip-audit 不拦截; 桌面打包任务按条件跳过 | Fixed | `e1c45e1` installs ffmpeg on Ubuntu, marks the smoke test `e2e`, runs it as a required CI step, and fails if ffmpeg unexpectedly disappears. `23006c0` removes `continue-on-error` and uses `pip-audit --strict`. `ecff892` builds the desktop bundle on pull requests and main-branch pushes. Verification: e2e 1 passed, filtered pipeline suite 12 passed, workflow YAML parsed successfully, and `git diff --check` passed. |
| 012 | P1 | OPT-029 | 关键回归被理想化 mock 掩盖 (R032、R033) | Satisfied | Existing regressions now use the real `ClipRecord` shape (`605643f`, `.txt` text path), assert validation retries preserve the original wrapped prompt (`fa9f313`), and assert duration deviation succeeds rather than retrying (`c9897f1`). The real-ffmpeg pipeline smoke (`b8d3200`) also crosses compress/analyze boundaries with only the provider faked. Focused: 5 boundary tests passed. |
| 013 | P1 | OPT-034 | 11 月 7-11 日五天合入 75 个提交, 其中 5 个修复引入回归 | Satisfied | The remediation batch now pairs each high-severity fix with a behavioral regression (`605643f`, `fa9f313`, `c9897f1`, `ff98198`) and adds a production-path real-ffmpeg smoke (`b8d3200`) required on every CI run and PR (`e1c45e1`). This directly guards the cross-boundary regressions that previously required follow-up commits. |
| 014 | P1 | R002 | Provider 的 base_url 与 api_key_env 可经 API 任意改写, 随后 /api/ai/test 会把该环境变量的值作为 Bearer 发往新地址 | Fixed | `ff98198` binds each provider to its canonical `<PROVIDER>_API_KEY`, requires proof of possession before URL or credential-slot changes, strips stored keys, and rejects unsafe connection-test targets before any provider call. Focused: 89 passed, 1 skipped; full suite: 3222 passed, 12 skipped. |
| 015 | P1 | E8 | 分析保存不打用户编辑标记; 重跑或指纹变化时旧分析被直接删除 | Fixed | `427ea6f` marks saved analysis as user-edited and gates stale replacement behind the shared artifact-overwrite confirmation. Confirmed replacements back up JSON/TXT before rewriting or cleanup; preview/start reports analysis artifacts. Focused: 216 pytest cases passed; full suite: 3227 passed, 12 skipped. |
| 016 | P1 | R006 | 只有 run_plan_all_days 传 filter_by_day=True; run --day、plan --day、UI 运行都不过天过滤, UI 也没有 all-days 入口 | Fixed | `ed63199` makes unscoped multi-day planning default to day filtering, preserving selection-scoped plans. `ab690d1` adds all-day pipeline and UI scoping with previews; explicit file selections remain selection-scoped. Focused: 224 pytest and 34 runner Vitest cases passed; full suites: 3246 pytest (12 skipped) and 924 Vitest cases passed; typecheck, Ruff, mypy, and `main.py check` passed. |
| 017 | P1 | E4 | 长视频每个窗口切片都注入整段转录 (绝对时间戳) | Fixed | `caaa9ad` clips each injected transcript to the current window or sparse-retry gap, rebases timestamps to zero, preserves segment confidence fields, and bumps analysis/window cache versions. Focused: 44 pytest cases passed; full suite: 3250 passed, 12 skipped; Ruff, mypy, and `git diff --check` passed. |
| 018 | P1 | E5 | 按 creation_time 的 UTC 日期分天; dayN 每次重算, 但只在为空时写入 | Fixed | `ac81c05` converts timezone-aware container `creation_time` values to UTC before assigning day labels while preserving filename-date fallback. Project-wide relabeling and fill-only writes are covered by existing and new cache tests. Focused: 55 analyze-task tests passed. Full: 3256 passed, 12 skipped; Ruff, mypy, `main.py check`, and `git diff --check` passed. |
| 019 | P1 | E9 | 多窗口合并后的摘要重写失败会让整个长视频失败 | Fixed | `b78a43f` keeps the merged window analysis when summary rewriting fails, marks it with `_summary_failed: true`, and emits a localized warning; cancellation still propagates. Focused: 55 analyze-task and 24 i18n tests passed. Full: 3256 passed, 12 skipped; Ruff, mypy, `main.py check`, and `git diff --check` passed. |
| 020 | P1 | E7 | 单视频重跑不接受 confirm_artifact_overwrite, 口播被编辑过时只会失败 | Fixed | `ff56888` adds rerun preflight protection for edited analysis and voiceover artifacts, persists the confirmation on managed rerun tasks, and prompts before retrying. Focused: 124 pytest and 4 Vitest cases passed. Full: 3260 pytest passed, 12 skipped; 926 Vitest passed; typecheck, Ruff, mypy, `main.py check`, and `git diff --check` passed. |
| 021 | P1 | E10 | .env 里只要有白名单外的变量就整体拒绝保存 | Fixed | `ccd42a4` preserves arbitrary non-dangerous dotenv entries while allowing project-read variables such as `CLIO_LANG`, ffmpeg/PyTorch tuning, and proxy endpoints. Secret masking is case-insensitive, dangerous executable/module-injection keys remain blocked, and rejected saves leave the existing file untouched. Focused: 22 passed, 1 skipped; Ruff and `git diff --check` passed. The full suite remains order-dependent because `TestActiveLocale` leaks the process locale; the isolated rerun reached 3230 passed, 37 failed, with failures limited to unrelated locale assertions. |
| 022 | P1 | R010 | 10-bit 原片 (HDR HEVC) 压缩后是 H.264 High 10 / yuv420p10le | Fixed | `6d7f1a0` forces `yuv420p` output and tone-maps PQ/HLG sources to BT.709 SDR. Focused: 173 related pytest and 36 export tests passed; real ffmpeg smoke produced H.264 High / yuv420p / BT.709 from HEVC Main 10 / PQ / BT.2020. |
| 023 | P1 | R008 | 示例配置没有 config_version, 首次加载被当成 V1 迁移, 经 yaml.dump 重写后注释全部丢失 | Fixed | `8bd9bc8` adds `config_version: V2` to the example and handles versionless or V1 global-only files by inserting or replacing only the version marker, preserving existing text, comments, and CRLF line endings without a backup rewrite. Genuine V1 configs with project-level fields still use the split migration. Focused: 168 config tests passed; Ruff and `git diff --check` passed. A broader config batch reached 275 passed with one unrelated existing project-route replacement assertion failure. |
| 024 | P1 | R009 | 自动升级把当前默认值"写进用户 YAML", 默认值以后再变, 老用户也拿不到; 且在读取路径上写文件 | Fixed | `3dff30b` removes loader-time dataclass default injection for both global and project configs. Missing YAML fields now use runtime defaults without rewriting or losing comments. Aside from first-run config creation, no existing config file is rewritten during loading; only explicit V1-to-V2 migration remains a write path. Focused: 207 config/schema/cache tests passed, plus 165 config follow-up tests. |
| 025 | P1 | OPT-028 | doctor 不检查旧默认值与模型名有效性 | Fixed | `5a67ee5` warns for known legacy defaults and empty or unregistered model bindings, probes each distinct configured provider/model pair once with a bounded timeout (including keyless Ollama), and keeps connectivity failures non-blocking. Focused: 92 pytest cases, ruff, mypy, `main.py check`, and `git diff --check` passed. |
| 026 | P1 | R011 | 分析指纹包含整段转录文本和全部压缩参数, 任何变化都让全部分析失效并静默重跑 | Fixed | `d7f72da` narrows the analysis lineage to effective inputs and actual compressed identity, exposes invalidation components, and adds browser-UI-safe cache diagnostics. `3cb3d20` shows rerun counts/reasons in run previews, returns 409 until confirmed, and forwards the confirmation through `run/start`. Focused: 204 pytest and 56 Vitest cases passed; typecheck and Ruff passed. |
| 027 | P1 | OPT-011 | 运行预览只给步骤数 (/api/run/preview), 没有调用数、token 和费用 (F2) | Fixed | `298018b` adds conservative per-step estimates for planned calls, prompt/completion tokens, upload bytes, and USD cost when both provider prices are configured. Totals aggregate all selected AI steps, skipped steps report zero, and unconfigured or partial pricing is shown as unknown rather than `$0`. Focused: 153 run-preview/route pytest cases, 35 Vitest cases, typecheck, Ruff, and mypy passed. |
| 028 | P1 | R014 | plan 输入不设上限: 全部素材的完整 timeline 一次送入, 且每条转录出现三次 (transcript、transcript_segments、<transcript> 块) | Satisfied | Existing `build_plan_clips()` limits timeline rows and key moments, removes inline transcripts, and uses a bounded candidate shortlist (`90e58f0`, `95da5f6`, `47703f8`). Focused: 102 planning tests passed. |
| 029 | P1 | OPT-012 | plan 的 clips_json 每条 timeline 都带 transcript 与 transcript_segments, 同样内容又出现在 <transcript> 块 (实测每条约 183 字符, 其中转录相关约 110) | Satisfied | Existing projection omits top-level and timeline-level transcript fields before serialization; regression coverage asserts the removal. Focused with R014: 102 planning tests passed. |
| 030 | P1 | R018 | 默认绑定 deepseek-chat / deepseek-reasoner; DeepSeek 官方价目页现在只列 deepseek-flash 与 deepseek-v4-pro, 并写明 v4-flash 等旧名已退役 | Fixed | Examples now bind voiceover to `deepseek-flash` and vlog_plan to `deepseek-v4-pro`; provider/model suggestions, UI defaults, docs, and locale descriptions use the same official pair. Retired names still load with the existing model-list warning. Focused: 106 config tests and 78 Vitest cases passed; typecheck passed. A credentialed `/api/ai/test` call was not run because no DeepSeek API key is available locally. |
| 031 | P1 | OPT-006 | video_analyze 默认绑定没有 temperature (config.example.yaml:71-73), 实测调用时为 None | Fixed | Both shipped examples now bind video analysis at temperature `0.2`; the example-config regression asserts the value. Focused: 106 config tests passed. |
| 032 | P1 | OPT-001 | 没有评测集, Q1/Q2/Q8/Q9 的效果无法量化 (F1 未开始) | Satisfied | The v1 evaluation suite contains 12 real-footage cases with reviewed references and a stored prediction baseline (`98b4f4d`): 12/12 scored, mean 96.92, minimum 87.51. The suite enforces a minimum-score gate of 70. Focused: 16 evaluation tests passed. |
| 033 | P1 | R007 | 三条约束互相冲突: max_clips_per_day=12 按"段"截断、总时长 180 秒 ±25% 不满足即失败、提示词要求 3-5 秒钩子与 1-3 秒 B-roll | Fixed | `17aca81` separates distinct-source and final-segment limits, keeps non-overlapping cuts from one source, retains timeline validation, and makes `{max_segments}` optional for legacy prompt overrides. Focused: 376 pytest cases passed; frontend: 934 Vitest cases passed. |
| 034 | P1 | O1 | 未使用原生结构化输出 | Satisfied | Satisfied by native structured output (`446d52b`). |
| 035 | P1 | OPT-002 | 所有任务靠提示约束 JSON, 再用 extract_json 容错 (clio/analyze.py:822, 1026); 非 JSON 输出直接失败 (E12) | Satisfied | Native structured output is implemented in `446d52b`: Pydantic-backed schemas in `clio/ai/structured.py` are sent as Gemini `response_json_schema` and OpenAI-compatible `response_format`; fixed-output tasks use schemas while refine tasks retain JSON-object mode and tolerant parsing. Focused: 123 evaluation/provider tests passed. |
| 036 | P1 | Q6 | 分析输出是散文, 缺镜头属性 | Satisfied | Satisfied; structured shot fields implemented (`c5600d7`, `336824c`). |
| 037 | P1 | Q11 | 规划一次生成, 没有评审轮 | Satisfied | Satisfied; staged planning implemented (`c3156ba`, `d47829d`). |
| 038 | P1 | Q12 | 口播在规划前按单素材生成 | Satisfied | Satisfied; planned voiceover implemented (`89b9315`, `9da2483`). |
| 039 | P1 | Q7 | 各素材独立分析, 实体名不一致 | Satisfied | Satisfied; same-day consistency stage implemented (`b141fb6`, `8292a0c`, `cfc3c81`, `535146b`). |
| 040 | P1 | Q5 | 未用 fps、偏移和 media_resolution | Satisfied | Satisfied; media resolution/window metadata implemented (`304acd3`). |
| 041 | P1 | Q4 | 拍摄时间与 GPS 默认不用 | Satisfied | Satisfied; GPMF defaults are enabled (`7189ede`). |
| 042 | P1 | F2 | 只统计 token, 没有费用与版本 | Satisfied | Existing calls record provider, model, prompt-version hash, split token counts, and estimated USD cost; the token pane exposes provider, prompt version, and cost. Focused: 18 token-usage tests passed. |
| 043 | P1 | OPT-018 | 单视频重跑 (clio/ui/routes/run.py:113-196) 自己维护一套步骤表, 与 pipeline_STEP_FUNCS 平行 | Fixed | `b1d24df` derives `SINGLE_VIDEO_RERUN_STEPS` from `PIPELINE_STEPS` and drives reruns through step runner/label maps. Focused: 107 pytest cases passed; Ruff passed. |
| 044 | P1 | O17 | AGENTS.md 过长 | Satisfied | Satisfied; documentation split completed. |
| 045 | P2 | R003 | Origin/Host 校验和 JSON Content-Type 强制只在本机模式开启; LAN 模式全部关闭 | Fixed | `079427a` enforces Origin/Referer-to-Host consistency on every bind and requires JSON content types for all `run()` modes; only the Host allowlist remains loopback-specific. Focused: 227 pytest cases passed, 1 skipped; Ruff passed. |
| 046 | P2 | O15 | 手工转义; 首页无安全响应头 | Satisfied | Satisfied; escaping/security headers completed (`a97bf4d`, `7390edd`). |
| 047 | P2 | C10 | ??????????????? | Satisfied | The analyze path no longer cuts physical window slices: `clio/tasks/analyze.py:734-760` sends the compressed file once per logical window and `clio/ai/gemini.py:420-430` applies Gemini `VideoMetadata(start_offset/end_offset, fps)`. `slice_window_video` remains only as a legacy helper. Focused: 90 `test_tasks_analyze` / `test_analyze_windows` tests passed. |
| 048 | P2 | D11 | ????????? meta ???os ? fps ?? | Satisfied | `clio/export/jianying.py:351-366` clamps each source timerange to the material length; `:565-571` writes `draft_meta_info.json` from `_build_draft_meta`; `:429-449` derives fps from the first clip and OS from `platform.system()`. Focused: 36 `test_export` tests passed. |
| 049 | P2 | E12 | ????? -c:v libx264 -crf 23, 10-bit ???? High 10 | Fixed | `dbbf3e2` adds explicit `-preset fast` and `-pix_fmt yuv420p` to re-encoded cuts, so 10-bit/HDR sources no longer produce High 10 output. Focused: 98 `test_cut` / `test_tasks_cut` tests passed; Ruff passed. |
| 050 | P2 | R015 | 规划与审阅调用不传 cancel_event, 取消要等模型返回 | Fixed | `452f228` threads `cancel_event` through `refine_text` / `refine_script` and `run_refine_texts` / `run_refine_scripts`; pre-set or mid-call cancellation raises `InterruptedError` instead of waiting for the model or being swallowed as a per-file failure. Focused: 115 `test_analyze_funcs` / `test_tasks_refine` tests passed; full suite 3339 passed, 12 skipped; Ruff passed. |
| 051 | P2 | E12 | 输出不是 JSON 时 extract_json 直接抛出, 不进入重试 | Fixed | `61b46d2` treats an `extract_json` parse failure as a retryable validation issue: the first malformed response is sent back once with a parse-correction prompt, and a second parse failure raises the existing validation error instead of aborting immediately. Focused: 113 `test_analyze_funcs` / i18n tests passed; full suite 3341 passed, 12 skipped; Ruff passed. |
| 052 | P2 | E14 | 过时片段的备份只在全部成功时删除 | Fixed | `c661272` moves stale-artifact cleanup into a `finally` block, so `.clio_bak` files are removed when the cut finishes even after failed segments or cancellation, instead of only when every segment succeeded. Focused: 98 `test_cut` / `test_tasks_cut` tests passed; Ruff passed. |
| 053 | P2 | E15 | 窗口切片的两条重编码回退仍带 -an; 大小上限写死 200 MB | Fixed | `e3d4c03` removes the module-level 200 MB cap from the legacy slicer: `max_bytes` is now required from the caller (intended to come from `video_upload_max_mb`), and both re-encode fallbacks keep a mono 64 kbps AAC track instead of `-an`. Focused: 90 `test_analyze_windows` / `test_tasks_analyze` tests passed; full suite 3341 passed, 12 skipped; Ruff passed. |
| 054 | P2 | E17 | 单视频"全部重跑"仍是 压缩 → 分析 → 口播 → 转录 | Fixed | `b1d24df` now runs `all` in `compress -> transcribe -> analyze -> voiceover` order, with regression coverage for the canonical rerun sequence. Focused: 107 pytest cases passed; Ruff passed. |
| 055 | P2 | E18 | 删除 plan 只删 .json, 留下 .md, 用户编辑版不备份 | Fixed | `94c9375` backs up both `{day}_plan.json` and `{day}_plan.md` before deletion, removes both files, and aborts without deleting anything when backup creation fails. The response lists created backups. Focused: 65 `test_routes_plan` tests passed; full suite 3343 passed, 12 skipped; Ruff passed. |
| 056 | P2 | E19 | 规划默认用推理模型并带 temperature, 超时 120 秒 | Fixed | `61e04ef` adds an optional per-task `ai.tasks.<name>.timeout_sec` (parser, validators, UI field, separate cached provider) and binds `vlog_plan` to 300 s; the shipped examples no longer send a temperature to the reasoning model. Real-account large-material planning was not run here, so the provider-side latency check remains manual. Focused: 188 config/AI pytest cases + 80 Vitest cases passed; full suite 3346 passed, 12 skipped; typecheck, Ruff, and mypy passed. |
| 057 | P2 | R016 | 只读取 prompt_feedback.block_reason; 候选级 finish_reason=SAFETY/RECITATION 且文本为空时仍报"JSON 解析失败" | Fixed | Empty candidates now surface blocking finish reasons (including SAFETY and RECITATION) as `block_reason` instead of falling through to JSON parsing. Focused: 69 Gemini provider tests passed. |
| 058 | P2 | R017 | 只记录 candidates_token_count, 不含思考 token; 官方价目表的输出价包含思考 token | Fixed | Gemini completion usage now sums candidate and thinking tokens while preserving the provider total. Focused: 67 Gemini provider tests passed. |
| 059 | P2 | R019 | 云端转录没有置信度, avg_logprob 取默认 0.0, 在对数尺度上等于"最高置信" | Fixed | `70d887b`: `TranscriptSegment.avg_logprob` is optional; Aliyun segments omit it instead of reporting 0.0, and transcript selection samples evenly across time when no confidence scores exist. Focused: 245 ASR/analyze/transcript-route tests passed; full suite 3346 passed, 12 skipped (the only failure was an unrelated locale copy mismatch, fixed separately). |
| 060 | P2 | R023 | 保存分析/口播时不重写配套的 .txt/.md; 分析保存不保护 _lineage、index 等字段; 口播保存不做任何校验 | Fixed | `6d5bee7` rewrites `.txt`/`.md` sidecars after successful saves, preserves server-owned analysis metadata and voiceover lineage, and validates voiceover payloads. Focused: 101 routes/helper tests passed; full Python suite: 3356 passed, 12 skipped. Ruff and focused mypy passed. |
| 061 | P2 | R030 | 口播步骤遍历 texts 下全部 JSON, 不按 videos.json 过滤 | Pending audit |  |
| 062 | P2 | O16 | 前端覆盖率低 | Satisfied | Satisfied; frontend coverage raised (`5487304`). |
| 063 | P2 | Q13 | 切点按整秒估算 | Satisfied | Satisfied; cut snapping implemented (`0e1c9bf`). |
| 064 | P2 | R012 | ANALYZE_PROMPT 只要求描述"实际能看到"的内容, 没有任何音频字段; Q2 保留的音轨没被用上 | Pending audit |  |
| 065 | P2 | E13 | 截断后用完全相同的请求重试, 视频重新上传 | Pending audit |  |
| 066 | P2 | E16 | 示例仍是 00:10-00:45 (35 秒); narrative_beat/transition 下游未使用 | Pending audit |  |
| 067 | P2 | O4 | 只发一条 user 消息 | Pending audit |  |
| 068 | P2 | R013 | "5-10 秒一条"与"每窗口最多 60 条"在 15 分钟窗口下矛盾 (需要 90-180 条) | Pending audit |  |
| 069 | P2 | R020 | 只要有 1 个视频分析失败, analyze 步骤就抛异常, 后面的口播、规划全部不执行 | Pending audit |  |
| 070 | P2 | C16 | 原生弹窗: plan 编辑无撤销 (部分修复) | Satisfied | Satisfied; native dialog/undo work completed. |
| 071 | P2 | B10 | README 数字与流程过时 | Pending audit |  |
| 072 | P2 | O18 | ROADMAP 过长 | Satisfied | Satisfied; roadmap archival completed. |
| 073 | P2 | R031 | target_size_mb=5 在 800 kbps 保底下对约 46 秒以上的素材不再成立; 日志仍打印"目标 5MB", README 仍写 ~5MB; 短素材码率没有上限 | Pending audit |  |
| 074 | P2 | F8 | 草稿只有视频轨 (部分修复) | Pending audit |  |
| 075 | P2 | F4 | 没有 AI 输出自动质检 | Satisfied | Satisfied; readiness warnings implemented (`9c08ab2`). |
| 076 | P2 | F5 | 没有本地模型 Provider | Pending audit |  |
| 077 | P2 | F9 | 规划模型看不到画面 | Pending audit |  |
| 078 | P2 | O8 | make_handler 过于庞大 | Satisfied | Satisfied; handler split completed (`f6a7b07`, `970d559`, `786ec31`, `961fdb1`). |
| 079 | P2 | O10 | 宽泛异常 | Satisfied | Satisfied; broad exception cleanup completed (`52fab55`). |
| 080 | P2 | O11 | 限流与等待逻辑重复 | Satisfied | Satisfied; limiter merge completed (`cb8613d`). |
| 081 | P2 | O13 | ruff 规则集过窄 | Satisfied | Satisfied; Ruff expansion completed (`ea32a69`, `52fab55`). |
| 082 | P2 | O14 | 大量无类型 JS | Satisfied | Satisfied; frontend split/typecheck completed (`e5903ac`). |
| 083 | P2 | O9 | 全量 mypy 错误 | Satisfied | Satisfied; mypy gate enabled (`d88d141`). |
| 084 | P2 | OPT-003 | 只发一条 user 消息 (clio/ai/openai_compat.py:60-63), 模板、规范和数据混在一起 | Pending audit |  |
| 085 | P2 | OPT-004 | 长视频按窗口切片后逐片上传 (clio/tasks/analyze.py:358-381), N 个窗口上传 N 次, 切片带关键帧预卷 (C10) | Pending audit |  |
| 086 | P2 | OPT-005 | 不设 media_resolution 与 fps (clio/ai/gemini.py:337-348)。官方文档: 每帧 258 token (低分辨率 66), 音频 32 token/秒, 1 fps 采样 | Pending audit |  |
| 087 | P2 | OPT-008 | 口播在规划前按整段素材生成 (Q12), 不知道最终用了哪几秒 | Pending audit |  |
| 088 | P2 | OPT-009 | analyze.use_gpmf 默认 false; 分天用 UTC 日期 (E5) | Pending audit |  |
| 089 | P2 | OPT-013 | 校验重试与截断重试都会重新上传视频 (clio/analyze.py:855-886、clio/ai/gemini.py:287-356) | Pending audit |  |
| 090 | P2 | OPT-014 | analyze.max_workers 默认 1, 20 段素材串行 | Pending audit |  |
| 091 | P2 | OPT-015 | SSE 每个连接每 250 ms 查询一次 SQLite (clio/ui/routes/tasks.py:146-167), 每个标签页 2 条流 | Pending audit |  |
| 092 | P2 | OPT-016 | make_handler 是 680 行闭包 (clio/ui/server.py:328-1009), 内含 Handler 类与 92 条路由 | Pending audit |  |
| 093 | P2 | OPT-017 | 52 个函数圈复杂度 ≥ 20: _build_videos_payload 85、main 80、handle_post_run_start 53、run_cut_all 53、handle_post_rerun 51 | Pending audit |  |
| 094 | P2 | OPT-019 | mypy 全量 470 个错误。非测试代码 82 个, 集中在 tasks/plan.py (16)、ui/routes/videos.py (13) | Pending audit |  |
| 095 | P2 | OPT-020 | ruff 只开 E/F/I/W/UP; 非测试代码有 172 处 except Exception | Pending audit |  |
| 096 | P2 | OPT-021 | clio/config/models.py 末尾保留 4 个标注为过渡用途的旧 dataclass; _plan_index_key 的逻辑在 analyze.py 里另有两处内联副本 (731-734, 743-746) | Pending audit |  |
| 097 | P2 | OPT-024 | 步骤函数返回值各不相同 (list / int / dict / None), pipeline.py:126-130 靠类型猜结果, failed 恒为 0 | Pending audit |  |
| 098 | P2 | OPT-025 | 配置升级靠"缺字段就写默认值" (R009) | Pending audit |  |
| 099 | P2 | OPT-026 | 34 处 confirm() 与 1 处 alert(); pywebview 下原生弹窗样式与行为不一致 | Pending audit |  |
| 100 | P2 | OPT-027 | 分析失败时统一提示"请检查 API Key 和网络连接" (task.analyze.incomplete), 即使原因是 JSON 结构或安全拦截 | Pending audit |  |
| 101 | P2 | OPT-030 | Vitest 语句覆盖 55.78%; editor-plan.js 17%、main.js 28%、viewer.js 38% | Pending audit |  |
| 102 | P2 | OPT-032 | AGENTS.md 411 行、ROADMAP.md 1,826 行、CHANGELOG.md 1,103 行; docs/analysis 27 份、docs/superpowers 109 份 | Pending audit |  |
| 103 | P2 | OPT-033 | README 手写测试数量 (2409 / 720), 与实测 (2870 / 857) 不符 | Pending audit |  |
| 104 | P3 | R036 | 任务指纹包含 provider、model、base_url、max_tokens, 但不包含 temperature | Fixed | Task lineage settings now include the bound task temperature. Focused: 27 plan-lineage tests passed, including a temperature-change fingerprint regression. |
| 105 | P3 | R025 | test_readonly_dir_is_handled_gracefully 以 root 运行时失败 | Pending audit |  |
| 106 | P3 | R035 | 校验重试的修正说明取自界面语言包, 英文界面下中文提示词后面会接一段英文说明 | Pending audit |  |
| 107 | P3 | R021 | 运行预览显示 plan "将跳过", 实际启动却返回 409 要求确认覆盖 | Pending audit |  |
| 108 | P3 | R022 | 同为 API, 部分 403/404 返回 HTML 错误页, 部分返回 JSON | Pending audit |  |
| 109 | P3 | R026 | 锁文件里 69 个包的地址指向 registry.npmmirror.com, 78 个指向 registry.npmjs.org | Pending audit |  |
| 110 | P3 | R027 | "锁定"文件只固定了 4 个顶层包, 没有传递依赖和哈希; pip-audit 是 continue-on-error | Pending audit |  |
| 111 | P3 | R028 | 替换 global_cfg 时只清 _paths 缓存, _ai/_compress/_whisper 仍指向旧对象 | Fixed | Replacing `global_cfg` now invalidates every combined-section cache, not only paths. Focused: 105 config tests passed, including AI/compress/whisper cache replacement. |
| 112 | P3 | R029 | analyze_video 签名与协议不一致, 但 isinstance(provider, VideoAIProvider) 只看方法名, 检查恒为真 | Fixed | Removed the unsupported placeholder from the OpenAI-compatible provider, so a text-only provider no longer satisfies `VideoAIProvider`. Focused: 15 factory and 31 OpenAI-compatible tests passed. |
| 113 | P3 | R034 | compress.target_size_mb 的校验下限是 0.01, 因此 -crf 分支永远走不到, compress.crf 配置不生效 | Fixed | `target_size_mb: 0` is now valid and selects the CRF path; negative values remain rejected. The project example documents the mode. Focused: 106 config and 27 compression tests passed. |
| 114 | P3 | OPT-007 | 规划一次生成 (Q11), 输入是全部素材的完整 timeline | Pending audit |  |
| 115 | P3 | OPT-010 | 分析输出是散文描述 (Q6), 景别、运镜写在 description 里 | Pending audit |  |
| 116 | P3 | OPT-022 | 前端 58 个文件 16,164 行无类型 JS, editor-config.js 2,414 行 | Pending audit |  |
| 117 | P3 | OPT-023 | 产物文件名含 AI 生成的标题 ({index}_{title}), 标题一变就产生孤儿文件 (封面、口播、标号视频) | Pending audit |  |
