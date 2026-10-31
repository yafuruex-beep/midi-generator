# Development Rules

- Keep this project independent of individual songs, bands, and DAWs.
- CLI and Python API must share the same core implementation.
- Keep song-specific settings in request/config files, not hard-coded in core modules.
- Add instrument modules incrementally behind a documented common interface.
- Preserve original MIDI files; never overwrite source assets by default.
- Track provenance and license/usage notes for external MIDI and audio sources.
- Separate estimated analysis from user-verified annotations and include confidence when available.
- Prefer small, testable changes and run tests after implementation.
- Do not claim a feature exists unless it is implemented and tested.
- Do not add AI model training or advanced audio analysis before basic MIDI workflows are reliable.
- Ask before destructive changes to existing user data.
- Formal guitar roles come only from MIDI files directly inside `data/sources/guitar/backing/` or `lead/`; genre and section belong in catalog tags.
- Legacy guitar paths remain readable with no formal role. Never automatically move guitar assets or establish formal Backing/Lead roles from filenames, tags, pitch, or analysis features.
- After manual guitar placement, reconcile stable IDs and hash-verified analysis references using dry-run and backed-up metadata updates before rescanning.
