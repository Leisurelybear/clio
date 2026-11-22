fix(project): report failures when removing a project

The project-card remove button awaited POST /api/project/remove without
try/catch, so network/server errors became unhandled rejections and an
ok:false reply was silently ignored. Show project.remove_failed in the
status bar for both cases and disable the button while the request is in
flight to prevent double submits. Adds the new key to both locales.

Refs: E-17
