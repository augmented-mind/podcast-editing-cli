"""Auto-edit: speaker detection, FCPXML camera switches, audio muting, filler markers."""

from __future__ import annotations

import copy
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np

from podcast.fcpxml import (
    FRAME_DUR,
    detect_structure,
    fmt,
    parse_time,
    snap_to_frame,
    write_fcpxml,
)

SAMPLE_RATE = 48000
WINDOW_SEC = 0.1  # 100ms analysis windows

# Filler word lists
DEFINITE_FILLERS = {
    "um", "uh", "uhm", "umm", "hmm", "hm", "mm", "er", "ah", "erm", "eh",
}
MIN_PAUSE_SEC = 1.5


# ================================================================
# Audio analysis
# ================================================================
def extract_channels(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Extract L/R channels from audio file via ffmpeg."""
    print("  Running ffmpeg ...")
    cmd = [
        "ffmpeg", "-i", path,
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "pipe:1", "-v", "error",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"ffmpeg error: {proc.stderr.decode()}")
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    return raw[0::2].copy(), raw[1::2].copy()


def detect_speakers(
    left: np.ndarray, right: np.ndarray, silence_db: float, crossover_db: float
) -> list:
    """Classify each analysis window as speaker A (L) or B (R)."""
    win = int(SAMPLE_RATE * WINDOW_SEC)
    n = min(len(left), len(right)) // win

    L = left[: n * win].reshape(n, win)
    R = right[: n * win].reshape(n, win)

    eps = 1e-10
    l_db = 20 * np.log10(np.sqrt(np.mean(L ** 2, axis=1)) + eps)
    r_db = 20 * np.log10(np.sqrt(np.mean(R ** 2, axis=1)) + eps)

    speakers = []
    for i in range(n):
        l, r = l_db[i], r_db[i]
        if l < silence_db and r < silence_db:
            speakers.append(None)
        elif l - r > crossover_db:
            speakers.append("A")
        elif r - l > crossover_db:
            speakers.append("B")
        else:
            speakers.append(None)

    # Forward-fill None with last known speaker
    last = "A"
    for i in range(n):
        if speakers[i] is None:
            speakers[i] = last
        else:
            last = speakers[i]

    segs = []
    cur = speakers[0]
    start = 0
    for i in range(1, n):
        if speakers[i] != cur:
            segs.append([start * WINDOW_SEC, i * WINDOW_SEC, cur])
            start = i
            cur = speakers[i]
    segs.append([start * WINDOW_SEC, n * WINDOW_SEC, cur])
    return segs


def merge_segments(segs: list, min_dur: float) -> list[tuple]:
    """Merge segments shorter than min_dur into neighbors."""
    if not segs:
        return segs
    segs = [list(s) for s in segs]

    changed = True
    while changed:
        changed = False

        merged = [segs[0]]
        for s in segs[1:]:
            if s[2] == merged[-1][2]:
                merged[-1][1] = s[1]
                changed = True
            else:
                merged.append(s)
        segs = merged

        merged = []
        for s in segs:
            if s[1] - s[0] < min_dur and merged:
                merged[-1][1] = s[1]
                changed = True
            else:
                merged.append(s)
        segs = merged

        if len(segs) > 1 and segs[0][1] - segs[0][0] < min_dur:
            segs[1][0] = segs[0][0]
            segs.pop(0)
            changed = True

    return [(s, e, sp) for s, e, sp in segs]


# ================================================================
# FCPXML generation
# ================================================================
def audio_secs_to_pst(t_sec: float, info: dict) -> Fraction:
    """Audio file seconds -> parent source time (Fraction, frame-snapped)."""
    a_off = info["audio_offset"]
    a_st = info["audio_start"]
    raw = a_off + Fraction(t_sec).limit_denominator(100000) - a_st
    return snap_to_frame(raw)


def generate_fcpxml(
    segments: list[tuple],
    info: dict,
    output_path: str,
    audio_name: str,
) -> None:
    """Generate FCPXML with camera switches and audio lane splitting."""
    tree = info["tree"]
    parent = info["parent"]
    parent_start = info["parent_start"]
    parent_dur = info["parent_dur"]
    parent_end = parent_start + parent_dur
    cam_a_refs = info["cam_a_refs"]
    audio_ref = info["audio_ref"]

    # ---- categorise children (DTD order!) ----
    cam_a_clips = []
    preamble = []
    connected = []
    acs_children = []
    for child in list(parent):
        tag = child.tag
        ref = child.get("ref", "")
        is_cam_a = (
            tag == "asset-clip" and ref in cam_a_refs and child.get("enabled") is None
        )
        is_audio = tag == "asset-clip" and ref == audio_ref
        if is_cam_a:
            cam_a_clips.append(child)
        elif is_audio:
            pass  # drop; we regenerate
        elif tag == "audio-channel-source":
            acs_children.append(child)
        elif tag in ("conform-rate", "timeMap", "note") or tag.startswith("adjust-"):
            preamble.append(child)
        else:
            connected.append(child)

    for child in list(parent):
        parent.remove(child)
    for child in preamble:
        parent.append(child)
    for child in connected:
        parent.append(child)

    # ---- map segments to parent-source-time ----
    pst_segs = []
    for s, e, sp in segments:
        ps = audio_secs_to_pst(s, info)
        pe = audio_secs_to_pst(e, info)
        ps = max(ps, parent_start)
        pe = min(pe, parent_end)
        if ps < pe:
            pst_segs.append((ps, pe, sp))

    print(f"  {len(pst_segs)} segments mapped to timeline")

    # ---- split Camera A clips ----
    n_cam = 0
    for clip in cam_a_clips:
        c_off = parse_time(clip.get("offset"))
        c_st = parse_time(clip.get("start", "0s"))
        c_dur = parse_time(clip.get("duration"))
        c_end = c_off + c_dur

        for seg_s, seg_e, sp in pst_segs:
            ov_s = max(c_off, seg_s)
            ov_e = min(c_end, seg_e)
            if ov_s >= ov_e:
                continue

            sub = copy.deepcopy(clip)
            sub.set("offset", fmt(ov_s))
            sub.set("start", fmt(c_st + (ov_s - c_off)))
            sub.set("duration", fmt(ov_e - ov_s))

            if sp == "B":
                sub.set("enabled", "0")
            elif "enabled" in sub.attrib:
                del sub.attrib["enabled"]

            has_mute = any(
                c.tag == "audio-channel-source" and c.get("enabled") == "0"
                for c in sub
            )
            if not has_mute:
                m = ET.SubElement(sub, "audio-channel-source")
                m.set("srcCh", "1, 2")
                m.set("role", "dialogue.dialogue-1")
                m.set("enabled", "0")

            parent.append(sub)
            n_cam += 1

    print(f"  {n_cam} Camera A sub-clips")

    # ---- split Audio into two lanes ----
    a_off = info["audio_offset"]
    a_st = info["audio_start"]
    a_dur = info["audio_dur"]
    a_end_pst = a_off + a_dur
    a_fmt = info["audio_clip"].get("format", "r5")

    n_aud = 0
    for lane, src_ch, role, active_speaker in [
        ("-1", "1", "dialogue.dialogue-1", "A"),
        ("-2", "2", "dialogue.dialogue-2", "B"),
    ]:
        for seg_s, seg_e, sp in pst_segs:
            ov_s = max(a_off, seg_s)
            ov_e = min(a_end_pst, seg_e)
            if ov_s >= ov_e:
                continue

            ac = ET.SubElement(parent, "asset-clip")
            ac.set("ref", audio_ref)
            ac.set("lane", lane)
            ac.set("offset", fmt(ov_s))
            ac.set("name", audio_name)
            ac.set("start", fmt(a_st + (ov_s - a_off)))
            ac.set("duration", fmt(ov_e - ov_s))
            ac.set("format", a_fmt)
            ac.set("audioRole", "dialogue")

            ch = ET.SubElement(ac, "audio-channel-source")
            ch.set("srcCh", src_ch)
            ch.set("role", role)

            if sp != active_speaker:
                ac.set("enabled", "0")

            n_aud += 1

    print(f"  {n_aud} audio sub-clips (2 lanes)")

    # ---- audio-channel-source must come LAST per DTD ----
    for child in acs_children:
        parent.append(child)

    write_fcpxml(tree, output_path)
    print(f"  Written to {output_path}")


# ================================================================
# Filler detection
# ================================================================
def detect_fillers(
    audio_path: str,
    model_name: str,
    info: dict,
    output_path: str,
    language: str = "en",
) -> list[tuple]:
    """Detect filler words using whisper-mps. Also saves SRT + transcript."""
    from podcast.transcriber import (
        generate_srt,
        save_segments_json,
        save_transcript,
        transcribe_audio,
    )

    result = transcribe_audio(audio_path, model=model_name, language=language)

    fillers = []
    prev_end = 0.0

    for seg in result["segments"]:
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()

        if start - prev_end > MIN_PAUSE_SEC:
            fillers.append((prev_end, start - prev_end, "[PAUSE]"))

        words = re.findall(r"[a-z']+", text.lower())
        seg_dur = end - start
        n_words = max(len(words), 1)

        for i, w in enumerate(words):
            clean = w.strip(".,!?;:'\"")
            if clean in DEFINITE_FILLERS:
                word_time = start + (i / n_words) * seg_dur
                fillers.append((word_time, seg_dur / n_words, clean))

        prev_end = end

    out = Path(output_path)
    save_transcript(result["segments"], out.with_name(out.stem + "_transcript.txt"))
    save_segments_json(result["segments"], out.with_name(out.stem + "_segments.json"))

    audio_trim = float(info["audio_start"])
    generate_srt(result["segments"], out.with_suffix(".srt"), audio_trim=audio_trim)

    return fillers


def add_filler_markers(info: dict, fillers: list[tuple]) -> int:
    """Insert FCPXML markers for filler words on the parent clip."""
    parent = info["parent"]
    a_off = info["audio_offset"]
    a_st = info["audio_start"]
    parent_start = info["parent_start"]
    parent_end = parent_start + info["parent_dur"]

    # DTD order: connected-clips*, markers*, audio-channel-source*
    insert_idx = 0
    for i, child in enumerate(parent):
        if child.tag == "audio-channel-source":
            insert_idx = i
            break
    else:
        insert_idx = len(list(parent))

    n_markers = 0
    for filler_time, filler_dur, word in fillers:
        pst = a_off + Fraction(filler_time).limit_denominator(100000) - a_st
        pst = snap_to_frame(pst)

        if pst < parent_start or pst >= parent_end:
            continue

        marker = ET.Element("marker")
        marker.set("start", fmt(pst))
        marker.set("duration", fmt(FRAME_DUR))
        marker.set("value", word)

        parent.insert(insert_idx + n_markers, marker)
        n_markers += 1

    print(f"  {n_markers} markers added to timeline")
    return n_markers


# ================================================================
# Orchestrator
# ================================================================
def run_autoedit(
    fcpxml_file: str,
    audio_file: str,
    *,
    output: str | None = None,
    min_segment: float = 2.0,
    silence_db: float = -40,
    crossover_db: float = 3,
    fillers: bool = False,
    whisper_model: str = "base",
    language: str = "en",
) -> None:
    """Full autoedit pipeline."""
    fcpxml_path = Path(fcpxml_file)
    audio_path = Path(audio_file)

    if output is None:
        # Handle both .fcpxml and .fcpxmld/Info.fcpxml
        if fcpxml_path.name == "Info.fcpxml":
            stem = fcpxml_path.parent.stem.replace(".fcpxmld", "")
            output = str(fcpxml_path.parent.parent / f"{stem}_edited.fcpxml")
        else:
            output = str(fcpxml_path.with_stem(fcpxml_path.stem + "_edited"))

    n_steps = 5 if fillers else 4

    print("=" * 60)
    print("  Podcast Auto-Editor")
    print("=" * 60)

    # 0 ---- detect structure ----
    print(f"\n[0/{n_steps}] Parsing FCPXML: {fcpxml_file}")
    info = detect_structure(str(fcpxml_path))

    # 1 ---- extract audio ----
    print(f"\n[1/{n_steps}] Extracting audio channels ...")
    left, right = extract_channels(str(audio_path))
    dur = len(left) / SAMPLE_RATE
    print(f"  {dur / 60:.1f} min  ({len(left)} samples/ch)")

    # 2 ---- detect speakers ----
    print(f"\n[2/{n_steps}] Detecting speakers ...")
    raw = detect_speakers(left, right, silence_db, crossover_db)
    print(f"  {len(raw)} raw segments")

    # 3 ---- merge ----
    print(f"\n[3/{n_steps}] Merging (min {min_segment}s) ...")
    segments = merge_segments(raw, min_segment)
    print(f"  {len(segments)} final segments")

    a_t = sum(e - s for s, e, sp in segments if sp == "A")
    b_t = sum(e - s for s, e, sp in segments if sp == "B")
    tot = a_t + b_t
    print(f"\n  Speaker A (Cam A / L):  {a_t / 60:.1f} min  ({a_t / tot * 100:.0f}%)")
    print(f"  Speaker B (Cam B / R):  {b_t / 60:.1f} min  ({b_t / tot * 100:.0f}%)")
    print(f"  Camera switches:        {len(segments) - 1}")

    print("\n  First 15 segments:")
    for s, e, sp in segments[:15]:
        ms, ss = divmod(s, 60)
        me, se = divmod(e, 60)
        print(f"    {int(ms):02d}:{ss:05.2f} – {int(me):02d}:{se:05.2f}  [{sp}]  ({e - s:.1f}s)")
    if len(segments) > 15:
        print(f"    ... ({len(segments) - 15} more)")

    # 4 ---- filler words (optional) ----
    filler_list = []
    if fillers:
        print(f"\n[4/{n_steps}] Detecting filler words ...")
        filler_list = detect_fillers(
            str(audio_path), whisper_model, info, output, language=language
        )
        n_definite = sum(1 for _, _, w in filler_list if not w.startswith("?") and w != "[PAUSE]")
        n_pauses = sum(1 for _, _, w in filler_list if w == "[PAUSE]")
        print(f"  {n_definite} definite fillers (um, uh, ...)")
        print(f"  {n_pauses} long pauses (>{MIN_PAUSE_SEC}s)")

    # 5 ---- generate FCPXML ----
    step = n_steps
    print(f"\n[{step}/{n_steps}] Generating FCPXML ...")
    generate_fcpxml(segments, info, output, audio_name=audio_path.name)

    if filler_list:
        info2 = detect_structure(output)
        add_filler_markers(info2, filler_list)
        write_fcpxml(info2["tree"], output)

    print(f"\n{'=' * 60}")
    print("  Done!  In FCP:  File -> Import -> XML ...")
    print("  To change back: select any disabled clip -> re-enable it")
    if filler_list:
        print("  Filler markers: use Ctrl+' to jump between markers in FCP")
    print(f"{'=' * 60}")
