from __future__ import annotations

from pathlib import Path
from typing import Iterable

import click
from manim import (
    BLUE_E,
    GREEN_C,
    ORANGE,
    PURPLE_B,
    RIGHT,
    UP,
    DOWN,
    Line,
    RoundedRectangle,
    Scene,
    VGroup,
    ValueTracker,
    config,
    linear,
    tempconfig,
    rgb_to_color,
    interpolate_color,
)
from manim.utils.color import rgb_to_hex

from midi import Mora, read_midi


# MIDI note number to color mapping
def get_note_color(note: int):
    """Map MIDI note number to color using a rainbow spectrum."""
    # Use note % 12 for octave-independent coloring (C=0, C#=1, ..., B=11)
    pitch_class = note % 12
    colors = [
        "#FF0000",  # C (红)
        "#FF7700",  # C#
        "#FFFF00",  # D (黄)
        "#00FF00",  # D# (绿)
        "#00FFFF",  # E (青)
        "#0000FF",  # F (蓝)
        "#FF00FF",  # F# (紫红)
        "#FF0088",  # G
        "#FF8800",  # G#
        "#88FF00",  # A (黄绿)
        "#00FF88",  # A#
        "#0088FF",  # B (青蓝)
    ]
    return colors[pitch_class]


def darken_color(hex_color: str, factor: float = 0.6) -> str:
    """Darken a hex color by multiplying RGB values."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = max(0, int(r * factor))
    g = max(0, int(g * factor))
    b = max(0, int(b * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def get_note_y_position(
    note: int,
    min_note: int,
    max_note: int,
    screen_height: float,
    coverage: float = 4 / 5,
) -> float:
    """Map MIDI note number to Y-axis position (higher notes = higher position)."""
    if min_note == max_note:
        return 0.0
    # Map to range [-screen_height/2, screen_height/2]
    normalized = (note - min_note) / (max_note - min_note)
    return -screen_height * coverage / 2 + normalized * screen_height * coverage


def get_dynamic_note_height(
    min_note: int,
    max_note: int,
    screen_height: float,
    coverage: float = 4 / 5,
    space_ratio: float = 0.1,
) -> tuple[float, float]:
    """Calculate note height dynamically so all notes occupy coverage% of screen height."""
    if min_note == max_note:
        return 0.3, 0.03
    note_range = max_note - min_note + 1  # +1 to include spacing
    total_height = screen_height * coverage
    height = total_height / (note_range * (1 + space_ratio))
    return height, height * space_ratio


def shift_moras(moras: Iterable[Mora], pre_roll_s: float) -> tuple[list[Mora], int]:
    """Shift notes so the earliest start appears after a short pre-roll."""
    moras = list(moras)
    if not moras:
        return [], 0

    pre_roll_ms = int(pre_roll_s * 1000)
    earliest = min(m.start for m in moras)
    shift_ms = pre_roll_ms - earliest if earliest < pre_roll_ms else 0

    shifted = [
        Mora(
            start=m.start + shift_ms,
            end=m.end + shift_ms,
            continous=m.continous,
            note=m.note,
        )
        for m in moras
    ]
    return shifted, shift_ms


class MidiVisualizationScene(Scene):
    def __init__(
        self,
        moras: list[Mora],
        scroll_speed: float,
        lead_time_s: float,
        tail_time_s: float,
        coverage: float = 4 / 5,
        **kwargs,
    ) -> None:
        self.moras = moras
        self.scroll_speed = scroll_speed
        self.lead_time_s = lead_time_s
        self.tail_time_s = tail_time_s
        self.coverage = coverage

        # Calculate min and max notes
        notes = [m.note for m in moras]
        self.min_note = min(notes)
        self.max_note = max(notes)

        # Get screen height from manim config
        self.screen_height = config.frame_height

        # Calculate dynamic note height
        self.note_height, self.note_space = get_dynamic_note_height(
            self.min_note, self.max_note, self.screen_height, coverage=self.coverage
        )

        max_end_ms = max(m.end for m in moras)
        self.total_time_s = max_end_ms / 1000 + tail_time_s
        super().__init__(**kwargs)

    def construct(self) -> None:
        elapsed = ValueTracker(0.0)

        judge_line = Line(
            UP * config.frame_height / 2, DOWN * config.frame_height / 2, color=ORANGE
        )
        self.add(judge_line)

        notes = VGroup()

        for idx, mora in enumerate(self.moras):
            start_s = mora.start / 1000
            end_s = mora.end / 1000
            width = max((end_s - start_s) * self.scroll_speed, 0.1)

            # Get color based on note pitch
            color = get_note_color(mora.note)
            darker_color = darken_color(color, factor=0.5)
            # Get Y position based on note pitch using min/max from all notes
            y_position = get_note_y_position(
                mora.note,
                self.min_note,
                self.max_note,
                self.screen_height,
                coverage=self.coverage,
            )

            # Create shadow rectangle behind the note
            shadow = RoundedRectangle(
                height=self.note_height,
                width=width * 0.98,
                corner_radius=0.05,
                color=darker_color,
            )
            shadow.set_fill(darker_color, opacity=0.4)
            shadow.set_stroke(width=0)

            # Create main note rectangle
            note = RoundedRectangle(
                height=self.note_height,
                width=width * 0.98,
                corner_radius=0.05,
                color=color,
            )
            note.set_fill(color, opacity=0.82)
            # Darker stroke for edge definition
            note.set_stroke(darker_color, width=2)
            note.set_opacity(0.0)

            def updater(mobj_shadow, mobj_note, s=start_s, e=end_s, y=y_position):
                t = elapsed.get_value()
                center_x = ((s + e) / 2 - t) * self.scroll_speed
                visible = (t >= s - self.lead_time_s) and (t <= e + self.tail_time_s)
                if not visible:
                    mobj_shadow.set_opacity(0)
                    mobj_note.set_opacity(0)
                    return
                # Position shadow slightly offset for depth
                mobj_shadow.move_to(RIGHT * center_x + UP * y + DOWN * 0.08 + RIGHT * 0.05)
                mobj_shadow.set_opacity(0.3)
                # Position main note
                mobj_note.move_to(RIGHT * center_x + UP * y)
                mobj_note.set_opacity(0.82)

            # Create a combined updater for both shadow and note
            shadow.add_updater(lambda x, s=shadow, n=note: None)
            
            def combined_updater(x):
                updater(shadow, note)
            
            note.add_updater(combined_updater)
            notes.add(shadow)
            notes.add(note)

        self.add(notes)
        self.play(
            elapsed.animate.set_value(self.total_time_s),
            run_time=self.total_time_s,
            rate_func=linear,
        )
        self.wait(0.25)


def render_midi_video(
    midi_file: Path,
    output_path: Path,
    offset_ms: int,
    scroll_speed: float,
    pre_roll_s: float,
    tail_s: float,
    note_height: float,
    fps: int,
    width: int,
    height: int,
) -> Path:
    moras = list(read_midi(midi_file, offset_ms=offset_ms))
    if not moras:
        raise RuntimeError("No notes parsed from MIDI")

    moras, shift_ms = shift_moras(moras, pre_roll_s)
    min_note = min(m.note for m in moras)
    max_note = max(m.note for m in moras)
    print(f"[*] Loaded {len(moras)} notes; applied shift {shift_ms} ms for pre-roll")
    print(f"[*] Note range: {min_note} - {max_note}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempconfig(
        {
            "output_file": output_path.stem,
            "media_dir": str(output_path.parent),
            "video_dir": str(output_path.parent),
            "frame_rate": fps,
            "pixel_width": width,
            "pixel_height": height,
            "movie_file_extension": ".mp4",
            "write_to_movie": True,
        }
    ):
        scene = MidiVisualizationScene(
            moras=moras,
            scroll_speed=scroll_speed,
            lead_time_s=pre_roll_s,
            tail_time_s=tail_s,
        )
        scene.render()

    final_path = output_path.with_suffix(".mp4")
    print(f"[+] Video written to {final_path}")
    return final_path


@click.command()
@click.argument(
    "midi_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output",
    "output_path",
    default=Path("midi_visualization.mp4"),
    type=click.Path(path_type=Path),
)
@click.option(
    "--offset-ms",
    default=0,
    show_default=True,
    help="Time shift applied when reading MIDI",
)
@click.option(
    "--scroll-speed",
    default=2.5,
    show_default=True,
    help="Units per second from right to left",
)
@click.option(
    "--pre-roll",
    default=2.0,
    show_default=True,
    help="Seconds shown before the first note hits the line",
)
@click.option(
    "--tail",
    default=2.0,
    show_default=True,
    help="Seconds kept after the last note finishes",
)
@click.option(
    "--note-height",
    default=0.4,
    show_default=True,
    help="Note block height in scene units",
)
@click.option("--fps", default=60, show_default=True, help="Output frame rate")
@click.option("--width", default=1280, show_default=True, help="Output width in pixels")
@click.option(
    "--height", default=720, show_default=True, help="Output height in pixels"
)
def main(
    midi_file: Path,
    output_path: Path,
    offset_ms: int,
    scroll_speed: float,
    pre_roll: float,
    tail: float,
    note_height: float,
    fps: int,
    width: int,
    height: int,
) -> None:
    """Render a MIDI scroll video with the judge line centered."""

    render_midi_video(
        midi_file=midi_file,
        output_path=output_path,
        offset_ms=offset_ms,
        scroll_speed=scroll_speed,
        pre_roll_s=pre_roll,
        tail_s=tail,
        note_height=note_height,
        fps=fps,
        width=width,
        height=height,
    )


if __name__ == "__main__":
    main()
