"""Whisper-mps transcription and SRT generation."""

from __future__ import annotations

import json
from pathlib import Path


def fmt_srt_time(t: float) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_audio(
    audio_path: str, model: str = "large", language: str = "en"
) -> dict:
    """Run whisper-mps transcription, return result dict with 'segments'."""
    from whisper_mps.whisper.transcribe import transcribe as mps_transcribe

    print(f"  Transcribing with whisper-mps (model={model}, lang={language}) ...")
    return mps_transcribe(
        audio_path, model=model, verbose=False, language=language
    )


def generate_srt(
    segments: list[dict],
    output_path: str | Path,
    audio_trim: float = 0.0,
) -> int:
    """Generate SRT subtitle file from Whisper segments.

    Args:
        segments: Whisper segment dicts with start/end/text
        output_path: where to write the .srt file
        audio_trim: seconds to subtract from Whisper timestamps
                    (the audio clip's start/trim point in the source file)

    Returns:
        Number of SRT entries written.
    """
    idx = 0
    with open(output_path, "w", encoding="utf-8-sig") as f:  # UTF-8 BOM for FCP
        for seg in segments:
            start = seg["start"] - audio_trim
            end = seg["end"] - audio_trim
            text = seg["text"].strip()
            if not text:
                continue
            if end <= 0:
                continue
            start = max(0.0, start)
            if end - start < 0.3:
                continue
            idx += 1
            f.write(f"{idx}\r\n")
            f.write(f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}\r\n")
            f.write(f"{text}\r\n")
            f.write(f"\r\n")

    print(f"  SRT saved to {output_path} ({idx} entries)")
    return idx


def save_transcript(segments: list[dict], output_path: str | Path) -> None:
    """Save plain-text transcript with timestamps."""
    with open(output_path, "w") as f:
        for seg in segments:
            m, s = divmod(seg["start"], 60)
            f.write(f"[{int(m):02d}:{s:05.2f}] {seg['text'].strip()}\n")
    print(f"  Transcript saved to {output_path}")


def save_segments_json(segments: list[dict], output_path: str | Path) -> None:
    """Save raw Whisper segments as JSON (for reuse without re-transcribing)."""
    with open(output_path, "w") as f:
        json.dump(segments, f, indent=2, default=str)
    print(f"  Segments JSON saved to {output_path}")


def run_transcription(
    audio_file: str,
    *,
    model: str = "large",
    language: str = "en",
    output_dir: str | None = None,
) -> None:
    """Full transcription pipeline: transcribe -> SRT + transcript + JSON."""
    audio_path = Path(audio_file)
    out_dir = Path(output_dir) if output_dir else audio_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem

    result = transcribe_audio(str(audio_path), model=model, language=language)
    print(f"  {len(result['segments'])} segments")

    generate_srt(result["segments"], out_dir / f"{stem}.srt")
    save_transcript(result["segments"], out_dir / f"{stem}_transcript.txt")
    save_segments_json(result["segments"], out_dir / f"{stem}_segments.json")

    print("Done!")
