from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clio.config.enums import WhisperDevice, WhisperLang, WhisperModelSize

# ---------------------------------------------------------------------------
# Shared / unchanged dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProxyConfig:
    enabled: bool = False
    url: str = ""


@dataclass
class ProviderConfig:
    name: str
    type: str
    api_key: str = ""
    api_key_env: str = ""
    base_url: str = ""
    poll_interval_sec: int = 5
    retry_attempts: int = 2
    requests_per_minute: int = 0
    timeout_sec: float = 120.0
    # 0 = unlimited (do not send max_tokens to the API)
    max_tokens: int = 0
    models: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


@dataclass
class TaskConfig:
    provider: str
    model: str


@dataclass
class AnalyzeConfig:
    compressed_subdir: str = "compressed"
    texts_subdir: str = "texts"
    skip_existing: bool = True
    max_analyze_duration_min: int = 0
    window_max_min: int = 15
    window_overlap_sec: int = 20
    max_workers: int = 1
    # Opt-in: inject GoPro GPMF/sidecar summary into analyze context (R-024b).
    # Default false — most clips have no GPS; missing telemetry is always a no-op.
    use_gpmf: bool = False


@dataclass
class NamingConfig:
    index_width: int = 3


@dataclass
class ScriptConfig:
    scripts_subdir: str = "scripts"
    template_file: Path = Path("./templates/vlog_template.md")
    target_words: int = 80


@dataclass
class PlanConfig:
    plans_subdir: str = "plans"
    max_clips_per_day: int = 12
    target_duration_sec: int = 180
    use_transcripts: bool = True


@dataclass
class PreviewSubtitlesConfig:
    """Plan-preview subtitle appearance / layout (project-scoped)."""

    enabled: bool = True
    mode: str = "auto"  # auto | multi | scroll
    max_lines: int = 2
    max_len_per_line: int = 16
    min_font_size: int = 14
    scroll_speed: int = 40  # px/s in scroll mode
    font_size: int = 22
    font_family: str = ""  # empty = follow system
    font_color: str = "#ffffff"
    background: str = "rgba(0,0,0,0.55)"
    outline: str = "0 0 2px rgba(0,0,0,0.8)"  # text-shadow syntax for legibility
    pos_x: int = 50  # percent of player width, 0..100
    pos_y: int = 8  # percent offset from player bottom, 0=bottom 100=top


@dataclass
class PreviewConfig:
    subtitles: PreviewSubtitlesConfig = field(default_factory=PreviewSubtitlesConfig)


@dataclass
class ServerConfig:
    api_token: str | None = None
    task_retention_days: int = 30
    task_max_terminal_tasks: int = 1000
    task_cleanup_interval_min: int = 60

    def __post_init__(self) -> None:
        for name in ("task_retention_days", "task_max_terminal_tasks", "task_cleanup_interval_min"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"server.{name} must be a non-negative integer")


@dataclass
class ExportConfig:
    canvas_ratio: str = "16:9"
    output_subdir: str = field(default="export", metadata={"deprecated": True})
    jianying_draft_dir: str = ""
    auto_copy_draft: bool = False


CANVAS_PRESETS: dict[str, dict[str, int | float]] = {
    "16:9": {"width": 1920, "height": 1080, "ratio": 16 / 9},
    "9:16": {"width": 1080, "height": 1920, "ratio": 9 / 16},
    "1:1": {"width": 1080, "height": 1080, "ratio": 1.0},
}


# ---------------------------------------------------------------------------
# Global-only sub-dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GlobalPathsConfig:
    ffmpeg: str = ""
    ffprobe: str = ""
    logs_dir: Path = Path("./logs")


@dataclass
class GlobalAIConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    debug_print_prompt: bool = False
    provider_ttl_min: int = 60


@dataclass
class GlobalCompressConfig:
    codec: str = "libx264"
    fps: int = 15
    remove_audio: bool = True
    crf: int = 32


@dataclass
class GlobalWhisperConfig:
    cache_dir: str = ""
    hf_endpoint: str = ""


@dataclass
class GlobalConfig:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    naming: NamingConfig = field(default_factory=NamingConfig)
    paths: GlobalPathsConfig = field(default_factory=GlobalPathsConfig)
    ai: GlobalAIConfig = field(default_factory=GlobalAIConfig)
    compress: GlobalCompressConfig = field(default_factory=GlobalCompressConfig)
    whisper: GlobalWhisperConfig = field(default_factory=GlobalWhisperConfig)


# ---------------------------------------------------------------------------
# Project-only sub-dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProjectPathsConfig:
    output_dir: Path = Path("./output")


@dataclass
class ProjectAIConfig:
    tasks: dict[str, TaskConfig] = field(default_factory=dict)
    context: str = ""


@dataclass
class ProjectCompressConfig:
    target_size_mb: float = 5
    max_width: int = 640


@dataclass
class ProjectWhisperConfig:
    enabled: bool = True
    model_size: str = "medium"
    language: str = "zh"
    device: str = "auto"
    max_segments_per_clip: int = 5
    transcripts_subdir: str = "transcripts"
    engine: str = "local"

    def sanitize(self) -> None:
        if self.model_size not in list(WhisperModelSize):
            raise ValueError(f"whisper.model_size must be one of {', '.join(WhisperModelSize)}, got: {self.model_size}")
        if self.language not in list(WhisperLang):
            raise ValueError(f"whisper.language must be one of {', '.join(WhisperLang)}, got: {self.language}")
        if self.device not in list(WhisperDevice):
            raise ValueError(f"whisper.device must be one of {', '.join(WhisperDevice)}, got: {self.device}")
        if self.max_segments_per_clip < 1:
            raise ValueError(f"whisper.max_segments_per_clip must be >= 1, got: {self.max_segments_per_clip}")
        if not self.engine or "/" in self.engine:
            raise ValueError(f"whisper.engine must be a valid ASR provider id, got: {self.engine}")


@dataclass
class ProjectConfig:
    paths: ProjectPathsConfig = field(default_factory=ProjectPathsConfig)
    ai: ProjectAIConfig = field(default_factory=ProjectAIConfig)
    compress: ProjectCompressConfig = field(default_factory=ProjectCompressConfig)
    analyze: AnalyzeConfig = field(default_factory=AnalyzeConfig)
    script: ScriptConfig = field(default_factory=ScriptConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    whisper: ProjectWhisperConfig = field(default_factory=ProjectWhisperConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)


# ---------------------------------------------------------------------------
# Read-only combined views (backward compat)
# ---------------------------------------------------------------------------


@dataclass
class CombinedPaths:
    _global: GlobalPathsConfig
    _project: ProjectPathsConfig | None

    @property
    def ffmpeg(self) -> str:
        return self._global.ffmpeg

    @property
    def ffprobe(self) -> str:
        return self._global.ffprobe

    @property
    def logs_dir(self) -> Path:
        return self._global.logs_dir

    @property
    def output_dir(self) -> Path:
        return self._project.output_dir if self._project else Path("./output")


@dataclass
class CombinedAIConfig:
    _global: GlobalAIConfig
    _project: ProjectAIConfig | None

    @property
    def providers(self) -> dict[str, ProviderConfig]:
        return self._global.providers

    @property
    def debug_print_prompt(self) -> bool:
        return self._global.debug_print_prompt

    @property
    def provider_ttl_min(self) -> int:
        return self._global.provider_ttl_min

    @property
    def tasks(self) -> dict[str, TaskConfig]:
        return self._project.tasks if self._project else {}

    @property
    def context(self) -> str:
        return self._project.context if self._project else ""


@dataclass
class CombinedCompressConfig:
    _global: GlobalCompressConfig
    _project: ProjectCompressConfig | None

    @property
    def codec(self) -> str:
        return self._global.codec

    @property
    def fps(self) -> int:
        return self._global.fps

    @property
    def remove_audio(self) -> bool:
        return self._global.remove_audio

    @property
    def crf(self) -> int:
        return self._global.crf

    @property
    def target_size_mb(self) -> float:
        return self._project.target_size_mb if self._project else 5

    @property
    def max_width(self) -> int:
        return self._project.max_width if self._project else 640


@dataclass
class CombinedWhisperConfig:
    _global: GlobalWhisperConfig
    _project: ProjectWhisperConfig | None

    @property
    def cache_dir(self) -> str:
        return self._global.cache_dir

    @property
    def hf_endpoint(self) -> str:
        return self._global.hf_endpoint

    @property
    def enabled(self) -> bool:
        return self._project.enabled if self._project else True

    @property
    def model_size(self) -> str:
        return self._project.model_size if self._project else "medium"

    @property
    def language(self) -> str:
        return self._project.language if self._project else "zh"

    @property
    def device(self) -> str:
        return self._project.device if self._project else "auto"

    @property
    def max_segments_per_clip(self) -> int:
        return self._project.max_segments_per_clip if self._project else 5

    @property
    def transcripts_subdir(self) -> str:
        return self._project.transcripts_subdir if self._project else "transcripts"

    @property
    def engine(self) -> str:
        return self._project.engine if self._project else "local"


# ---------------------------------------------------------------------------
# AppConfig — runtime merged view
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Backward-compat aliases (used in type hints, parsers, and tests)
# These will be removed after all callers are migrated.
# ---------------------------------------------------------------------------

# These old merged dataclasses are kept temporarily so that callers
# (parsers, validators, tests) continue to compile
# during the transition. They are NOT used by the new loader or AppConfig.


@dataclass
class AIConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    tasks: dict[str, TaskConfig] = field(default_factory=dict)
    context: str = ""
    debug_print_prompt: bool = False
    provider_ttl_min: int = 60


@dataclass
class PathsConfig:
    output_dir: Path = Path("./output")
    ffmpeg: str = ""
    ffprobe: str = ""
    logs_dir: Path = Path("./logs")


@dataclass
class CompressConfig:
    target_size_mb: float = 5
    max_width: int = 640
    fps: int = 15
    codec: str = "libx264"
    crf: int = 32
    remove_audio: bool = True


@dataclass
class WhisperConfig:
    enabled: bool = True
    model_size: str = "medium"
    language: str = "zh"
    device: str = "auto"
    max_segments_per_clip: int = 5
    cache_dir: str = ""
    transcripts_subdir: str = "transcripts"
    hf_endpoint: str = ""

    def sanitize(self) -> None:
        if self.model_size not in list(WhisperModelSize):
            raise ValueError(f"whisper.model_size must be one of {', '.join(WhisperModelSize)}, got: {self.model_size}")
        if self.language not in list(WhisperLang):
            raise ValueError(f"whisper.language must be one of {', '.join(WhisperLang)}, got: {self.language}")
        if self.device not in list(WhisperDevice):
            raise ValueError(f"whisper.device must be one of {', '.join(WhisperDevice)}, got: {self.device}")
        if self.max_segments_per_clip < 1:
            raise ValueError(f"whisper.max_segments_per_clip must be >= 1, got: {self.max_segments_per_clip}")


_EMPTY_PROJECT = ProjectConfig()


class AppConfig:
    """Runtime merged view of GlobalConfig + optional ProjectConfig.

    Read-only combined properties (paths, ai, compress, whisper) delegate to the
    correct layer. Non-split properties delegate directly (project-only → project_cfg,
    global-only → global_cfg). Computed path properties remain unchanged.
    """

    def __init__(
        self,
        *,
        global_cfg: GlobalConfig,
        project_cfg: ProjectConfig | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_cfg = global_cfg
        self._project_cfg = project_cfg
        self._project_dir = project_dir.resolve() if project_dir else None
        self._paths: CombinedPaths | None = None
        self._ai: CombinedAIConfig | None = None
        self._compress: CombinedCompressConfig | None = None
        self._whisper: CombinedWhisperConfig | None = None
        self._default_project_cfg: ProjectConfig | None = None

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    # -- layer accessors --

    @property
    def global_cfg(self) -> GlobalConfig:
        return self._global_cfg

    @global_cfg.setter
    def global_cfg(self, val: GlobalConfig) -> None:
        self._global_cfg = val
        self._paths = None

    @property
    def project_cfg(self) -> ProjectConfig | None:
        return self._project_cfg

    @project_cfg.setter
    def project_cfg(self, val: ProjectConfig | None) -> None:
        self._project_cfg = val
        self._paths = None
        self._ai = None
        self._compress = None
        self._whisper = None

    # -- read-only combined properties --

    @property
    def paths(self) -> CombinedPaths:
        if self._paths is None:
            self._paths = CombinedPaths(self._global_cfg.paths, self._project_cfg.paths if self._project_cfg else None)
        return self._paths

    @property
    def ai(self) -> CombinedAIConfig:
        if self._ai is None:
            self._ai = CombinedAIConfig(self._global_cfg.ai, self._project_cfg.ai if self._project_cfg else None)
        return self._ai

    @property
    def compress(self) -> CombinedCompressConfig:
        if self._compress is None:
            self._compress = CombinedCompressConfig(
                self._global_cfg.compress, self._project_cfg.compress if self._project_cfg else None
            )
        return self._compress

    @property
    def whisper(self) -> CombinedWhisperConfig:
        if self._whisper is None:
            self._whisper = CombinedWhisperConfig(
                self._global_cfg.whisper, self._project_cfg.whisper if self._project_cfg else None
            )
        return self._whisper

    # -- non-split: project-only sections --

    @property
    def analyze(self) -> AnalyzeConfig:
        if self._project_cfg is not None:
            return self._project_cfg.analyze
        if self._default_project_cfg is None:
            self._default_project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        return self._default_project_cfg.analyze

    @analyze.setter
    def analyze(self, val: AnalyzeConfig) -> None:
        if self._project_cfg is None:
            self._project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        self._project_cfg.analyze = val

    @property
    def script(self) -> ScriptConfig:
        if self._project_cfg is not None:
            return self._project_cfg.script
        if self._default_project_cfg is None:
            self._default_project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        return self._default_project_cfg.script

    @script.setter
    def script(self, val: ScriptConfig) -> None:
        if self._project_cfg is None:
            self._project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        self._project_cfg.script = val

    @property
    def plan(self) -> PlanConfig:
        if self._project_cfg is not None:
            return self._project_cfg.plan
        if self._default_project_cfg is None:
            self._default_project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        return self._default_project_cfg.plan

    @plan.setter
    def plan(self, val: PlanConfig) -> None:
        if self._project_cfg is None:
            self._project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        self._project_cfg.plan = val

    @property
    def export(self) -> ExportConfig:
        if self._project_cfg is not None:
            return self._project_cfg.export
        if self._default_project_cfg is None:
            self._default_project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        return self._default_project_cfg.export

    @export.setter
    def export(self, val: ExportConfig) -> None:
        if self._project_cfg is None:
            self._project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        self._project_cfg.export = val

    @property
    def preview(self) -> PreviewConfig:
        if self._project_cfg is not None:
            return self._project_cfg.preview
        if self._default_project_cfg is None:
            self._default_project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        return self._default_project_cfg.preview

    @preview.setter
    def preview(self, val: PreviewConfig) -> None:
        if self._project_cfg is None:
            self._project_cfg = ProjectConfig()
            self._paths = None
            self._ai = None
            self._compress = None
            self._whisper = None
        self._project_cfg.preview = val

    # -- non-split: global-only sections --

    @property
    def proxy(self) -> ProxyConfig:
        return self._global_cfg.proxy

    @property
    def server(self) -> ServerConfig:
        return self._global_cfg.server

    @property
    def naming(self) -> NamingConfig:
        return self._global_cfg.naming

    # -- computed paths (unchanged) --

    def _resolved_output_subdir(self, subdir: str) -> Path:
        """Join *subdir* under output_dir and re-check root containment (GAP-P1-03)."""
        from clio.utils import validate_within_root

        root = self.paths.output_dir
        return validate_within_root(root / subdir, root)

    @property
    def compressed_dir(self) -> Path:
        return self._resolved_output_subdir(self.analyze.compressed_subdir)

    @property
    def texts_dir(self) -> Path:
        return self._resolved_output_subdir(self.analyze.texts_subdir)

    @property
    def scripts_dir(self) -> Path:
        return self._resolved_output_subdir(self.script.scripts_subdir)

    @property
    def plans_dir(self) -> Path:
        return self._resolved_output_subdir(self.plan.plans_subdir)

    @property
    def transcripts_dir(self) -> Path:
        return self._resolved_output_subdir(self.whisper.transcripts_subdir)

    @property
    def summary_csv(self) -> Path:
        return self._resolved_output_subdir("summary.csv")
