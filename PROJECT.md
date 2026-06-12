# Project Context

## Goals

- Python CLI toolkit (`podcast` command) for podcast transcription and non-destructive auto-editing
- Targets duo-mono audio: L channel = Speaker A (Camera A), R channel = Speaker B (Camera B)
- Outputs FCPXML for Final Cut Pro X; edits are non-destructive (disabled clips can be re-enabled)

## Constraints

- Transcription backend: whisper-mps (MLX/Metal); requires Apple Silicon or compatible hardware
- System dependency: ffmpeg for audio extraction
- FCPXML child elements must follow DTD order: note?, conform-rate?, adjust-*, connected-clips*, markers*, audio-channel-source*

## Open Questions

- PR #2 (feature/overlay-fcpxml) still open after MINOR_ISSUES review; PR #3 (feature/fcpxml-overlays-pr, 2026-06-11) appears to supersede it — confirm PR #2 can be closed.
