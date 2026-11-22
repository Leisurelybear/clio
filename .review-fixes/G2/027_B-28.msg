fix(asr): translate provider display names when they are read

ProviderCapabilities.display_name was translated once at import time, so
switching the UI language with set_active_locale never changed the ASR
engine names in the settings schema or readiness messages. Capabilities now
carry the i18n key and list_providers()/check_asr_ready() return the name
in the active locale.

Refs: B-28
