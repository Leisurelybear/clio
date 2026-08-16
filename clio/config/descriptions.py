"""Chinese descriptions for every config field. Used by UI to show tooltips."""

CONFIG_DESCRIPTIONS: dict[str, str] = {
    # paths
    "paths.project_dir": "项目目录，包含 videos.json 和 project.yaml 等配置",
    "paths.output_dir": "所有输出文件（压缩、转录、文案等）的根目录",
    "paths.ffmpeg": "ffmpeg 可执行文件路径，留空则自动搜索",
    "paths.ffprobe": "ffprobe 可执行文件路径，留空则自动搜索",
    "paths.logs_dir": "日志目录，按小时切文件：YYYY-MM-DD-HH.log",
    # proxy
    "proxy.enabled": "是否启用代理（访问 Gemini 等需要）",
    "proxy.url": "代理地址，如 socks5://localhost:1080",
    # ai
    "ai.context": "项目特定背景信息，每次 AI 调用前自动注入到提示词前面",
    "ai.providers": "AI 厂商配置列表，每个厂商可独立设置 API 密钥、地址等",
    "ai.tasks": "AI 任务配置，每个步骤可指定使用哪个厂商和模型",
    # ai.providers.*
    "ai.providers.{name}.type": "AI 厂商类型：gemini（多模态视频理解）或 openai（纯文本兼容接口）",
    "ai.providers.{name}.api_key_env": "API 密钥的环境变量名（如 GEMINI_API_KEY），而非密钥本身",
    "ai.providers.{name}.api_key": "API 密钥（直接填入，优先级低于 api_key_env）",
    "ai.providers.{name}.base_url": "API 基础地址，OpenAI 兼容接口需要填写",
    "ai.providers.{name}.poll_interval_sec": "Gemini 文件处理状态轮询间隔（秒）",
    "ai.providers.{name}.retry_attempts": "额外重试次数（默认 2，总计尝试 3 次）",
    "ai.providers.{name}.timeout_sec": "OpenAI 兼容接口的 HTTP 超时时间（秒），默认 120",
    "ai.providers.{name}.max_tokens": "单次生成最大 token 数；0 = 不限制（由模型/服务端决定，规划类任务推荐）",
    "ai.providers.{name}.models": "该厂商支持的模型名称列表（如 gemini-2.5-flash），用于任务绑定的下拉选择",
    "ai.providers.{name}.capabilities": "能力标签列表。video 表示可做视频理解，text 表示可做文本生成",
    "ai.providers.{name}.requests_per_minute": "每分钟最多调用次数，0 为不限流",
    # ai.tasks.*
    "ai.tasks.{name}.provider": "此任务使用的 AI 厂商名称（在 providers 中定义）",
    "ai.tasks.{name}.model": "模型名称，如 gemini-2.5-flash、deepseek-chat",
    "ai.debug_print_prompt": "调试用：设为 true 时在每次 AI 调用前打印完整 prompt 到控制台（含上下文注入）",
    "ai.provider_ttl_min": "已缓存的 AI 提供商过期间隔（分钟），超时后自动关闭重建。0 为永不过期",
    # compress
    "compress.target_size_mb": "压缩后目标文件大小（MB）",
    "compress.max_width": "压缩后视频最大宽度（像素），高度按比例缩放",
    "compress.fps": "压缩后视频帧率",
    "compress.codec": "视频编码器，默认 libx264",
    "compress.crf": "CRF 压缩质量（0-51，越小质量越高，文件越大）",
    "compress.remove_audio": "是否移除音频（压缩后仅保留画面，可减小体积）",
    # analyze
    "analyze.compressed_subdir": "压缩视频存放的子目录名",
    "analyze.texts_subdir": "AI 分析结果（文案）存放的子目录名",
    "analyze.skip_existing": "全局跳过开关：跳过已处理的文件（影响所有步骤）",
    "analyze.max_analyze_duration_min": "整片硬顶（分钟），超过则跳过分析。0 不限制。长片主要由 window_max_min 控制",
    "analyze.window_max_min": "AI 分析单窗最长分钟数；超过则临时切片多窗分析后合并",
    "analyze.window_overlap_sec": "相邻分析窗重叠秒数，用于边界去重",
    "analyze.max_workers": "AI 分析并发数（ThreadPoolExecutor），1=串行",
    "analyze.use_gpmf": "可选：将原片旁 .gpmf.json / GPMF 探测摘要注入 AI 分析上下文（默认关闭；无 GPS 时静默跳过）",
    # naming
    "naming.index_width": "文件名中索引编号的位数（如 3 表示 001）",
    # script
    "script.scripts_subdir": "口播文案存放的子目录名",
    "script.template_file": "口播文案模板文件路径",
    "script.target_words": "单条口播文案的字数上限；短素材会按 timeline 时长自动缩短",
    # plan
    "plan.plans_subdir": "剪辑规划存放的子目录名",
    "plan.max_clips_per_day": "每日 vlog 最大片段数",
    "plan.target_duration_sec": "每日 vlog 目标时长（秒）",
    "plan.use_transcripts": "规划时是否注入语音转录内容作为参考",
    # whisper
    "whisper.enabled": "是否启用语音转录（需安装 faster-whisper）",
    "whisper.model_size": "Whisper 模型大小。small（快速）、medium（平衡）、large-v3（高精度）",
    "whisper.language": "转录语言。zh（中文）、en（英文）、auto（自动检测）",
    "whisper.device": "计算设备。auto（自动）、cpu（CPU）、cuda（GPU）",
    "whisper.max_segments_per_clip": "每段视频最多取前 N 条转录结果注入规划",
    "whisper.cache_dir": "Whisper 模型缓存目录，留空使用程序默认路径",
    "whisper.transcripts_subdir": "转录结果存放的子目录名",
    "whisper.hf_endpoint": "HuggingFace 镜像地址。国内推荐 hf-mirror.com，留空用官方",
    # preview.subtitles
    "preview.subtitles.enabled": "plan 预览是否显示字幕",
    "preview.subtitles.mode": "字幕显示模式: auto | multi | scroll",
    "preview.subtitles.max_lines": "多行模式下最多同时显示行数",
    "preview.subtitles.max_len_per_line": "每行最大字数",
    "preview.subtitles.min_font_size": "自动缩字号时最小字号 (px)",
    "preview.subtitles.scroll_speed": "滚动模式下滚动速度 (px/s)",
    "preview.subtitles.font_size": "字幕字号 (px)",
    "preview.subtitles.font_family": "字幕字体族 (空=跟随系统)",
    "preview.subtitles.font_color": "字幕文字颜色",
    "preview.subtitles.background": "字幕背景 (rgba)",
    "preview.subtitles.outline": "字幕描边样式",
    "preview.subtitles.pos_x": "字幕水平位置 (占播放器宽 %，0~100)",
    "preview.subtitles.pos_y": "字幕垂直偏移 (自播放器底部 %，0=底部)",
}
