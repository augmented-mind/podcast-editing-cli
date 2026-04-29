from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from podcast.fcpxml import parse_time
from podcast.overlays import insert_overlays, parse_timestamp, png_dimensions


def write_png(path: Path, width: int = 1920, height: int = 1080) -> None:
    # Enough of a PNG for the IHDR parser used by the CLI.
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def write_fcpxml(path: Path) -> None:
    path.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE fcpxml>
<fcpxml version=\"1.11\">
  <resources>
    <format id=\"r1\" name=\"FFVideoFormat1080p30\" frameDuration=\"1/30s\" width=\"1920\" height=\"1080\" colorSpace=\"1-1-1 (Rec. 709)\" />
    <asset id=\"r2\" name=\"source.mov\" start=\"0s\" duration=\"120s\" hasVideo=\"1\" format=\"r1\" videoSources=\"1\" />
  </resources>
  <library>
    <event name=\"Event\">
      <project name=\"Project\">
        <sequence format=\"r1\" duration=\"120s\" tcStart=\"0s\" tcFormat=\"NDF\">
          <spine>
            <asset-clip ref=\"r2\" offset=\"0s\" name=\"source.mov\" start=\"0s\" duration=\"120s\" format=\"r1\">
              <marker start=\"10s\" duration=\"1/30s\" value=\"marker\" />
            </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    )


def test_parse_timestamp() -> None:
    assert parse_timestamp("0_00") == Fraction(0)
    assert parse_timestamp("6_19") == Fraction(379)
    assert parse_timestamp("10-30-title") == Fraction(630)
    assert parse_timestamp("1_02_03") == Fraction(3723)
    assert parse_timestamp("1_02_03.5_card") == Fraction(7447, 2)
    assert parse_timestamp("6_99") is None
    assert parse_timestamp("not_a_timestamp") is None


def test_png_dimensions(tmp_path: Path) -> None:
    image = tmp_path / "6_19.png"
    write_png(image, 123, 456)

    assert png_dimensions(image) == (123, 456)


def test_insert_overlays(tmp_path: Path) -> None:
    fcpxml = tmp_path / "timeline.fcpxml"
    overlay_dir = tmp_path / "overlay"
    output = tmp_path / "timeline_overlays.fcpxml"
    overlay_dir.mkdir()
    write_fcpxml(fcpxml)
    write_png(overlay_dir / "0_00.png")
    write_png(overlay_dir / "0_02.png")

    inserted = insert_overlays(fcpxml, overlay_dir, output=output, duration=4.5, lane=10)

    assert [item.path.name for item in inserted] == ["0_00.png", "0_02.png"]
    # The second overlay overlaps the first, so it should be moved to the next lane.
    assert [item.lane for item in inserted] == [10, 11]

    tree = ET.parse(output)
    root = tree.getroot()
    resources = root.find("resources")
    assert resources is not None

    overlay_assets = [
        el for el in resources.findall("asset")
        if el.get("name") in {"0_00.png", "0_02.png"}
    ]
    assert len(overlay_assets) == 2
    assert all(asset.find("media-rep") is not None for asset in overlay_assets)
    assert all(parse_time(asset.get("duration")) == Fraction(9, 2) for asset in overlay_assets)

    primary = root.find(".//spine/asset-clip")
    assert primary is not None
    children = list(primary)
    marker_index = next(i for i, child in enumerate(children) if child.tag == "marker")
    overlay_clips = [child for child in children if child.tag == "video"]
    assert len(overlay_clips) == 2
    assert all(children.index(child) < marker_index for child in overlay_clips)

    assert parse_time(overlay_clips[0].get("offset")) == Fraction(0)
    assert parse_time(overlay_clips[0].get("duration")) == Fraction(9, 2)
    assert overlay_clips[0].get("lane") == "10"
    assert overlay_clips[0].get("role") == "video"
    assert overlay_clips[1].get("lane") == "11"


def test_insert_overlays_accepts_fcpxmld_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "main-snapshot.fcpxmld"
    overlay_dir = tmp_path / "overlay"
    output = tmp_path / "timeline_overlays.fcpxml"
    bundle.mkdir()
    overlay_dir.mkdir()
    write_fcpxml(bundle / "Info.fcpxml")
    write_png(overlay_dir / "0_00.png")

    inserted = insert_overlays(bundle, overlay_dir, output=output, duration=4.5)

    assert output.exists()
    assert [item.path.name for item in inserted] == ["0_00.png"]


def test_insert_overlays_can_add_fade_in(tmp_path: Path) -> None:
    fcpxml = tmp_path / "timeline.fcpxml"
    overlay_dir = tmp_path / "overlay"
    output = tmp_path / "timeline_overlays.fcpxml"
    overlay_dir.mkdir()
    write_fcpxml(fcpxml)
    write_png(overlay_dir / "0_00.png")

    insert_overlays(fcpxml, overlay_dir, output=output, duration=4.5, fade_in=0.5)

    root = ET.parse(output).getroot()
    video = root.find(".//spine/asset-clip/video")
    assert video is not None
    blend = video.find("adjust-blend")
    assert blend is not None
    assert blend.get("amount") == "1"
    param = blend.find("param")
    assert param is not None
    assert param.get("name") == "amount"
    keyframes = param.findall("keyframeAnimation/keyframe")
    assert [(kf.get("time"), kf.get("value")) for kf in keyframes] == [
        ("0s", "0"),
        ("15/30s", "1"),
    ]


def test_insert_overlays_uses_project_timeline_across_primary_items(tmp_path: Path) -> None:
    fcpxml = tmp_path / "timeline.fcpxml"
    overlay_dir = tmp_path / "overlay"
    output = tmp_path / "timeline_overlays.fcpxml"
    overlay_dir.mkdir()
    fcpxml.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE fcpxml>
<fcpxml version=\"1.11\">
  <resources>
    <format id=\"r1\" name=\"FFVideoFormat1080p30\" frameDuration=\"1/30s\" width=\"1920\" height=\"1080\" />
    <asset id=\"r2\" name=\"first.mov\" start=\"0s\" duration=\"60s\" hasVideo=\"1\" format=\"r1\" videoSources=\"1\" />
    <asset id=\"r3\" name=\"second.mov\" start=\"100s\" duration=\"60s\" hasVideo=\"1\" format=\"r1\" videoSources=\"1\" />
  </resources>
  <library>
    <event name=\"Event\">
      <project name=\"Project\">
        <sequence format=\"r1\" duration=\"120s\" tcStart=\"0s\" tcFormat=\"NDF\">
          <spine>
            <asset-clip ref=\"r2\" offset=\"0s\" name=\"first.mov\" start=\"0s\" duration=\"60s\" format=\"r1\" />
            <asset-clip ref=\"r3\" offset=\"60s\" name=\"second.mov\" start=\"100s\" duration=\"60s\" format=\"r1\" />
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    )
    write_png(overlay_dir / "1_05.png")

    inserted = insert_overlays(fcpxml, overlay_dir, output=output, duration=4.5)

    assert [item.start for item in inserted] == [Fraction(65)]
    root = ET.parse(output).getroot()
    primary_items = root.findall(".//spine/asset-clip")
    assert len(primary_items[0].findall("video")) == 0
    second_videos = primary_items[1].findall("video")
    assert len(second_videos) == 1
    # 65s on the project timeline maps to 105s in the second clip's local timeline
    # because that primary item starts at 60s in the project and 100s locally.
    assert parse_time(second_videos[0].get("offset")) == Fraction(105)
