fix(whisper): bound pip install time and stream its output

`whisper install` ran two pip installs (~2.8 GB of CUDA wheels) without
any timeout, and the first one captured output, so a stalled mirror hung
the CLI forever with no visible progress. Pass pip --timeout/--retries,
give both runs a generous subprocess timeout that is reported as an install
failure, and stream pip output to the console. run_subprocess itself keeps
its no-default-timeout contract so other long-running callers are unchanged.

Refs: A-08
