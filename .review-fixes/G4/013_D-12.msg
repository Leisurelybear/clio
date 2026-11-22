fix(ui): lock videos.json/project.json updates and whitelist fields

PUT /api/videos/selected, PUT /api/videos/relink and PUT /api/project
did read-modify-write without a lock, so concurrent saves (the UI saves
lastVideo often) lost updates. PUT /api/project also merged the raw body,
letting a client overwrite output_dir/version/createdAt.
The updates now run under path_write_lock of the target file and
PUT /api/project only accepts currentDay/source/lastEntity/lastVideo/name.

Refs: D-12
