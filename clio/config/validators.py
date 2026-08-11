import logging

from clio.config.models import CANVAS_PRESETS, AppConfig, GlobalConfig

logger = logging.getLogger("clio.config")

_SUPPORTED_PROVIDER_TYPES = {"gemini", "openai", "openai_compat"}


def _filter_dc(raw: dict, dc: type) -> dict:
    fields = set()
    if hasattr(dc, "__dataclass_fields__"):
        fields = {f.name for f in dc.__dataclass_fields__.values()}
    return {k: v for k, v in raw.items() if k in fields}


def _require_finite(field_name: str, value: int | float) -> None:
    import math

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got: {value}")


def _require_min(field_name: str, value: int | float, minimum: int | float) -> None:
    _require_finite(field_name, value)
    if value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}, got: {value}")


def _require_max(field_name: str, value: int | float, maximum: int | float) -> None:
    _require_finite(field_name, value)
    if value > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}, got: {value}")


def _require_positive(field_name: str, value: int | float) -> None:
    _require_finite(field_name, value)
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0, got: {value}")


def _require_choice(field_name: str, value: str, choices: tuple[str, ...]) -> None:
    if value not in choices:
        available = ", ".join(choices)
        raise ValueError(f"{field_name} must be one of {available}, got: {value}")


def _require_range(field_name: str, value: int | float, minimum: int | float, maximum: int | float) -> None:
    _require_finite(field_name, value)
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be in [{minimum}, {maximum}], got: {value}")


def _require_supported_provider_type(provider_name: str, provider_type: str) -> None:
    if provider_type not in _SUPPORTED_PROVIDER_TYPES:
        available = ", ".join(sorted(_SUPPORTED_PROVIDER_TYPES))
        raise ValueError(
            f"ai.providers.{provider_name}.type = '{provider_type}' 不受支持。可选 provider type: {available}。"
        )


def _require_video_provider_compatible(task_name: str, provider_name: str, provider_type: str) -> None:
    if task_name == "video_analyze" and provider_type != "gemini":
        raise ValueError(
            "ai.tasks.video_analyze.provider 必须绑定 gemini 类型 provider."
            f"'{provider_name}' 当前类型为 '{provider_type}'。"
        )


def _warn_unknown_model(task_name: str, provider_name: str, task_model: str, provider_models: list[str]) -> None:
    """Soft warning: a bound model outside the provider's declared list is not fatal.

    Third-party gateways accept arbitrary model names (e.g. `deepseek-v4-flash`
    on custom proxies), and old projects may bind models that a newer
    config.example.yaml no longer lists. Downgraded from a hard load failure
    (R-040 F-2) so upgrades never crash; the UI still offers a curated dropdown.
    """
    if provider_models and task_model not in provider_models:
        available = ", ".join(provider_models)
        logger.warning(
            "ai.tasks.%s.model = '%s' 不在 ai.providers.%s.models 中: %s。"
            "如该模型可用，请把模型名加入 provider 的 models 列表。",
            task_name,
            task_model,
            provider_name,
            available,
        )


def _validate_config(config: AppConfig) -> None:
    if config.proxy.enabled and not config.proxy.url:
        raise ValueError("proxy.enabled=true 但 proxy.url 为空。请填写 proxy.url，或把 proxy.enabled 改成 false。")
    _require_min("analyze.max_workers", config.analyze.max_workers, 1)
    _require_min("analyze.max_analyze_duration_min", config.analyze.max_analyze_duration_min, 0)
    _require_min("analyze.window_max_min", config.analyze.window_max_min, 1)
    _require_min("analyze.window_overlap_sec", config.analyze.window_overlap_sec, 0)
    if config.analyze.window_overlap_sec >= config.analyze.window_max_min * 60:
        raise ValueError(
            f"analyze.window_overlap_sec 必须小于 window_max_min×60，"
            f"当前 overlap={config.analyze.window_overlap_sec}，"
            f"window_max_min={config.analyze.window_max_min}"
        )
    _require_min("compress.target_size_mb", config.compress.target_size_mb, 0.01)
    _require_min("compress.max_width", config.compress.max_width, 1)
    _require_min("compress.fps", config.compress.fps, 1)
    _require_min("compress.crf", config.compress.crf, 0)
    _require_max("compress.crf", config.compress.crf, 51)
    _require_min("script.target_words", config.script.target_words, 1)
    _require_min("plan.max_clips_per_day", config.plan.max_clips_per_day, 1)
    _require_min("plan.target_duration_sec", config.plan.target_duration_sec, 1)
    _require_min("naming.index_width", config.naming.index_width, 1)
    _require_min("ai.provider_ttl_min", config.ai.provider_ttl_min, 0)
    if config.export.canvas_ratio not in CANVAS_PRESETS:
        available = ", ".join(CANVAS_PRESETS)
        raise ValueError(f"export.canvas_ratio must be one of {available}, got: {config.export.canvas_ratio}")
    if config.export.output_subdir != "export":
        raise NotImplementedError("export.output_subdir 当前未实现，请保持默认值 'export' 或留空")
    _require_choice("preview.subtitles.mode", config.preview.subtitles.mode, ("auto", "multi", "scroll"))
    _require_min("preview.subtitles.max_lines", config.preview.subtitles.max_lines, 1)
    _require_min("preview.subtitles.max_len_per_line", config.preview.subtitles.max_len_per_line, 1)
    _require_min("preview.subtitles.min_font_size", config.preview.subtitles.min_font_size, 4)
    _require_min("preview.subtitles.font_size", config.preview.subtitles.font_size, 4)
    if config.preview.subtitles.font_size < config.preview.subtitles.min_font_size:
        raise ValueError(
            f"preview.subtitles.font_size ({config.preview.subtitles.font_size}) "
            f"must be >= min_font_size ({config.preview.subtitles.min_font_size})"
        )
    _require_min("preview.subtitles.scroll_speed", config.preview.subtitles.scroll_speed, 0)
    _require_max("preview.subtitles.scroll_speed", config.preview.subtitles.scroll_speed, 500)
    _require_range("preview.subtitles.pos_x", config.preview.subtitles.pos_x, 0, 100)
    _require_range("preview.subtitles.pos_y", config.preview.subtitles.pos_y, 0, 100)
    if config.project_cfg is not None:
        config.project_cfg.whisper.sanitize()

    provider_names = set(config.ai.providers)
    for provider_name, provider_cfg in config.ai.providers.items():
        _require_supported_provider_type(provider_name, provider_cfg.type)
        _require_min(f"ai.providers.{provider_name}.requests_per_minute", provider_cfg.requests_per_minute, 0)
        _require_min(f"ai.providers.{provider_name}.retry_attempts", provider_cfg.retry_attempts, 0)
        # 0 means unlimited; only reject negative values
        _require_min(f"ai.providers.{provider_name}.max_tokens", provider_cfg.max_tokens, 0)
        _require_positive(f"ai.providers.{provider_name}.timeout_sec", provider_cfg.timeout_sec)
        if provider_cfg.type == "gemini":
            _require_positive(f"ai.providers.{provider_name}.poll_interval_sec", provider_cfg.poll_interval_sec)

    for task_name, task_cfg in config.ai.tasks.items():
        if task_cfg.provider not in provider_names:
            available = ", ".join(sorted(provider_names)) or "<无>"
            raise ValueError(
                f"ai.tasks.{task_name}.provider = '{task_cfg.provider}' 不存在。已配置的 provider: {available}。"
            )

        provider_cfg = config.ai.providers[task_cfg.provider]
        _require_video_provider_compatible(task_name, task_cfg.provider, provider_cfg.type)
        _warn_unknown_model(task_name, task_cfg.provider, task_cfg.model, provider_cfg.models)


def validate_global_config(config: GlobalConfig) -> None:
    """Validate global-layer config only (no project tasks). Numeric floors match _validate_config (>= 0)."""
    if config.proxy.enabled and not config.proxy.url:
        raise ValueError("proxy.enabled=true 但 proxy.url 为空。请填写 proxy.url，或把 proxy.enabled 改成 false。")
    _require_min("ai.provider_ttl_min", config.ai.provider_ttl_min, 0)
    _require_min("naming.index_width", config.naming.index_width, 1)
    _require_min("compress.fps", config.compress.fps, 1)
    _require_min("compress.crf", config.compress.crf, 0)
    _require_max("compress.crf", config.compress.crf, 51)
    for provider_name, provider_cfg in config.ai.providers.items():
        _require_supported_provider_type(provider_name, provider_cfg.type)
        _require_min(f"ai.providers.{provider_name}.requests_per_minute", provider_cfg.requests_per_minute, 0)
        _require_min(f"ai.providers.{provider_name}.retry_attempts", provider_cfg.retry_attempts, 0)
        _require_min(f"ai.providers.{provider_name}.max_tokens", provider_cfg.max_tokens, 0)
        _require_positive(f"ai.providers.{provider_name}.timeout_sec", provider_cfg.timeout_sec)
        if provider_cfg.type == "gemini":
            _require_positive(f"ai.providers.{provider_name}.poll_interval_sec", provider_cfg.poll_interval_sec)
