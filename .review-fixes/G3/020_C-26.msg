fix(credentials): ignore empty inline keys on shared credential refs

With two providers sharing one explicit credential_ref, the inline-key
conflict check compared "abc" with "" when the provider holding the key came
first and raised ValueError, so config loading failed; the reverse order
loaded fine. Empty inline values are now skipped, and only two different
non-empty values are reported as a conflict.

Refs: C-26
