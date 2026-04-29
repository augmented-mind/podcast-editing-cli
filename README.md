# podcast-cli

CLI toolkit for podcast transcription and auto-editing. Designed for two-camera, two-mic podcast recordings edited in Final Cut Pro.

## Install

```bash
brew install ffmpeg  # if not already installed
uv pip install -e .
```

## Workflow

### Setup: Recording

Record with two cameras and two microphones (one per speaker). The audio should be a **duo-mono** file where:
- **L channel** = Speaker A mic (the person filmed by Camera A)
- **R channel** = Speaker B mic (the person filmed by Camera B)

### Step 1: Import & sync in Final Cut Pro

1. Import all media into FCP (camera files + audio file)
2. If Camera A is split across multiple files, drag them onto a timeline in order
3. Select all clips in the Browser, right-click → **New Multicam Clip** (sync by audio waveform)
4. Create a new Project, drop the multicam clip in
5. Arrange the timeline: Camera B as the primary storyline, Camera A clips on lane 1 (above), audio on lane -1 (below)
6. Disable the cameras' built-in audio (we use the external audio file instead)

### Step 2: Export FCPXML

**File → Export XML...** → save as `.fcpxml` (version 1.11, metadata view: General)

### Step 3: Auto-edit

```bash
podcast autoedit timeline.fcpxml audio.aifc
```

This generates `timeline_edited.fcpxml` with:
- **Camera switches**: Camera A clips are split and enabled/disabled based on who's speaking
- **Two audio lanes**: each speaker's mic on a separate lane, muted when they're not talking

All edits are non-destructive — select any disabled clip in FCP and press **V** to re-enable it.

### Step 4: Import back into FCP

**File → Import → XML...** → select the `_edited.fcpxml` file. FCP creates a new project with the auto-edits applied. Fine-tune from there.

### Optional: Add timestamped PNG overlays

Name overlay PNGs by their desired start time, e.g. `6_19.png` for 6:19 or
`1_02_03.png` for 1:02:03, then import them into a copied timeline XML:

```bash
podcast overlays timeline.fcpxml overlay --duration 4.5 -o timeline_overlays.fcpxml
```

Import `timeline_overlays.fcpxml` back into Final Cut Pro. Each PNG is inserted
as an editable connected clip on a high video lane.

### Step 5: Transcribe the final export

After finishing your manual edits, export the final video from FCP, then:

```bash
podcast transcribe final_video.mp4
```

This generates:
- `final_video.srt` — subtitles (upload to YouTube via Subtitles → Add → Upload file)
- `final_video_transcript.txt` — timestamped text transcript
- `final_video_segments.json` — raw Whisper segments (reusable)

## Commands

### `podcast transcribe`

```bash
podcast transcribe video.mp4                           # SRT + transcript + JSON
podcast transcribe video.mp4 --model small             # faster, less accurate
podcast transcribe video.mp4 --model large             # most accurate (default)
podcast transcribe video.mp4 --language zh              # non-English
podcast transcribe video.mp4 -o output_dir/            # custom output directory
```

### `podcast autoedit`

```bash
podcast autoedit timeline.fcpxml audio.aifc                        # camera switches + audio lanes
podcast autoedit timeline.fcpxml audio.aifc --fillers              # + filler word markers
podcast autoedit timeline.fcpxml audio.aifc --fillers --whisper-model small  # faster filler detection
podcast autoedit timeline.fcpxml audio.aifc --min-segment 3.0     # less frequent camera switches
podcast autoedit timeline.fcpxml audio.aifc -o custom_output.fcpxml
```

### `podcast overlays`

```bash
podcast overlays timeline.fcpxml overlay                           # 4.5s PNG overlays
podcast overlays timeline.fcpxml overlay --duration 5.0            # custom duration
podcast overlays timeline.fcpxml overlay --lane 12                 # use lane 12+
podcast overlays timeline.fcpxml overlay -o timeline_overlays.fcpxml
```

PNG file names should begin with a timestamp: `M_SS.png`, `MM_SS.png`, or
`H_MM_SS.png`. Descriptive suffixes are okay, e.g. `6_19_topic_card.png`.

#### Options

| Flag | Default | Description |
|---|---|---|
| `--min-segment` | 2.0 | Minimum seconds before switching cameras |
| `--silence-db` | -40 | Below this dB level = silence |
| `--crossover-db` | 3 | dB difference needed to pick the active speaker |
| `--fillers` | off | Detect filler words (um, uh) and add markers to the timeline |
| `--whisper-model` | base | Whisper model for filler detection |
| `--language` | en | Language for transcription |

## FCP Tips

| Action | Shortcut |
|---|---|
| Re-enable a disabled clip | Select it → **V** |
| Jump to next marker | **Ctrl + '** |
| Jump to previous marker | **Ctrl + Shift + '** |
| Blade all tracks at playhead | **Shift + B** |
| Delete range across all tracks | **I** (in) → **O** (out) → **Cmd + Shift + Delete** |
