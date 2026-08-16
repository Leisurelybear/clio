# 云端 ASR 集成 — 实现方案

> **日期:** 2026-08-16
> **状态:** 设计阶段，待用户确认
> **关联:** 继承自 `2026-06-14-whisper-transcription-plan.md`（本地 Whisper 转录）

## 1. 背景与动机

当前转录完全依赖本地 `faster-whisper`，存在以下痛点：

- **环境脆弱**：需要 cuBLAS DLL、CUDA 运行时、模型下载（1~2 GB），Windows 安装成功率低
- **打包版不可用**：`clio.exe` 无法内置 Whisper 依赖，打包版用户无法转录
- **性能受限**：CPU 转录慢；GPU 转录依赖 NVIDIA 显卡 + 正确的 CUDA 配置
- **模型质量天花板**：faster-whisper 对中文方言、专业术语识别效果有限

引入云端 ASR 服务可以：脱离本地环境依赖、利用大厂高精度模型、开箱即用。

## 2. 设计目标

- **可选可配**：用户在 `project.yaml` 中选择 `local`（faster-whisper）或 `cloud`（API 转录），默认仍为 `local`
- **多服务商**：首期支持阿里云（智能语音交互）、百度智能云（语音识别），预留腾讯云、讯飞等扩展点
- **统一接口**：抽象出 `TranscriptionProvider` 协议，与 AI 那边的 `TextAIProvider` 模式一致
- **零破坏**：现有本地 Whisper 流程完全不变，云端是增量能力
- **配置友好**：API Key 走 `.env`，与 AI Provider 保持一致；Web UI 可配置
- **输出一致**：无论本地还是云端，最终产出的 `_transcript.json` schema 完全一致

## 3. 架构设计

### 3.1 分层总览

```
clio/tasks/transcribe.py        ← pipeline 层（不变，通过 engine 参数分发）
        │
        ▼
clio/transcribe.py              ← 引擎路由层（新增 transcribe_audio 分发逻辑）
        │
        ├── local: faster-whisper（现有逻辑，提取为 LocalWhisperEngine）
        └── cloud: TranscriptionProvider 协议
                │
                ├── clio/asr/
                │   ├── base.py          # TranscriptionProvider Protocol + 数据结构
                │   ├── factory.py       # 按名称构建 provider（仿 ai/factory.py）
                │   ├── aliyun.py       # 阿里云智能语音交互
                │   ├── baidu.py        # 百度语音识别
                │   └── tencent.py      # 腾讯云 ASR（预留，二期）
                │
                └── clio/asr/__init__.py
```

### 3.2 TranscriptionProvider 协议

```python
# clio/asr/base.py

from __future__ import annotations
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from collections.abc import Callable

@dataclass
class TranscriptSegment:
    start: float       # 秒
    end: float
    text: str
    avg_logprob: float = 0.0      # 云端无此字段，默认 0；用于与本地统一 schema

@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    language: str = "zh"
    provider: str = ""            # 标识来源，写入 _transcript.json
    extra: dict | None = None     # 厂商特有字段（如置信度、说话人 ID）

@runtime_checkable
class TranscriptionProvider(Protocol):
    provider_id: str

    def transcribe(
        self,
        audio_path: str,
        language: str = "zh",
        progress_callback: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TranscriptResult: ...

    def close(self) -> None: ...
```

### 3.3 引擎路由

`clio/transcribe.py` 中现有的 `transcribe_audio()` 保留为本地引擎入口，新增 `transcribe_audio_dispatch()`：

```python
def transcribe_audio(
    audio_path: Path,
    config: AppConfig,
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict]:
    engine = config.whisper.engine  # "local" | "cloud"
    if engine == "cloud":
        return _transcribe_cloud(audio_path, config, progress_callback)
    return _transcribe_local(audio_path, config, progress_callback)
```

- `_transcribe_local` = 现有的 faster-whisper 逻辑（重命名，行为不变）
- `_transcribe_cloud` = 调用 `asr.factory.build_provider()`，返回统一 `list[dict]`

### 3.4 Provider 工厂

```python
# clio/asr/factory.py

_PROVIDER_TYPES: dict[str, type[TranscriptionProvider]] = {
    "aliyun": AliyunASRProvider,
    "baidu": BaiduASRProvider,
    "tencent": TencentASRProvider,
}

def build_provider(config: AppConfig) -> TranscriptionProvider:
    engine_cfg = config.whisper.cloud  # CloudASRConfig
    cls = _PROVIDER_TYPES.get(engine_cfg.provider)
    if not cls:
        raise ValueError(f"不支持的云端 ASR 服务商: {engine_cfg.provider}")
    return cls(engine_cfg, config.proxy)
```

## 4. 配置设计

### 4.1 全局配置 (config.yaml)

```yaml
whisper:
  cache_dir: ""
  hf_endpoint: https://hf-mirror.com
  # 云端 ASR 服务商配置（全局，API Key 在 .env 中管理）
  cloud:
    aliyun:
      api_key_env: ALIYUN_ASR_API_KEY
      # app_key: 智能语音交互项目 AppKey（非密钥，可明文配置）
      app_key: ""
    baidu:
      api_key_env: BAIDU_ASR_API_KEY
      secret_key_env: BAIDU_ASR_SECRET_KEY  # 百度需 AK+SK 换 token
```

### 4.2 项目级配置 (project.yaml)

```yaml
whisper:
  engine: local            # local | cloud
  # 以下仅 engine=cloud 时生效
  cloud:
    provider: aliyun       # aliyun | baidu | tencent
    model: ""              # 留空=各厂商默认模型；阿里可选 paraformer-v2 等
    # 厂商特有参数可在此覆盖
  # 以下仅 engine=local 时生效（现有字段不变）
  enabled: true
  model_size: medium
  language: zh
  device: auto
  max_segments_per_clip: 5
  transcripts_subdir: transcripts
```

### 4.3 .env 示例

```env
# 云端 ASR 密钥
ALIYUN_ASR_API_KEY=your_aliyun_nls_token
BAIDU_ASR_API_KEY=your_baidu_api_key
BAIDU_ASR_SECRET_KEY=your_baidu_secret_key
```

### 4.4 Config Dataclass 变更

```python
# clio/config/models.py 新增

@dataclass
class AliyunASRConfig:
    api_key_env: str = "ALIYUN_ASR_API_KEY"
    app_key: str = ""

@dataclass
class BaiduASRConfig:
    api_key_env: str = "BAIDU_ASR_API_KEY"
    secret_key_env: str = "BAIDU_ASR_SECRET_KEY"

@dataclass
class GlobalCloudASRConfig:
    aliyun: AliyunASRConfig = field(default_factory=AliyunASRConfig)
    baidu: BaiduASRConfig = field(default_factory=BaiduASRConfig)

# GlobalWhisperConfig 新增字段
@dataclass
class GlobalWhisperConfig:
    cache_dir: str = ""
    hf_endpoint: str = ""
    cloud: GlobalCloudASRConfig = field(default_factory=GlobalCloudASRConfig)

# ProjectWhisperConfig 新增字段
@dataclass
class ProjectWhisperConfig:
    engine: str = "local"           # "local" | "cloud"
    cloud: ProjectCloudASRConfig = field(default_factory=ProjectCloudASRConfig)
    # ... 现有字段不变 ...

@dataclass
class ProjectCloudASRConfig:
    provider: str = "aliyun"
    model: str = ""
```

## 5. 云端 Provider 实现细节

### 5.1 阿里云智能语音交互

**SDK 选择：** `dashscope` SDK（阿里官方 Python SDK，支持 Paraformer 录音文件识别）

**认证流程：**
- API Key → `DASHSCOPE_API_KEY` 环境变量（或显式传参）
- 录音文件识别：上传音频 URL 或本地文件，异步轮询结果

**实现要点：**
- 本地 WAV 文件需先上传到 OSS 或使用 DashScope 的文件上传接口
- `paraformer-v2` 模型支持时间戳输出，可直接映射为 `TranscriptSegment`
- 轮询模式：提交任务 → 每 5s 轮询 → 完成后提取 segments
- 支持 `cancel_event` 中断轮询

```python
# clio/asr/aliyun.py（伪代码）

class AliyunASRProvider:
    provider_id = "aliyun"

    def transcribe(self, audio_path, language, progress_callback, cancel_event):
        import dashscope
        dashscope.api_key = os.environ[self._cfg.api_key_env]

        task = dashscope.audio.asr.Transcription.async_call(
            model=self._cfg.model or "paraformer-v2",
            file_urls=[self._upload(audio_path)],
            language_hints=[language],
            parameters={"output_format": "json"},
        )
        # 轮询...
        result = dashscope.audio.asr.Transcription.wait(
            task_id=task.output.task_id,
            progress_callback=progress_callback,
        )
        return self._parse_result(result)
```

### 5.2 百度智能云语音识别

**SDK 选择：** `baidu-aip` SDK 或直接 HTTP API（REST）

**认证流程：**
- API Key + Secret Key → 换取 access_token（有效期 30 天，需缓存）
- 录音文件识别：POST 音频数据到百度 API

**实现要点：**
- 百度录音文件识别 API 支持直接 POST base64 编码音频或文件 URL
- 返回结果包含时间戳和文本，映射为 `TranscriptSegment`
- access_token 缓存到内存，过期前自动刷新
- 速率限制：百度有 QPS 限制（默认 2 QPS），需在 provider 层加限速

```python
# clio/asr/baidu.py（伪代码）

class BaiduASRProvider:
    provider_id = "baidu"

    def __init__(self, cfg, proxy):
        self._cfg = cfg
        self._token = None
        self._token_expires = 0

    def _ensure_token(self):
        if time.time() < self._token_expires - 300:
            return self._token
        # 调用 https://aip.baidubce.com/oauth/2.0/token 换 token
        ...
        return self._token

    def transcribe(self, audio_path, language, progress_callback, cancel_event):
        token = self._ensure_token()
        # POST 音频到百度 ASR API
        ...
        return self._parse_result(resp)
```

### 5.3 腾讯云 ASR（二期预留）

- **SDK 选择：** `tencentcloud-sdk-python` 的 `asr` 模块
- **认证流程：** SecretId + SecretKey → 签名鉴权
- 与阿里类似，录音文件识别为异步轮询模式

## 6. 音频上传策略

云端 ASR 需要音频文件可达。有三种策略：

| 策略 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **SDK 文件上传** | 阿里 DashScope | 无需额外存储 | 依赖 SDK 上传能力 |
| **本地 HTTP 服务** | 已有 web server 运行 | 复用现有基础设施 | 云端需能访问本机 IP，公网不可达 |
| **OSS/对象存储** | 生产环境 | 最可靠 | 需用户配置存储桶 |

**首期方案：** 阿里用 SDK 文件上传（最简）；百度用 POST 音频数据（直接上传）；不引入对象存储依赖。如用户需要更可靠方案，二期再加 OSS 支持。

## 7. Web UI 变更

### 7.1 配置面板 (editor-config.js)

- **Whisper 配置区块** 新增 `engine` 选择器（单选：本地 / 云端）
- 选择 `cloud` 后，展开云端 provider 下拉选择（阿里 / 百度）
- 全局配置区新增「云端 ASR 密钥」配置卡片：
  - 阿里：API Key 环境变量名 + AppKey 输入
  - 百度：API Key + Secret Key 环境变量名
- 现有 whisper model management 区块在 `engine=cloud` 时隐藏

### 7.2 API 端点

复用现有 `PUT /api/config/project` 保存 `whisper.engine` 和 `whisper.cloud` 配置。
复用现有 `PUT /api/env` 保存云端 ASR 密钥。
无需新增 API 路由。

### 7.3 前端检测提示

- `engine=cloud` 时，调用 `GET /api/whisper/check` 增加 cloud 检测（API Key 是否已配置）
- 转录任务运行时，进度消息区分「本地 Whisper 转录中...」/「云端 {provider} 转录中...」

## 8. 输出 Schema 兼容性

`_transcript.json` 输出格式保持不变，新增可选字段：

```json
{
  "source_video": "001.mp4",
  "source_stem": "001",
  "language": "zh",
  "model_size": "medium",          // local 时有值；cloud 时为 cloud provider 名
  "engine": "cloud",                // 新增：local | cloud
  "provider": "aliyun",             // 新增（cloud 时有值）
  "segments": [
    {
      "start": 0.5,
      "end": 3.2,
      "text": "大家好",
      "avg_logprob": 0.0            // cloud 时固定 0.0，不作为质量参考
    }
  ],
  "generated_at": "2026-08-16T..."
}
```

下游消费者（`enrich_matching_analysis_files`、prompt 注入等）只读 `segments`，不受影响。

## 9. 实施计划

### Phase 1: 核心抽象 + 本地引擎重构（不引入云端依赖）

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 1.1 | 新建 `clio/asr/` 包，定义 `TranscriptionProvider` 协议 + 数据结构 | `clio/asr/base.py` | [ ] |
| 1.2 | 重构 `transcribe_audio()`：提取本地逻辑为内部函数，新增 engine 分发 | `clio/transcribe.py` | [ ] |
| 1.3 | 扩展 config dataclass：`engine`、`cloud` 配置 | `clio/config/models.py` | [ ] |
| 1.4 | 更新 config example 文件 | `config.example.yaml`, `docs/project.example.yaml` | [ ] |
| 1.5 | 更新 `.env.example` | `.env.example` | [ ] |
| 1.6 | 单元测试：config 解析、engine 路由分发（mock cloud provider） | `clio/tests/test_asr.py` | [ ] |

### Phase 2: 阿里云 Provider 实现

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 2.1 | 实现 `AliyunASRProvider`：DashScope SDK 集成 | `clio/asr/aliyun.py` | [ ] |
| 2.2 | 实现工厂 `build_provider()` | `clio/asr/factory.py` | [ ] |
| 2.3 | 端到端测试（mock SDK 响应） | `clio/tests/test_asr_aliyun.py` | [ ] |
| 2.4 | `requirements.txt` 新增 `dashscope`（可选依赖） | `requirements.txt` | [ ] |

### Phase 3: 百度 Provider 实现

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 3.1 | 实现 `BaiduASRProvider`：HTTP API + token 管理 | `clio/asr/baidu.py` | [ ] |
| 3.2 | 端到端测试（mock HTTP 响应） | `clio/tests/test_asr_baidu.py` | [ ] |
| 3.3 | 文档：百度 AI 平台配置指南 | `docs/asr-baidu-setup.md` | [ ] |

### Phase 4: Web UI 适配

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 4.1 | `editor-config.js` 新增 engine 选择器 + 云端 provider 配置卡片 | `clio/ui/static/src/editor-config.js` | [ ] |
| 4.2 | whisper check API 增加 cloud 密钥检测 | `clio/ui/routes/whisper_check.py` | [ ] |
| 4.3 | 转录进度文案适配（区分本地/云端） | `clio/tasks/transcribe.py` | [ ] |
| 4.4 | 前端测试 | `clio/ui/static/src/__tests__/` | [ ] |

### Phase 5: 文档 + 收尾

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 5.1 | 更新 AGENTS.md 目录结构和技术栈 | `AGENTS.md` | [ ] |
| 5.2 | 更新 README 转录配置说明 | `README.md` | [ ] |
| 5.3 | 更新 ROADMAP.md 新增 R-043 | `ROADMAP.md` | [ ] |
| 5.4 | 配置迁移：自动升级旧 config 兼容 `engine` 默认 `local` | `clio/config/loader.py` | [ ] |

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 阿里 SDK 文件上传不支持大文件 | 大视频音频上传失败 | 限制音频大小或二期引入 OSS |
| 百度 access_token 并发竞争 | 多线程转录时 token 刷新冲突 | provider 层加锁 |
| 云端 ASR 返回时间戳精度不一致 | segments 时间戳对齐偏差 | 在 provider 层统一四舍五入到 2 位小数 |
| 网络超时/服务不可用 | 转录任务失败 | provider 层重试（3 次，指数退避）+ 友好错误提示 |
| 可选依赖缺失 | 用户未装 dashscope | 运行时 ImportError → 提示安装命令 |
| 成本可控性 | 用户不清楚 API 调用费用 | 文档说明各厂商计费方式 + 转录前预估音频时长 |

## 11. 依赖管理

云端 ASR 依赖设为**可选依赖**，不影响核心安装：

```python
# transcribe_audio() 中的导入保护

def _transcribe_cloud(...):
    try:
        from clio.asr.factory import build_provider
    except ImportError as e:
        raise RuntimeError(
            f"云端 ASR 依赖未安装。请执行: pip install dashscope  (阿里云)\n"
            f"或直接用 httpx 调用百度 API（无需额外依赖）"
        ) from e
    ...
```

- `dashscope` 加入 `requirements.txt`（注释标注为可选）
- 百度使用 `httpx`（已是项目依赖），无需额外安装

## 12. 里程碑

| 里程碑 | 内容 | 预估工时 |
|--------|------|---------|
| M1 | Phase 1 完成：核心抽象就绪，本地引擎不受影响 | 2-3h |
| M2 | Phase 2 完成：阿里云可用，端到端可转录 | 3-4h |
| M3 | Phase 3 完成：百度可用 | 2-3h |
| M4 | Phase 4+5 完成：UI + 文档 | 2-3h |
| **合计** | | **9-13h** |

## 13. 未来扩展

- **腾讯云 ASR**：`tencentcloud-sdk-python`，与阿里类似
- **讯飞听见**：REST API，支持离线转写
- **OpenAI Whisper API**：`whisper-1` 模型，OpenAI 兼容接口
- **自动 fallback**：云端失败时自动回退到本地 Whisper
- **批量并发**：多视频并发提交云端转录
- **说话人分离**：集成 diarization 能力（部分厂商支持）
