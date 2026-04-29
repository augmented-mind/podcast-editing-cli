"""CLI entry point for the podcast tool."""

import click


@click.group()
@click.version_option(package_name="podcast-cli")
def cli():
    """Podcast toolkit for transcription and auto-editing."""
    pass


@cli.command()
@click.argument("audio_file", type=click.Path(exists=True))
@click.option("--model", default="large", help="Whisper model: tiny/base/small/medium/large")
@click.option("--language", default="en", help="Language code (default: en)")
@click.option(
    "--output-dir", "-o", type=click.Path(),
    help="Output directory (default: same as input file)",
)
def transcribe(audio_file, model, language, output_dir):
    """Transcribe audio/video and generate SRT + transcript.

    Outputs: <stem>.srt, <stem>_transcript.txt, <stem>_segments.json
    """
    from podcast.transcriber import run_transcription

    run_transcription(audio_file, model=model, language=language, output_dir=output_dir)


@cli.command()
@click.argument("fcpxml_file", type=click.Path(exists=True))
@click.argument("audio_file", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(),
    help="Output FCPXML path (default: <input>_edited.fcpxml)",
)
@click.option("--min-segment", type=float, default=2.0, help="Min segment duration (seconds)")
@click.option("--silence-db", type=float, default=-40, help="Silence threshold (dB)")
@click.option("--crossover-db", type=float, default=3, help="dB difference to pick speaker")
@click.option("--fillers", is_flag=True, help="Add filler word markers via Whisper")
@click.option("--whisper-model", default="base", help="Whisper model for filler detection")
@click.option("--language", default="en", help="Language for filler transcription")
def autoedit(fcpxml_file, audio_file, output, min_segment, silence_db,
             crossover_db, fillers, whisper_model, language):
    """Auto-edit FCPXML with speaker-based camera switches and audio muting.

    FCPXML_FILE: exported .fcpxml or .fcpxmld/Info.fcpxml
    AUDIO_FILE: duo-mono audio (L=Speaker A/Camera A, R=Speaker B/Camera B)
    """
    from podcast.autoedit import run_autoedit

    run_autoedit(
        fcpxml_file, audio_file,
        output=output,
        min_segment=min_segment,
        silence_db=silence_db,
        crossover_db=crossover_db,
        fillers=fillers,
        whisper_model=whisper_model,
        language=language,
    )


@cli.command()
@click.argument("fcpxml_file", type=click.Path(exists=True))
@click.argument("overlay_dir", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--output", "-o", type=click.Path(),
    help="Output FCPXML path (default: <input>_overlays.fcpxml)",
)
@click.option("--duration", type=float, default=4.5, show_default=True,
              help="Overlay duration in seconds")
@click.option("--lane", type=int, default=10, show_default=True,
              help="Base positive lane for connected overlay clips")
@click.option("--ignore-unmatched", is_flag=True,
              help="Skip PNGs whose names do not start with a timestamp")
def overlays(fcpxml_file, overlay_dir, output, duration, lane, ignore_unmatched):
    """Insert timestamped PNG overlays into an FCPXML timeline.

    FCPXML_FILE may be a .fcpxml file, a .fcpxmld bundle, or
    .fcpxmld/Info.fcpxml. PNG names should start with M_SS or H_MM_SS, for
    example: 6_19.png -> 6 minutes 19 seconds.
    """
    from podcast.overlays import insert_overlays

    insert_overlays(
        fcpxml_file,
        overlay_dir,
        output=output,
        duration=duration,
        lane=lane,
        ignore_unmatched=ignore_unmatched,
    )
