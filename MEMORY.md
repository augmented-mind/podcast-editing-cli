# Memory

## Durable
- FCPXML still-image assets: set uid=str(uuid.uuid4()).upper() (not file URI) and hasAudio="0" explicitly — FCP expects both.
- Overlay filename timestamps are relative to source-media time origin, not playback in-point; trimmed clips may diverge — document or reanchor when touching overlays.py.
