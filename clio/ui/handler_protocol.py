"""Typed Protocol for dynamic handler methods attached in server.py's make_handler()."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Protocol

from clio.config import AppConfig


class HandlerProtocol(Protocol):
    """Minimal typed interface for stable cross-route handler capabilities."""

    # -- Standard HTTP server methods (from BaseHTTPRequestHandler) --
    def send_response(self, code: int, message: str | None = None) -> None: ...
    def send_header(self, keyword: str, value: str) -> None: ...
    def end_headers(self) -> None: ...
    def send_error(self, code: int, message: str | None = None) -> None: ...

    wfile: Any  # io.BufferedIOBase

    # -- Custom instance methods --
    def _send_json(self, data: Any, status: int = 200) -> None: ...
    def _send_bytes(
        self,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None: ...
    def _send_static(self, rel: str) -> None: ...
    def _resolve_project_dir(self, qs: dict[str, Any]) -> Path: ...
    def _resolve_project_input(self, qs: dict[str, Any]) -> Path: ...  # compat alias
    def _get_project_output(self, qs_or_proj_dir: dict[str, Any] | Path) -> Path: ...
    def _get_config(self, project_dir: Path | None = None) -> AppConfig: ...
    def _send_video_range(self, path: Path) -> None: ...
    def _get_state(self, project_key: str) -> Any: ...
    def _resolve_texts(self, basename: str, proj_out: Path | None = None) -> Path | None: ...
    def _resolve_in(self, subdir: str, basename: str, proj_out: Path | None = None) -> Path | None: ...

    # -- Stable class-level attributes --
    config_path: Path | None
    project_dir: Path
    output_dir: Path
    DEFAULT_PROJECT: dict[str, Any]
    _api_token: str | None
    _config_cache: ClassVar[Any]
