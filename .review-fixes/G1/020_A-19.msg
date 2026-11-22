fix(ffmpeg): log swallowed binary lookup and verification errors

verify_binaries and the managed-binary lookup in discover_ffmpeg_bin
swallowed every exception silently, so permission or timeout problems
looked like "ffmpeg not found". Keep the fallbacks but record the exception
type and message (and non-zero -version exit codes) at debug level. The
package-manager loop (:378) is already covered by C-24.

Refs: A-19
