"""FCPXML overlay insertion for timestamped PNG stills."""

from __future__ import annotations

import re
import struct
import sys
import uuid
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


@dataclass(frozen=True)
class TimelineAnchor:
    """Top-level primary storyline item used to anchor connected overlays."""

    container: ET.Element
    timeline_start: Fraction
    timeline_end: Fraction
    local_start: Fraction


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


def find_project_sequence(root: ET.Element) -> ET.Element:
    """Find the project sequence, not a nested/resource sequence."""
    sequence = root.find(".//project/sequence")
    if sequence is None:
        sequence = root.find(".//sequence")
    if sequence is None:
        sys.exit("Could not find <sequence> in FCPXML")
    return sequence


def find_timeline_spine(root: ET.Element) -> ET.Element:
    """Find the top-level project timeline spine."""
    sequence = find_project_sequence(root)
    spine = sequence.find("spine")
    if spine is None:
        sys.exit("Could not find project <spine> in FCPXML")
    return spine


def find_timeline_anchors(root: ET.Element) -> list[TimelineAnchor]:
    """Find primary storyline items that can anchor overlays.

    Overlay file names are interpreted in project/sequence timeline seconds.
    FCPXML connected clips live inside the primary storyline item they are
    anchored to, so we translate each project timeline timestamp into that
    item's local timeline when writing the child ``offset``.
    """
    anchors: list[TimelineAnchor] = []
    spine = find_timeline_spine(root)

    for child in spine:
        if child.tag not in PRIMARY_STORYLINE_TAGS or child.get("lane") is not None:
            continue
        duration = child.get("duration")
        if duration is None:
            continue
        timeline_start = parse_time(child.get("offset", "0s"))
        local_start = parse_time(child.get("start", "0s"))
        timeline_duration = parse_time(duration)
        anchors.append(
            TimelineAnchor(
                container=child,
                timeline_start=timeline_start,
                timeline_end=timeline_start + timeline_duration,
                local_start=local_start,
            )
        )

    if not anchors:
        sys.exit("Could not find a primary storyline item in the project spine")

    return anchors


def find_anchor_for_timeline_time(
    anchors: list[TimelineAnchor], timeline_time: Fraction
) -> TimelineAnchor | None:
    """Return the primary storyline item covering ``timeline_time``."""
    for anchor in anchors:
        if anchor.timeline_start <= timeline_time < anchor.timeline_end:
            return anchor
    return None


def _connected_clip_insert_index(container: ET.Element) -> int:
    """Return where connected clips can be inserted without violating FCPXML order."""
    children = list(container)
    for i, child in enumerate(children):
        if child.tag in CONNECTED_CLIP_BOUNDARY_TAGS:
            return i
    return len(children)


def _retitle_project(root: ET.Element, output_path: Path) -> None:
    """Give the imported timeline its own project identity.

    FCPXML exports keep the original project UID/name. If we import an edited XML
    with the same identity, Final Cut can make it look like the old/original
    project is being reused. Assigning a fresh project UID and output-based name
    makes the generated timeline clearly independent.
    """
    project = root.find(".//project")
    if project is None:
        return
    project.set("name", output_path.stem)
    project.set("uid", str(uuid.uuid4()).upper())


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

    anchors = find_timeline_anchors(root)

    prepared: list[tuple[OverlayClip, TimelineAnchor, Fraction, Fraction, Fraction, str]] = []
    format_ids: dict[tuple[int, int], str] = {}

    for overlay in overlays:
        timeline_start = snap_to_frame(overlay.start, frame_dur)
        anchor = find_anchor_for_timeline_time(anchors, timeline_start)
        if anchor is None:
            print(
                f"  Skipping {overlay.path.name}: "
                f"{float(timeline_start):.2f}s is not covered by the primary storyline"
            )
            continue

        local_start = snap_to_frame(
            anchor.local_start + (timeline_start - anchor.timeline_start), frame_dur
        )
        local_end = anchor.local_start + (anchor.timeline_end - anchor.timeline_start)
        dur = min(clip_duration, local_end - local_start)
        if dur <= 0:
            print(
                f"  Skipping {overlay.path.name}: "
                "no room before the end of the primary storyline item"
            )
            continue

        format_id = _ensure_image_format(root, resources, format_ids, overlay.width, overlay.height)
        asset_id = _append_overlay_asset(root, resources, overlay, format_id, dur, frame_dur)
        prepared.append((overlay, anchor, timeline_start, local_start, dur, asset_id))

    if not prepared:
        sys.exit("No overlays fell within the project timeline")

    intervals = [(timeline_start, timeline_start + dur) for _, _, timeline_start, _, dur, _ in prepared]
    lanes = _assign_lanes(intervals, lane)

    insert_indices: dict[int, int] = {}
    insert_counts: dict[int, int] = {}
    inserted: list[InsertedOverlay] = []

    for (overlay, anchor, timeline_start, local_start, dur, asset_id), assigned_lane in zip(
        prepared, lanes
    ):
        # Use <video> rather than <asset-clip> for generated still overlays.
        # FCPXML's asset-clip importer is brittle for newly declared stills and
        # can crash Final Cut during XML import; <video> is the lower-level
        # video-only story element for ranges from an asset.
        video = ET.Element("video")
        video.set("ref", asset_id)
        video.set("lane", str(assigned_lane))
        video.set("offset", fmt(local_start, frame_dur))
        video.set("name", overlay.path.name)
        video.set("start", "0s")
        video.set("duration", fmt(dur, frame_dur))
        video.set("role", "video")

        container = anchor.container
        container_id = id(container)
        if container_id not in insert_indices:
            insert_indices[container_id] = _connected_clip_insert_index(container)
            insert_counts[container_id] = 0
        insert_at = insert_indices[container_id] + insert_counts[container_id]
        container.insert(insert_at, video)
        insert_counts[container_id] += 1
        inserted.append(
            InsertedOverlay(
                path=overlay.path,
                start=timeline_start,
                duration=dur,
                lane=assigned_lane,
                asset_id=asset_id,
            )
        )

    _retitle_project(root, output_path)
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
