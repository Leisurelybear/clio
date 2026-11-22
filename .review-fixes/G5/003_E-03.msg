fix(editor): keep dirty flag when edits land during a save

saveEditorPlan cleared dirty after the PUT resolved, even if the user kept
editing while the request was in flight. Those edits were not in the body,
yet beforeunload and tab-switch guards stopped warning and they were lost.
markDirty() now bumps state.editSeq; the save snapshots it and only clears
dirty when it is unchanged. Covers artifact and config saves; tests added.

Refs: E-03
