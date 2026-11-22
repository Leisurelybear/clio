from clio.ui.services.run_preview import _build_stage_summary

rows = [
    {
        "name": "transcribe",
        "total": 0,
        "will_run": 0,
        "will_skip": 0,
        "warnings": ["transcription disabled"],
        "disabled": True,
    },
]
print(_build_stage_summary(["transcribe"], rows))
print(
    _build_stage_summary(
        ["compress", "transcribe"], [{**rows[0]}, {"name": "compress", "total": 3, "will_run": 0, "will_skip": 3}]
    )
)
