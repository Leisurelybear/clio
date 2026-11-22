perf(runner): coalesce run preview refreshes from list re-renders

renderVideoList() always ends in updateRunFilesBadge(), which on the Run
page POSTed /api/run/preview. Every checkbox toggle, drag-select step or
list refresh triggered a server-side preview scan. Badge-driven refreshes
now run once immediately and fold further calls within 300ms into a
single trailing request. Direct option changes still refresh at once.

Refs: E-09
