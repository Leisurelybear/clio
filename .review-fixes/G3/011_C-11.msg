fix(ai): scope structured output fallback per model and schema

One 400/422 mentioning response_format set a provider-wide flag, so every
later task and model on that cached provider lost schema enforcement until
the provider TTL expired. strict: true was also sent for pydantic schemas
whose defaulted fields are not in required, which strict mode rejects.
The fallback is now keyed by (model, schema name) and expires after 10 min,
and strict is only set when every object is closed and fully required.

Refs: C-11
