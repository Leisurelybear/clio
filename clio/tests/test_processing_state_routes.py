from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from clio.ui.routes.processing_state_routes import handle_get_processing_state


class TestHandleGetProcessingState:
    def test_returns_state(self):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = Path("/some/input")
        handler._get_project_output.return_value = Path("/some/output")
        handler._send_json = MagicMock()

        handle_get_processing_state(handler, {})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args.args[0]
        assert payload["version"] == 1
        assert payload["steps"] == ["compress", "transcribe", "analyze", "voiceover", "plan", "label"]
        assert payload["files"] == {}
