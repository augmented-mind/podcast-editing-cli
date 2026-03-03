# Project: podcast CLI

A CLI toolkit for podcast transcription and auto-editing. See README.md for full docs.

## Quick reference

- **Entry point**: `podcast = podcast.cli:cli` (Click)
- **Key modules**: `cli.py` (commands), `transcriber.py` (whisper-mps + SRT), `autoedit.py` (speaker detection, FCPXML generation), `fcpxml.py` (FCPXML time helpers, structure detection)
- **System deps**: ffmpeg (audio extraction)
- **Tests**: `pytest`

## Architecture notes

- Duo-mono audio: L channel = Speaker A (Camera A), R channel = Speaker B (Camera B)
- Speaker detection uses RMS energy comparison in 100ms windows
- FCPXML edits are non-destructive: disabled clips can be re-enabled in FCP
- Whisper-mps (MLX/Metal) is the transcription backend
- SRT generation is shared between `transcribe` and `autoedit --fillers`
- All FCPXML child elements must follow DTD order: note?, conform-rate?, adjust-*, connected-clips*, markers*, audio-channel-source*
