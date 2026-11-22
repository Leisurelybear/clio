ci: run on pushes to branches containing a slash

GitHub's `*` glob does not match `/`, so pushes to feature/x or fix/y never
triggered CI. Use `**`.

Refs: C-22
