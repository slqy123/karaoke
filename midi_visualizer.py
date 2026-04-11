#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path

import click

from midi import Mora, read_midi


NOTE_COLORS: tuple[str, ...] = (
    "#FF0000",
    "#FF7700",
    "#FFFF00",
    "#00FF00",
    "#00FFFF",
    "#0000FF",
    "#FF00FF",
    "#FF0088",
    "#FF8800",
    "#88FF00",
    "#00FF88",
    "#0088FF",
)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def format_ass_time(ms: int) -> str:
    if ms < 0:
        ms = 0

    centiseconds = ms // 10
    seconds = centiseconds // 100
    minutes = seconds // 60
    hours = minutes // 60

    cs = centiseconds % 100
    s = seconds % 60
    m = minutes % 60
    h = hours

    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rgb_hex_to_ass_bgr(color: str) -> str:
    color = color.lstrip("#")
    return color[4:] + color[2:4] + color[:2]


def opacity_to_ass_alpha(opacity: float) -> str:
    opacity = clamp(opacity, 0.0, 1.0)
    alpha = int(round((1.0 - opacity) * 255.0))
    return f"{alpha:02X}"


def get_note_color(note: int) -> str:
    return NOTE_COLORS[note % len(NOTE_COLORS)]


def build_lane_geometry(
    note_count: int,
    band_height: float,
    overlap_ratio: float,
) -> tuple[float, float]:
    if note_count <= 1:
        height = max(0.06, band_height)
        return height, 0.0

    overlap_ratio = clamp(overlap_ratio, 0.0, 0.95)
    denom = 1.0 + (note_count - 1) * (1.0 - overlap_ratio)
    note_height = max(0.04, band_height / denom)
    lane_step = note_height * (1.0 - overlap_ratio)
    return note_height, lane_step


def get_note_y_position(
    note: int,
    max_note: int,
    top_y: float,
    band_height: float,
    note_height: float,
    lane_step: float,
) -> float:
    lane_idx = max_note - note
    y = top_y + note_height / 2 + lane_idx * lane_step
    lower = top_y + note_height / 2
    upper = top_y + band_height - note_height / 2
    return clamp(y, lower, upper)


def note_shape(width: int, height: int) -> str:
    return f"m 0 0 l {width} 0 {width} {height} 0 {height}"


def rounded_note_shape(width: int, height: int) -> str:
    width = max(1, int(width))
    height = max(1, int(height))

    radius = int(round(min(width, height) * 0.48))
    radius = max(1, min(radius, width // 2, height // 2))
    if radius <= 1:
        return note_shape(width, height)

    # Cubic Bezier approximation for a quarter circle.
    kappa = 0.55228475
    c = radius * kappa

    r = radius
    w = width
    h = height

    return (
        f"m {r} 0 "
        f"l {w - r} 0 "
        f"b {w - r + c:.2f} 0 {w:.2f} {r - c:.2f} {w} {r} "
        f"l {w} {h - r} "
        f"b {w:.2f} {h - r + c:.2f} {w - r + c:.2f} {h:.2f} {w - r} {h} "
        f"l {r} {h} "
        f"b {r - c:.2f} {h:.2f} 0 {h - r + c:.2f} 0 {h - r} "
        f"l 0 {r} "
        f"b 0 {r - c:.2f} {r - c:.2f} 0 {r} 0"
    )


def ass_header(width: int, height: int) -> str:
    return f"""[Script Info]
Title: MIDI Visualizer ASS
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, Strikeout, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Note,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1
Style: Judge,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def normalize_moras(moras: list[Mora]) -> tuple[list[Mora], int]:
    min_start_ms = min(m.start for m in moras)
    if min_start_ms >= 0:
        return moras, 0

    shift_ms = -min_start_ms
    return (
        [
            Mora(
                start=m.start + shift_ms,
                end=m.end + shift_ms,
                continous=m.continous,
                note=m.note,
            )
            for m in moras
        ],
        shift_ms,
    )


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []

    intervals.sort(key=lambda p: p[0])
    merged: list[tuple[int, int]] = [intervals[0]]
    for start_ms, end_ms in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start_ms <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end_ms))
        else:
            merged.append((start_ms, end_ms))
    return merged


def render_note_events(
    moras: list[Mora],
    width: int,
    height: int,
    scroll_speed: float,
    judge_ratio: float,
    band_ratio: float,
    lane_overlap: float,
    note_gap_s: float,
    note_opacity: float,
    show_band_background: bool,
) -> tuple[list[str], int]:
    if scroll_speed <= 0:
        raise ValueError("scroll_speed must be > 0")

    max_note = max(m.note for m in moras)
    note_count = max_note - min(m.note for m in moras) + 1

    judge_x = width * clamp(judge_ratio, 0.0, 1.0)
    band_height = height * clamp(band_ratio, 0.05, 1.0)
    note_height, lane_step = build_lane_geometry(note_count, band_height, lane_overlap)
    time_before_judge_s = (width - judge_x) / scroll_speed
    time_after_judge_s = judge_x / scroll_speed

    events: list[str] = []
    note_gap_s = max(0.0, note_gap_s)
    note_alpha = opacity_to_ass_alpha(note_opacity)
    min_note_width = 2
    last_end_ms = 0
    judge_active_intervals: list[tuple[int, int]] = []

    for mora in moras:
        start_s = mora.start / 1000
        end_s = mora.end / 1000
        last_end_ms = max(last_end_ms, mora.end)

        display_start_s = start_s + note_gap_s / 2
        display_end_s = end_s - note_gap_s / 2
        if display_end_s < display_start_s:
            mid = (start_s + end_s) / 2
            display_start_s = mid
            display_end_s = mid

        width_px = max(
            int(round(max(end_s - start_s - note_gap_s, 0.0) * scroll_speed)),
            min_note_width,
        )
        start_left_x = width
        end_left_x = -width_px

        y_pos = get_note_y_position(
            mora.note,
            max_note,
            0.0,
            band_height,
            note_height,
            lane_step,
        )
        top_y = int(round(y_pos - note_height / 2))
        shape_height = max(1, int(round(note_height)))

        fill_color = rgb_hex_to_ass_bgr(get_note_color(mora.note))
        outline_color = rgb_hex_to_ass_bgr("#FFFFFF")
        unclipped_start_ms = int(round((display_start_s - time_before_judge_s) * 1000))
        unclipped_end_ms = int(round((display_end_s + time_after_judge_s) * 1000))
        start_ms = max(0, unclipped_start_ms)
        end_ms = max(start_ms + 1, unclipped_end_ms)

        move_start_x = start_left_x
        if unclipped_start_ms < 0 and unclipped_end_ms > unclipped_start_ms:
            # If the note should have started before t=0, keep its speed by starting
            # at the interpolated x-position at t=0 instead of resetting to the edge.
            progress = clamp(
                (0 - unclipped_start_ms) / (unclipped_end_ms - unclipped_start_ms),
                0.0,
                1.0,
            )
            move_start_x = int(round(start_left_x + (end_left_x - start_left_x) * progress))

        move_tag = f"\\move({move_start_x},{top_y},{end_left_x},{top_y})"
        # Judge highlight follows raw MIDI time windows instead of visual note gaps.
        judge_active_start_ms = max(0, int(round(start_s * 1000)))
        judge_active_end_ms = max(judge_active_start_ms + 1, int(round(end_s * 1000)))
        judge_active_intervals.append((judge_active_start_ms, judge_active_end_ms))
        last_end_ms = max(last_end_ms, end_ms)

        gray_fill_color = rgb_hex_to_ass_bgr("#787878")
        gray_outline_color = rgb_hex_to_ass_bgr("#A0A0A0")

        base_text = (
            f"{{\\an7\\bord1\\shad0\\1c&H{gray_fill_color}&\\3c&H{gray_outline_color}&"
            f"\\1a&H{note_alpha}&\\3a&H{note_alpha}&"
            f"{move_tag}\\p1}}"
            f"{rounded_note_shape(width_px, shape_height)}"
            f"{{\\p0}}"
        )
        events.append(
            f"Dialogue: 0,{format_ass_time(start_ms)},{format_ass_time(end_ms)},Note,,0,0,0,,{base_text}"
        )

        color_overlay_text = (
            f"{{\\an7\\bord1\\shad0\\1c&H{fill_color}&\\3c&H{outline_color}&"
            f"\\1a&H{note_alpha}&\\3a&H{note_alpha}&"
            f"\\clip({int(round(judge_x))},0,{width},{height})"
            f"{move_tag}\\p1}}"
            f"{rounded_note_shape(width_px, shape_height)}"
            f"{{\\p0}}"
        )
        events.append(
            f"Dialogue: 1,{format_ass_time(start_ms)},{format_ass_time(end_ms)},Note,,0,0,0,,{color_overlay_text}"
        )

    judge_width = 4
    judge_left_x = int(round(judge_x - judge_width / 2))
    judge_height = max(1, int(round(band_height)))
    judge_base_color = rgb_hex_to_ass_bgr("#545454")
    judge_highlight_color = rgb_hex_to_ass_bgr("#FFD200")
    video_tail_ms = max(1, int(round(1000 / 60)))
    judge_end_ms = max(last_end_ms + video_tail_ms, 1)

    if show_band_background:
        # Keep this background lighter than the judge line and semi-transparent.
        band_bg_color = rgb_hex_to_ass_bgr("#7A7A7A")
        band_bg_alpha = opacity_to_ass_alpha(0.35)
        events.append(
            "Dialogue: -10,0:00:00.00,"
            f"{format_ass_time(judge_end_ms)},"
            f"Judge,,0,0,0,,{{\\an7\\bord0\\shad0\\1c&H{band_bg_color}&\\1a&H{band_bg_alpha}&\\p1\\pos(0,0)}}"
            f"{note_shape(width, judge_height)}{{\\p0}}"
        )

    events.append(
        "Dialogue: 10,0:00:00.00,"
        f"{format_ass_time(judge_end_ms)},"
        f"Judge,,0,0,0,,{{\\an7\\bord0\\shad0\\1c&H{judge_base_color}&\\p1\\pos({judge_left_x},0)}}"
        f"{note_shape(judge_width, judge_height)}{{\\p0}}"
    )

    for active_start_ms, active_end_ms in merge_intervals(judge_active_intervals):
        events.append(
            f"Dialogue: 11,{format_ass_time(active_start_ms)},{format_ass_time(active_end_ms)},"
            f"Judge,,0,0,0,,{{\\an7\\bord0\\shad0\\1c&H{judge_highlight_color}&\\p1\\pos({judge_left_x},0)}}"
            f"{note_shape(judge_width, judge_height)}{{\\p0}}"
        )

    return events, last_end_ms


def build_ass_document(
    moras: list[Mora],
    width: int,
    height: int,
    scroll_speed: float,
    judge_ratio: float,
    band_ratio: float,
    lane_overlap: float,
    note_gap_s: float,
    note_opacity: float,
    show_band_background: bool,
) -> tuple[str, int]:
    events, last_end_ms = render_note_events(
        moras=moras,
        width=width,
        height=height,
        scroll_speed=scroll_speed,
        judge_ratio=judge_ratio,
        band_ratio=band_ratio,
        lane_overlap=lane_overlap,
        note_gap_s=note_gap_s,
        note_opacity=note_opacity,
        show_band_background=show_band_background,
    )
    return ass_header(width, height) + "\n".join(events) + "\n", last_end_ms


@click.command()
@click.argument("midi_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "output_path",
    default=Path("midi_visualization.ass"),
    type=click.Path(path_type=Path),
    show_default=True,
    help="Output ASS file path",
)
@click.option("--offset-ms", default=0, show_default=True, type=int, help="MIDI time offset in milliseconds")
@click.option("--scroll-speed", default=500.0, show_default=True, type=float, help="Horizontal scroll speed in pixels per second")
@click.option("--judge-ratio", default=0.35, show_default=True, type=float, help="Judge line position as a ratio of canvas width")
@click.option("--band-ratio", default=0.4, show_default=True, type=float, help="Vertical ratio used for the note band")
@click.option("--band-bg/--no-band-bg", default=False, show_default=True, help="Add a light semi-transparent gray background in the band area")
@click.option("--lane-overlap", default=0.25, show_default=True, type=float, help="Vertical overlap ratio between note lanes")
@click.option("--note-gap-ms", default=30, show_default=True, type=int, help="Trimmed milliseconds from note edges")
@click.option("--opacity", default=0.7, show_default=True, type=float, help="Note opacity in range [0,1]")
@click.option("--width", default=1920, show_default=True, type=int, help="Canvas width in pixels")
@click.option("--height", default=1080, show_default=True, type=int, help="Canvas height in pixels")
def main(
    midi_file: Path,
    output_path: Path,
    offset_ms: int,
    scroll_speed: float,
    judge_ratio: float,
    band_ratio: float,
    band_bg: bool,
    lane_overlap: float,
    note_gap_ms: int,
    opacity: float,
    width: int,
    height: int,
) -> None:
    moras = list(read_midi(midi_file, offset_ms=offset_ms))
    if not moras:
        raise RuntimeError("No notes parsed from MIDI")

    moras, shift_ms = normalize_moras(moras)
    if shift_ms:
        click.echo(f"[*] Shifted note times by +{shift_ms} ms to normalize MIDI start")

    click.echo(f"[*] Loaded {len(moras)} notes")
    click.echo(f"[*] Note range: {min(m.note for m in moras)} - {max(m.note for m in moras)}")

    ass_text, _last_end_ms = build_ass_document(
        moras=moras,
        width=width,
        height=height,
        scroll_speed=scroll_speed,
        judge_ratio=judge_ratio,
        band_ratio=band_ratio,
        lane_overlap=lane_overlap,
        note_gap_s=max(0.0, note_gap_ms / 1000),
        note_opacity=opacity,
        show_band_background=band_bg,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ass_text, encoding="utf-8")
    click.echo(f"[+] ASS written to {output_path}")


if __name__ == "__main__":
    main()
