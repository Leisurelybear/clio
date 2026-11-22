fix(ffmpeg): find package-manager installs, log failures, allow cancel

After winget/choco/scoop succeeded, only the registry PATH changed, so
shutil.which in the same process missed the new ffmpeg: system mode reported
failure and auto mode downloaded it again. Failures were swallowed by a bare
except, and the 900s wait ignored cancel_event. Search the package managers'
link/shim/bin dirs (and Homebrew's on macOS), log exceptions and non-zero
exits with a stderr tail, and poll cancel_event while the default runner waits.

Refs: C-24
