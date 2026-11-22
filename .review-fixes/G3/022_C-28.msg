refactor(credentials): share one masked-placeholder constant

The set of UI mask placeholders ("***", "********", "••••••••••") was
spelled out in credentials/models.py, twice in credentials/service.py and in
ai/provider_security.py, so a new mask would have to be added in four places.
They now all use credentials.models.MASKED_PLACEHOLDERS. No behaviour change.

Refs: C-28
