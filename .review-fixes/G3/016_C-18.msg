fix(desktop): keep the window open when the quit dialog fails

_ask_yes_no returned True whenever the Tk dialog could not be shown, so on
close with active runs a missing tkinter (or a Tk error off the main thread)
cancelled every running task without asking. _ask_yes_no now takes a
default; the quit prompt uses default=False and keeps the window open, while
the launch prompt still fails open for headless launches.

Refs: C-18
