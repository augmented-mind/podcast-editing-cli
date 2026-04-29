"""FCPXML overlay insertion for timestamped PNG stills."""

from __future__ import annotations

import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from podcast.fcpxml import (
    FRAME_DUR,
    detect_frame_duration,
    fmt,
    parse_time,
    snap_to_frame,
    write_fcpxml,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TIMESTAMP_RE = re.compile(
    r"^(?P<a>\d+)[_:-](?P<b>\d{1,2})(?:[_:-](?P<c>\d{1,2}(?:\.\d+)?))?(?:[ _-].*)?$"
)
CONNECTED_CLIP_BOUNDARY_TAGS = {"marker", "audio-channel-source"}
PRIMARY_STORYLINE_TAGS = {"asset-clip", "clip", "mc-clip", "sync-clip", "gap"}


@dataclass(frozen=True)
class OverlayClip:
    """A PNG overlay and its start time in timeline seconds."""

    path: Path
    start: Fraction
    width: int
    height: int


@dataclass(frozen=True)
class InsertedOverlay:
    """Information about an overlay that was inserted into the timeline."""

    path: Path
    start: Fraction
    duration: Fraction
    lane: int
    asset_id: str


def parse_timestamp(stem: str) -> Fraction | None:
    """Parse a timestamp from a PNG stem.

    Supported forms:
    - ``M_SS`` / ``MM_SS`` for minute-second timestamps, e.g. ``6_19``
    - ``H_MM_SS`` for hour-minute-second timestamps, e.g. ``1_02_03``
    - ``:`` or ``-`` may be used instead of ``_``
    - descriptive suffixes are allowed, e.g. ``6_19_lower_third``
    """
    match = TIMESTAMP_RE.match(stem)
    if not match:
        return None

    a = int(match.group("a"))
    b = int(match.group("b"))
    c = match.group("c")

    if c is None:
        minutes = a
        seconds = Fraction(b, 1)
        if b >= 60:
            return None
        return Fraction(minutes * 60, 1) + seconds

    hours = a
    minutes = b
    seconds = Fraction(c)
    if minutes >= 60 or seconds >= 60:
        return None
    return Fraction(hours * 3600 + minutes * 60, 1) + seconds


def png_dimensions(path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` for a PNG by reading its IHDR chunk."""
    with path.open("rb") as f:
        header = f.read(24)

    if len(header) < 24 or not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        raise ValueError(f"Not a valid PNG file: {path}")

    width, height = struct.unpack(">II", header[16:24])
    return width, height


def resolve_fcpxml_path(fcpxml_file: str | Path) -> Path:
    """Resolve a .fcpxml file or .fcpxmld bundle to an XML file path."""
    path = Path(fcpxml_file)
    if path.is_dir():
        info = path / "Info.fcpxml"
        if info.exists():
            return info
        sys.exit(f"FCPXML bundle does not contain Info.fcpxml: {path}")
    return path


def collect_overlays(overlay_dir: str | Path, *, ignore_unmatched: bool = False) -> list[OverlayClip]:
    """Collect timestamped PNG overlays from a directory."""
    root = Path(overlay_dir)
    if not root.is_dir():
        sys.exit(f"Overlay directory does not exist: {root}")

    overlays: list[OverlayClip] = []
    unmatched: list[Path] = []

    for path in sorted(root.glob("*.png")):
        start = parse_timestamp(path.stem)
        if start is None:
            unmatched.append(path)
            continue
        width, height = png_dimensions(path)
        overlays.append(OverlayClip(path=path, start=start, width=width, height=height))

    if unmatched and not ignore_unmatched:
        names = ", ".join(p.name for p in unmatched[:5])
        more = "" if len(unmatched) <= 5 else f" (+{len(unmatched) - 5} more)"
        sys.exit(
            "Could not parse timestamps from PNG file names: "
            f"{names}{more}. Expected names like 6_19.png or 1_02_03.png."
        )

    if not overlays:
        sys.exit(f"No timestamped PNG overlays found in {root}")

    return sorted(overlays, key=lambda o: (o.start, o.path.name))


def _next_resource_id(root: ET.Element) -> str:
    max_id = 0
    for el in root.iter():
        resource_id = el.get("id")
        if resource_id and resource_id.startswith("r") and resource_id[1:].isdigit():
            max_id = max(max_id, int(resource_id[1:]))
    return f"r{max_id + 1}"


def _append_resource_format(resources: ET.Element, format_el: ET.Element) -> None:
    """Insert a format resource after existing formats, before assets/media."""
    children = list(resources)
    insert_idx = 0
    for i, child in enumerate(children):
        if child.tag == "format":
            insert_idx = i + 1
    resources.insert(insert_idx, format_el)


def _sequence_format(root: ET.Element) -> ET.Element | None:
    sequence = root.find(".//sequence")
    if sequence is None:
        return None

    format_id = sequence.get("format")
    if not format_id:
        return None

    for format_el in root.iter("format"):
        if format_el.get("id") == format_id:
            return format_el
    return None


def _ensure_image_format(
    root: ET.Element,
    resources: ET.Element,
    format_ids: dict[tuple[int, int], str],
    width: int,
    height: int,
) -> str:
    key = (width, height)
    if key in format_ids:
        return format_ids[key]

    color_space = None
    seq_format = _sequence_format(root)
    if seq_format is not None:
        color_space = seq_format.get("colorSpace")

    format_id = _next_resource_id(root)
    format_el = ET.Element("format")
    format_el.set("id", format_id)
    format_el.set("name", "FFVideoFormatRateUndefined")
    format_el.set("width", str(width))
    format_el.set("height", str(height))
    if color_space:
        format_el.set("colorSpace", color_space)

    _append_resource_format(resources, format_el)
    format_ids[key] = format_id
    return format_id


def _append_overlay_asset(
    root: ET.Element,
    resources: ET.Element,
    overlay: OverlayClip,
    format_id: str,
    duration: Fraction,
    frame_dur: Fraction,
) -> str:
    asset_id = _next_resource_id(root)
    src = overlay.path.resolve().as_uri()

    asset = ET.Element("asset")
    asset.set("id", asset_id)
    asset.set("name", overlay.path.name)
    # Leave uid unset for newly introduced media. Final Cut assigns a stable
    # media uid on import from the media-rep src; using a made-up uid can make
    # the importer try to resolve a non-existent library object.
    asset.set("start", "0s")
    # Although still-image assets often export with a zero duration, Final Cut's
    # XML importer can crash when a newly declared still asset is immediately
    # used by an asset-clip whose duration is longer than the asset duration.
    # Give each generated still asset the duration we use on the timeline.
    asset.set("duration", fmt(duration, frame_dur))
    asset.set("hasVideo", "1")
    asset.set("format", format_id)
    asset.set("videoSources", "1")

    media_rep = ET.SubElement(asset, "media-rep")
    media_rep.set("kind", "original-media")
    media_rep.set("src", src)

    resources.append(asset)
    return asset_id


def find_primary_container(root: ET.Element) -> ET.Element:
    """Find the primary storyline item to receive connected overlay clips."""
    spine = root.find(".//spine")
    if spine is None:
        sys.exit("Could not find <spine> in FCPXML")

    for child in spine:
        if child.tag in PRIMARY_STORYLINE_TAGS and child.get("lane") is None:
            return child

    sys.exit("Could not find a primary storyline clip in the spine")


def _connected_clip_insert_index(container: ET.Element) -> int:
    """Return where connected clips can be inserted without violating FCPXML order."""
    children = list(container)
    for i, child in enumerate(children):
        if child.tag in CONNECTED_CLIP_BOUNDARY_TAGS:
            return i
    return len(children)


def _assign_lanes(intervals: list[tuple[Fraction, Fraction]], base_lane: int) -> list[int]:
    lane_ends: dict[int, Fraction] = {}
    lanes: list[int] = []

    for start, end in intervals:
        lane = base_lane
        while lane in lane_ends and start < lane_ends[lane]:
            lane += 1
        lane_ends[lane] = end
        lanes.append(lane)

    return lanes


def insert_overlays(
    fcpxml_file: str | Path,
    overlay_dir: str | Path,
    *,
    output: str | Path | None = None,
    duration: float = 4.5,
    lane: int = 10,
    ignore_unmatched: bool = False,
) -> list[InsertedOverlay]:
    """Insert timestamped PNG overlays into an FCPXML timeline."""
    fcpxml_input_path = Path(fcpxml_file)
    fcpxml_path = resolve_fcpxml_path(fcpxml_input_path)
    if output is None:
        # Handle .fcpxml, .fcpxmld bundles, and .fcpxmld/Info.fcpxml.
        if fcpxml_input_path.is_dir():
            output = fcpxml_input_path.with_name(
                f"{fcpxml_input_path.stem.replace('.fcpxmld', '')}_overlays.fcpxml"
            )
        elif fcpxml_path.name == "Info.fcpxml":
            stem = fcpxml_path.parent.stem.replace(".fcpxmld", "")
            output = fcpxml_path.parent.parent / f"{stem}_overlays.fcpxml"
        else:
            output = fcpxml_path.with_stem(fcpxml_path.stem + "_overlays")
    output_path = Path(output)

    overlays = collect_overlays(overlay_dir, ignore_unmatched=ignore_unmatched)

    tree = ET.parse(fcpxml_path)
    root = tree.getroot()
    resources = root.find("resources")
    if resources is None:
        sys.exit("Could not find <resources> in FCPXML")

    frame_dur = detect_frame_duration(root) or FRAME_DUR
    clip_duration = snap_to_frame(Fraction(str(duration)), frame_dur)
    if clip_duration <= 0:
        sys.exit("Overlay duration must be greater than 0")

    container = find_primary_container(root)
    container_start = parse_time(container.get("start", "0s"))
    container_duration = parse_time(container.get("duration"))
    container_end = container_start + container_duration

    prepared: list[tuple[OverlayClip, Fraction, Fraction, str, str]] = []
    format_ids: dict[tuple[int, int], str] = {}

    for overlay in overlays:
        start = snap_to_frame(container_start + overlay.start, frame_dur)
        end = min(start + clip_duration, container_end)
        if start >= container_end or end <= start:
            print(
                f"  Skipping {overlay.path.name}: "
                f"starts after the primary clip ends ({float(container_duration):.1f}s)"
            )
            continue

        format_id = _ensure_image_format(root, resources, format_ids, overlay.width, overlay.height)
        dur = end - start
        asset_id = _append_overlay_asset(root, resources, overlay, format_id, dur, frame_dur)
        prepared.append((overlay, start, dur, asset_id, format_id))

    if not prepared:
        sys.exit("No overlays fell within the primary clip duration")

    intervals = [(start, start + dur) for _, start, dur, _, _ in prepared]
    lanes = _assign_lanes(intervals, lane)

    insert_idx = _connected_clip_insert_index(container)
    inserted: list[InsertedOverlay] = []

    for i, ((overlay, start, dur, asset_id, format_id), assigned_lane) in enumerate(
        zip(prepared, lanes)
    ):
        # Use <video> rather than <asset-clip> for generated still overlays.
        # FCPXML's asset-clip importer is brittle for newly declared stills and
        # can crash Final Cut during XML import; <video> is the lower-level
        # video-only story element for ranges from an asset.
        video = ET.Element("video")
        video.set("ref", asset_id)
        video.set("lane", str(assigned_lane))
        video.set("offset", fmt(start, frame_dur))
        video.set("name", overlay.path.name)
        video.set("start", "0s")
        video.set("duration", fmt(dur, frame_dur))
        video.set("role", "video")

        container.insert(insert_idx + i, video)
        inserted.append(
            InsertedOverlay(
                path=overlay.path,
                start=start - container_start,
                duration=dur,
                lane=assigned_lane,
                asset_id=asset_id,
            )
        )

    write_fcpxml(tree, str(output_path))

    print("=" * 60)
    print("  FCPXML Overlay Inserter")
    print("=" * 60)
    print(f"  Input XML:    {fcpxml_path}")
    print(f"  Overlay dir:  {Path(overlay_dir)}")
    print(f"  Output XML:   {output_path}")
    print(f"  Duration:     {float(clip_duration):.2f}s ({fmt(clip_duration, frame_dur)})")
    print(f"  Base lane:    {lane}")
    print(f"  Inserted:     {len(inserted)} overlay(s)")
    print("\n  First overlays:")
    for item in inserted[:10]:
        total_seconds = float(item.start)
        minutes, seconds = divmod(total_seconds, 60)
        print(
            f"    {int(minutes):02d}:{seconds:05.2f}  "
            f"lane {item.lane:<2}  {item.path.name}"
        )
    if len(inserted) > 10:
        print(f"    ... ({len(inserted) - 10} more)")
    print("\n  Done! In FCP: File -> Import -> XML ...")
    print("=" * 60)

    return inserted
