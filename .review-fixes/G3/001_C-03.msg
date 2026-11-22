fix(credentials): keep keyring backend after a transient read failure

KeyringStore.get set self.backend = None on any backend error, so one locked
keychain, DBus timeout or denied macOS prompt switched default_source to
dotenv and later saves went to plaintext .env without telling the user.
get now logs one warning and returns None but keeps the backend; delete reads
through _call so a failing or unavailable keyring is reported, not skipped.

Refs: C-03
