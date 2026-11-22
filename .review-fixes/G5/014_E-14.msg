fix(models): serialize legacy download polling and stop off-page

The legacy whisper download fallback used setInterval(_pollModelDl, 1000)
with no in-flight guard: slow responses overlapped and could arrive out of
order (progress bar jumping back), and the timer kept polling after the
user left the settings page. Replace it with a setTimeout chain that waits
for each response, guarded by a generation counter, and stop as soon as
#whisper-model-mgmt is no longer in the DOM. Tests added.

Refs: E-14
