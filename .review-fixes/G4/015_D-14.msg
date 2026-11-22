fix(router): percent-decode path params once in Router.dispatch

Path params came from the undecoded URL path; credentials/providers
unquoted them by hand, but vmeta/prompts/tasks/notifications did not, so
a non-ASCII stem (encodeURIComponent'd by the client) always got 404.
handle_get_vmeta also put the stem straight into a glob pattern, so [ ]
acted as a character class. Router now decodes every param once, the
manual unquote calls are removed, and the vmeta glob is escaped.

Refs: D-14
