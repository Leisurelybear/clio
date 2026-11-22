fix(vmeta): accept a UTF-8 BOM in .vmeta and .vindex sidecars

Sidecars are machine-written, but users on Windows do open and re-save them
in Notepad, which adds a BOM and made json.loads fail so the sidecar was
treated as missing. Read them with utf-8-sig.

Refs: A-06c
