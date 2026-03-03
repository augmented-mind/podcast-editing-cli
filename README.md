# podcast-cli

CLI toolkit for podcast transcription and auto-editing.

## Install

```bash
pip install -e .
```

Requires `ffmpeg` installed on your system.

## Commands

### Transcribe

```bash
podcast transcribe video.mp4                          # SRT + transcript + JSON
podcast transcribe video.mp4 --model small            # faster, less accurate
podcast transcribe video.mp4 --language zh             # non-English
```

### Auto-edit (FCPXML)

```bash
podcast autoedit timeline.fcpxml audio.aifc            # camera switches + audio lanes
podcast autoedit timeline.fcpxml audio.aifc --fillers   # + filler word markers
```

Duo-mono audio mapping: L channel = Speaker A (Camera A), R channel = Speaker B (Camera B).
