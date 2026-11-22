fix(packaging): detect universal2 Python via sysconfig on macOS

The macOS build checked "universal2" in platform.machine(), which returns the
running arch (arm64 or x86_64), so it always built host-arch only and printed
a misleading warning; Apple Silicon builds did not run on Intel Macs.
It now checks sysconfig.get_platform() and asserts both archs with lipo.

Refs: C-20
