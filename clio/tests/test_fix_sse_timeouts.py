"""Regression tests: an SSE write timeout ends the stream quietly."""

from __future__ import annotations

from unittest.mock import MagicMock

from clio.ui.routes.notifications import handle_get_notifications_stream
from clio.ui.routes.tasks import handle_get_tasks_stream


def _handler(store: MagicMock) -> MagicMock:
    handler = MagicMock()
    handler.headers = {}
    handler._get_task_manager.return_value.store = store
    handler.wfile.write.side_effect = TimeoutError("timed out")
    return handler


def test_task_stream_returns_on_write_timeout():
    store = MagicMock()
    store.waiters_stopped = False
    event = MagicMock(seq=1, task_id="t1")
    event.to_dict.return_value = {}
    store.wait_for_events.return_value = [event]
    store.get_many.return_value = {}
    handler = _handler(store)

    handle_get_tasks_stream(handler, {})  # must not raise

    assert handler.close_connection is True


def test_notification_stream_returns_on_write_timeout():
    store = MagicMock()
    store.waiters_stopped = False
    store.wait_for_notification_revision.return_value = 5
    handler = _handler(store)

    handle_get_notifications_stream(handler, {})  # must not raise

    assert handler.close_connection is True
