"""FCPXML time parsing, structure detection, and XML I/O."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

FRAME_DUR = Fraction(1001, 30000)  # 29.97 fps


def parse_time(s: str) -> Fraction:
    """'1001/30000s' -> Fraction; '0s' -> Fraction(0)."""
    s = s.rstrip("s")
    if "/" in s:
        n, d = s.split("/")
        return Fraction(int(n), int(d))
    return Fraction(int(s))


def fmt(f: Fraction, frame_dur: Fraction | None = None) -> str:
    """Fraction -> FCPXML time string like '1001/30000s'.

    If ``frame_dur`` is provided, frame-aligned values are formatted with the
    sequence time base denominator so Final Cut recognizes them as exact frame
    boundaries.
    """
    f = Fraction(f)
    if f.denominator == 1:
        return f"{f.numerator}s"
    if frame_dur is not None:
        target_den = frame_dur.denominator
        if target_den % f.denominator == 0:
            mult = target_den // f.denominator
            return f"{f.numerator * mult}/{target_den}s"
    return f"{f.numerator}/{f.denominator}s"


def snap_to_frame(t: Fraction, frame_dur: Fraction = FRAME_DUR) -> Fraction:
    """Snap to nearest frame boundary."""
    n = round(t / frame_dur)
    return n * frame_dur


def detect_frame_duration(root: ET.Element) -> Fraction:
    """Detect the sequence frame duration, defaulting to 29.97fps."""
    sequence = root.find(".//sequence")
    seq_format_id = sequence.get("format") if sequence is not None else None

    if seq_format_id:
        for fmt_el in root.iter("format"):
            if fmt_el.get("id") == seq_format_id:
                frame_duration = fmt_el.get("frameDuration")
                if frame_duration:
                    return parse_time(frame_duration)
                break

    return FRAME_DUR


def detect_structure(fcpxml_path: str) -> dict:
    """Parse FCPXML and return structure info dict.

    Returns dict with keys: tree, root, spine, parent, parent_ref,
    parent_offset, parent_start, parent_dur, audio_clip, audio_ref,
    audio_offset, audio_start, audio_dur, cam_a_refs.
    """
    tree = ET.parse(fcpxml_path)
    root = tree.getroot()
    spine = root.find(".//spine")

    # Find the main parent clip (first enabled asset-clip on the spine)
    parent = None
    for el in spine:
        if el.tag == "asset-clip" and el.get("enabled") is None:
            parent = el
            break
    if parent is None:
        sys.exit("Could not find main clip in spine")

    parent_ref = parent.get("ref")
    parent_offset = parse_time(parent.get("offset", "0s"))
    parent_start = parse_time(parent.get("start", "0s"))
    parent_dur = parse_time(parent.get("duration"))

    # Find audio clip (lane=-1)
    audio_clip = None
    for child in parent:
        if child.tag == "asset-clip" and child.get("lane") == "-1":
            audio_clip = child
            break

    if audio_clip is None:
        sys.exit("Could not find audio clip (lane=-1) in parent")

    audio_ref = audio_clip.get("ref")
    audio_offset = parse_time(audio_clip.get("offset", "0s"))
    audio_start = parse_time(audio_clip.get("start", "0s"))
    audio_dur = parse_time(audio_clip.get("duration"))

    # Find Camera A clips (lane=1, enabled)
    cam_a_refs = set()
    for child in parent:
        if (
            child.tag == "asset-clip"
            and child.get("lane") == "1"
            and child.get("enabled") is None
        ):
            cam_a_refs.add(child.get("ref"))

    info = {
        "tree": tree,
        "root": root,
        "spine": spine,
        "parent": parent,
        "parent_ref": parent_ref,
        "parent_offset": parent_offset,
        "parent_start": parent_start,
        "parent_dur": parent_dur,
        "audio_clip": audio_clip,
        "audio_ref": audio_ref,
        "audio_offset": audio_offset,
        "audio_start": audio_start,
        "audio_dur": audio_dur,
        "cam_a_refs": cam_a_refs,
    }

    print(
        f"  Parent clip: ref={parent_ref}, offset={fmt(parent_offset)}, "
        f"start={fmt(parent_start)}, dur={float(parent_dur):.1f}s"
    )
    print(
        f"  Audio clip:  ref={audio_ref}, offset={fmt(audio_offset)}, "
        f"start={fmt(audio_start)}"
    )
    print(f"  Camera A refs: {cam_a_refs}")

    return info


def write_fcpxml(tree: ET.ElementTree, output_path: str) -> None:
    """Write FCPXML with correct XML declaration and DOCTYPE."""
    tree.write(output_path, xml_declaration=True, encoding="UTF-8")
    with open(output_path, "r") as f:
        txt = f.read()
    txt = txt.replace(
        "<?xml version='1.0' encoding='UTF-8'?>",
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>',
    )
    with open(output_path, "w") as f:
        f.write(txt)
