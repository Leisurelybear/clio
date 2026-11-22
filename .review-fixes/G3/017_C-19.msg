fix(desktop): deliver Windows toasts and guard notify-send arguments

Toasts were created with CreateToastNotifier('Clio'), but that AppUserModelID
is never registered, so Windows 10/11 dropped them while PowerShell still
exited 0 and the UI reported delivered=True. The script now uses Windows
PowerShell's registered AUMID. On Linux a title starting with "-" was parsed
as a notify-send option; "--" now ends option parsing.

Refs: C-19
