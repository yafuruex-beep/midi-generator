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
