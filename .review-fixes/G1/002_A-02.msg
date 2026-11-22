fix(readiness): skip undecodable or non-object analysis JSON

_collect_text_indices only caught JSONDecodeError/OSError and called
data.get() without a dict check, so a GBK-encoded or array-valued file in
texts/ crashed collect_project_indices and with it CLI cut/export and UI
export. Add a private _load_json_dict helper (UnicodeDecodeError + dict
check) and use it for all three texts/ readers in plan_readiness.

Refs: A-02
