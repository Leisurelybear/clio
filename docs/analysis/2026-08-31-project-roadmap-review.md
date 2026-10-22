# Clio 项目与 ROADMAP 迭代建议

**审查日期：** 2026-08-31  
**审查性质：** 基于当前代码、测试结果和 `ROADMAP.md` 的迭代规划总结，不替代针对单个模块的专项安全审计。  
**关联文档：** `ROADMAP.md`、`docs/analysis/2026-08-16-full-project-code-review.md`

## 1. 总体判断

项目已经从功能原型进入可用产品维护期。当前已经形成较完整的本地单用户闭环：

```text
选择项目与视频
  -> ffmpeg 压缩
  -> Gemini / OpenAI-compatible provider 分析
  -> 转录与口播
  -> plan 编辑
  -> cut / JianYing 导出
```

Task Center、桌面壳、通知中心、配置分层、媒体 identity 和多轮可靠性修复已经覆盖了主要基础设施。下一阶段的重点不应继续平铺更多功能，而应集中解决三件事：

1. 让依赖、任务状态和失败结果可信且可恢复。
2. 让云端 ASR、cut/export 和桌面发行版形成稳定的用户闭环。
3. 降低 ROADMAP、类型债务和大型前端模块带来的维护成本。

## 2. 当前验证快照

本轮本地验证结果：

| 检查 | 结果 | 备注 |
| --- | --- | --- |
| `python -m pytest clio/tests/ -q` | `1990 passed, 12 skipped` | 本机 Python 3.10.6 |
| `npm test -- --run` | `50 files passed, 509 tests passed` | Vitest |
| 工作区 | 仅有 `clio/tests/test_config_descriptions.py` 未提交修改 | 属于已有用户改动 |

项目声明支持 Python `>=3.11`，因此本机通过不能替代 Python 3.11/3.12 以及 Windows、macOS、Linux CI 矩阵。后续涉及发布或运行时兼容的改动，必须以 CI 矩阵为准。

## 3. 当前主要风险与机会

### 3.1 云端 ASR 是最高价值的产品化迭代

`ROADMAP.md` 中 R-047 仍标为 Planned，但代码和测试已经出现 ASR provider 抽象、Aliyun provider 以及 engine switch 相关实现。首先应做一次状态校准，区分“已经实现”和“仍需交付”的部分。

建议将 R-047 先收敛到 Aliyun 的完整生产路径，再决定是否增加 Baidu 等第二个云厂商。需要覆盖：

- local/cloud engine 配置迁移，默认值保持向后兼容；
- 云端请求的超时、重试、取消和网络失败状态；
- 与本地引擎相同的 `_transcript.json` 输出契约；
- API key 只存放在 `.env`，不进入任务历史、日志或 HTTP 响应；
- 无 CUDA、无本地 Whisper 模型的桌面环境仍可完成转录。

云端 ASR 直接降低桌面发行版的首要失败率，优先级高于新增更多编辑器功能。

### 3.2 ffmpeg 与媒体输出需要从“提示”升级为“前置保证”

R-028b/c 和 R-032e 仍代表桌面端的最后一公里：用户可能已经打开应用，却在压缩、裁剪、波形或转录真正开始时才发现 ffmpeg 不可用。

推荐顺序：

```text
ffmpeg 探测与安装体验
  -> 任务提交前 preflight
  -> cut/export 原子产物写入
  -> Plan 优先使用 cut/concat 结果预览
  -> 桌面发行版安装器、签名和发布 smoke test
```

运行前应明确列出缺失的二进制、受影响的步骤和修复入口。安装或分发方案必须保持“显式用户操作”，不能在 serve 启动时静默下载。

### 3.3 Task Center 需要更多真实生命周期验证

Task Center 已经是后台执行的核心基础设施，但其回归成本主要集中在跨请求、跨页面和服务重启边界。下一轮测试应优先覆盖：

- 服务重启后直接 retry 历史任务；
- POST 返回 `task_id` 后才建立 SSE；
- 快速完成、快速失败和取消与终态同时发生；
- SSE 断线重连、`Last-Event-ID` 和重复 `seq`；
- 多标签页同时打开、取消、重试；
- 详情页事件时间线实时追加且去重；
- cleanup 与 notification retention 的交互。

至少应有一组真实 HTTP 生命周期测试，不能只依赖 handler 或 EventSource mock。验收重点是：任何终态都能关闭 loading，任务状态与实际产物一致，断线后不会漏事件。

### 3.4 维护性债务已经成为下一阶段的主要成本

当前最大的维护性问题不是缺少抽象，而是历史状态和大型模块仍然分散：

- `ROADMAP.md` 超过千行，已完成事项、旧 review、当前 backlog 混杂；
- 全量 mypy 仍有未纳入门禁的类型债务；
- `editor-config.js`、`editor-plan.js`、`runner.js` 等前端模块过大；
- 部分旧文档、包名和支持矩阵可能落后于当前实现。

建议把精力投入到可验证的边界：API client、状态模型、渲染器、事件适配器和请求取消策略，而不是继续扩大单文件职责。

## 4. ROADMAP 校准建议

ROADMAP 当前最需要的是一次 reconciliation，而不是再增加更多条目：

1. 逐项检查所有 `[ ]`、`[~]` 和“Open”条目，对照代码、测试和 git log。
2. 将已经有实现和测试覆盖的 R-047 子阶段标为完成，只保留真正未交付的工作。
3. 将已关闭的 R-001 至 R-043、旧 bug tracker 和早期 review 结果移到 `docs/archive/`，保留索引和最终结论。
4. 顶部只保留未来一到两个 sprint 的 Active work；Deferred 和 Decision log 独立成区。
5. 将 `optimization-plan.md` 更新为当前维护 backlog，不再重复记录已经完成的 server/config/provider 重构。

建议保留的当前主线为：云端 ASR、ffmpeg/桌面发布、Task Center 生命周期、媒体导出可靠性、类型和前端维护性。

## 5. 推荐迭代顺序

### Sprint A：ASR 与运行前置检查

- 完成 Aliyun ASR 的生产化闭环；
- 明确 local/cloud engine 的配置迁移和兼容策略；
- 在任务提交前执行 ffmpeg、ASR、provider preflight；
- 统一错误码、取消和重试语义；
- 同步 README、桌面说明和配置示例。

**完成门槛：** 没有 CUDA 或本地 Whisper 环境的机器可以完成云端转录；失败、超时、取消和重试都能在 Task Center 中解释清楚。

### Sprint B：Task Center 一致性与恢复

- 增加真实 HTTP/SSE 生命周期测试；
- 统一 snapshot-first subscription；
- 覆盖断线重连、`Last-Event-ID`、重复事件和跨标签页操作；
- 详情页实时追加事件并限制事件数量；
- 验证 cleanup、通知保留和重启恢复。

**完成门槛：** 终态不丢失，事件不重复、不漏发；重启后历史任务可直接 retry；任何任务状态都能对应到实际产物或明确错误。

### Sprint C：媒体输出与桌面发布

- 统一 JianYing/cut/export 的 atomic artifact writer；
- 将 R-031b 接入 cut/concat 预览；
- 完成 ffmpeg 分发或显式安装流程；
- 增加 Windows 安装器、签名和升级说明；
- 建立无开发环境的桌面发布 smoke test。

**完成门槛：** 进程中断不会留下半写草稿；桌面用户能在首次启动时知道依赖状态，并能完成一条从导入到导出的主流程。

### Sprint D：维护性偿债

- 每轮将 5 至 10 个稳定模块纳入 mypy scope，并禁止新增错误；
- 拆分大型前端模块的 API client、view model、renderer 和 event adapter；
- 为异步请求统一 `AbortController` 或 latest-wins 策略；
- 统一 loading、empty、error 状态和可访问性测试；
- 建立输入扫描、压缩、AI、ASR、plan readiness、cut、JianYing、重启恢复的端到端矩阵。

## 6. 暂缓事项

以下事项有价值，但不应排在上面四条主线之前：

- R-024 GPMF telemetry 深化；
- R-027e 历史日志浏览；
- R-042 桌面 OS 通知；
- 同时接入多个新的云 ASR 厂商；
- 更复杂的 Prompt 管理和多模型比较；
- 全面替换 stdlib HTTP server。

这些项目不会像 ASR 可用性、ffmpeg 依赖、任务状态和导出可靠性一样直接决定主流程成功率。

## 7. 结论

下一阶段的产品目标应明确为：**可靠的本地单用户媒体处理工作台**。优先让每次运行的依赖、状态、产物和失败原因都可信、可恢复、可解释，再扩大功能面。完成 Sprint A 至 C 后，项目才适合继续投入更复杂的自动化剪辑和多模型能力。
