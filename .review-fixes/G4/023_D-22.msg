refactor(ui): drop dead duplicates from server.py and projects.py

server.py still defined _STATE_CREATE_LOCK, _TASK_MANAGER_CREATE_LOCK and
_setting_int, but the live copies are in handler_factory.py, and
handle_post_project_select was not routed anywhere. Remove the dead code so
there is one definition of each. The larger splits suggested in the review
(routes/_common.py, videos.py/run_preview.py) are left for later.

Refs: D-22
