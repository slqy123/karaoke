#!/bin/python
from __future__ import annotations

import multiprocessing as mp
import subprocess
from pathlib import Path
from typing import Any, Tuple

import click
import cv2
import numpy as np

from midi import Mora, read_midi


_WORKER_STATE: dict[str, Any] | None = None

# Shared gray palette for all note desaturation states.
UNIFORM_NOTE_GRAY_FILL: tuple[int, int, int] = (150, 150, 150)
UNIFORM_NOTE_GRAY_EDGE: tuple[int, int, int] = (95, 95, 95)


def _init_worker(state: dict[str, Any]) -> None:
    global _WORKER_STATE
    _WORKER_STATE = state


def _render_frame_worker(frame_idx: int) -> bytes:
    if _WORKER_STATE is None:
        raise RuntimeError("Worker state is not initialized")

    state = _WORKER_STATE
    width = state["width"]
    height = state["height"]
    t = state["start_time_s"] + (frame_idx / state["fps"])

    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = (0, 0, 0)

    overlay = image.copy()
    cv2.rectangle(
        overlay,
        (0, 0),
        (width, int(state["waterfall_height"])),
        (30, 30, 30),
        -1,
    )
    cv2.addWeighted(overlay, 0.3, image, 0.7, 0, image)

    timeline_active = False
    for start_s, end_s in state["timeline_intervals"]:
        if start_s <= t <= end_s:
            timeline_active = True
            break
        if start_s > t:
            break

    for start_s, end_s, note, _note_idx in state["moras"]:
        display_start_s = start_s + state["note_gap_s"] / 2
        display_end_s = end_s - state["note_gap_s"] / 2
        if display_end_s < display_start_s:
            mid = (start_s + end_s) / 2
            display_start_s = mid
            display_end_s = mid

        note_center_x_at_t = state["judge_x"] + (start_s + end_s) / 2 * state["scroll_speed"] - t * state["scroll_speed"]
        note_width = max((end_s - start_s - state["note_gap_s"]) * state["scroll_speed"], 0.04)

        if note_center_x_at_t + note_width / 2 < state["left_x"] or note_center_x_at_t - note_width / 2 > state["right_x"]:
            continue

        color_hex = get_note_color(note)
        color_bgr = hex_to_bgr(color_hex)
        darker_color_hex = darken_color(color_hex, factor=0.5)
        darker_bgr = hex_to_bgr(darker_color_hex)
        gray_color = UNIFORM_NOTE_GRAY_FILL
        gray_darker = UNIFORM_NOTE_GRAY_EDGE

        # Notes become gray after fully passing the timeline.
        if t > display_end_s:
            color_bgr = gray_color
            darker_bgr = gray_darker

        y_pos = get_note_y_position(
            note,
            state["max_note"],
            state["top_y"],
            state["waterfall_height"],
            state["note_height"],
            state["lane_step"],
        )

        shadow_x = note_center_x_at_t + 2
        shadow_y = y_pos + 4
        draw_rounded_rect(
            image,
            (shadow_x, shadow_y),
            (note_width, state["note_height"]),
            darker_bgr,
            thickness=-1,
            alpha=0.3,
        )

        draw_rounded_rect(
            image,
            (note_center_x_at_t, y_pos),
            (note_width, state["note_height"]),
            color_bgr,
            thickness=-1,
            alpha=0.82,
        )

        draw_rounded_rect(
            image,
            (note_center_x_at_t, y_pos),
            (note_width, state["note_height"]),
            darker_bgr,
            thickness=1,
            alpha=1.0,
        )

        if display_start_s <= t <= display_end_s:
            apply_passed_gray_overlay(
                image,
                center_x=note_center_x_at_t,
                center_y=y_pos,
                note_width=note_width,
                note_height=state["note_height"],
                judge_x=state["judge_x"],
                gray_color=gray_color,
                gray_edge_color=gray_darker,
            )

    judge_x_int = int(state["judge_x"])
    line_bottom = int(state["waterfall_height"])
    line_color = (0, 165, 255) if timeline_active else (130, 130, 130)
    cv2.line(image, (judge_x_int, 0), (judge_x_int, line_bottom), line_color, 3)

    return image.tobytes()


def _queue_worker_loop(
    task_queue: Any,
    result_queue: Any,
    state: dict[str, Any],
) -> None:
    _init_worker(state)
    while True:
        frame_idx = task_queue.get()
        if frame_idx is None:
            break
        frame_bytes = _render_frame_worker(frame_idx)
        result_queue.put((frame_idx, frame_bytes))


def build_timeline_intervals(
    moras: list[Mora],
    note_gap_s: float,
) -> list[tuple[float, float]]:
    """Build timeline active intervals using Mora.continous for continuity decisions."""
    half_gap = note_gap_s / 2
    merged: list[tuple[float, float]] = []
    for m in moras:
        s = m.start / 1000 + half_gap
        e = m.end / 1000 - half_gap
        if e < s:
            mid = (m.start + m.end) / 2000
            s = mid
            e = mid

        if not merged:
            merged.append((s, e))
            continue

        prev_s, prev_e = merged[-1]
        if m.continous or s <= prev_e:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))

    return merged


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def apply_passed_gray_overlay(
    image: np.ndarray,
    center_x: float,
    center_y: float,
    note_width: float,
    note_height: float,
    judge_x: float,
    gray_color: tuple[int, int, int],
    gray_edge_color: tuple[int, int, int],
) -> None:
    """Gray out the left passed part while preserving rounded note corners."""
    left = center_x - note_width / 2
    right = center_x + note_width / 2
    cut = clamp(judge_x, left, right)
    if cut <= left + 0.5:
        return

    h, w, _ = image.shape
    x1 = int(clamp(left, 0, w - 1))
    x2 = int(clamp(cut, 0, w - 1))
    y1 = int(clamp(center_y - note_height / 2, 0, h - 1))
    y2 = int(clamp(center_y + note_height / 2, 0, h - 1))
    if x2 <= x1 or y2 <= y1:
        return

    # Build a rounded mask identical to note geometry, then keep only passed-left region.
    mask = np.zeros((h, w), dtype=np.uint8)
    note_left = int(clamp(center_x - note_width / 2, 0, w - 1))
    note_right = int(clamp(center_x + note_width / 2, 0, w - 1))
    note_top = int(clamp(center_y - note_height / 2, 0, h - 1))
    note_bottom = int(clamp(center_y + note_height / 2, 0, h - 1))
    if note_right <= note_left or note_bottom <= note_top:
        return

    radius = int(min((note_right - note_left), (note_bottom - note_top)) * 0.48)
    radius = max(1, radius)
    cv2.rectangle(mask, (note_left + radius, note_top), (note_right - radius, note_bottom), 255, -1)
    cv2.rectangle(mask, (note_left, note_top + radius), (note_right, note_bottom - radius), 255, -1)
    cv2.circle(mask, (note_left + radius, note_top + radius), radius, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (note_right - radius, note_top + radius), radius, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (note_left + radius, note_bottom - radius), radius, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (note_right - radius, note_bottom - radius), radius, 255, -1, lineType=cv2.LINE_AA)

    # Keep only the region already passed the timeline.
    mask[:, x2:w] = 0

    overlay = image.copy()
    overlay[mask > 0] = gray_color
    cv2.addWeighted(overlay, 0.88, image, 0.12, 0, image)

    edge_top = int(clamp(note_top + radius * 0.7, 0, h - 1))
    edge_bottom = int(clamp(note_bottom - radius * 0.7, 0, h - 1))
    if edge_bottom > edge_top and x2 >= 0 and x2 < w:
        cv2.line(image, (x2, edge_top), (x2, edge_bottom), gray_edge_color, 1, lineType=cv2.LINE_AA)


def hex_to_bgr(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to BGR tuple for OpenCV."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (b, g, r)  # OpenCV uses BGR


def get_note_color(note: int) -> str:
    """Map MIDI note number to color using a rainbow spectrum."""
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
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = max(0, int(r * factor))
    g = max(0, int(g * factor))
    b = max(0, int(b * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def get_lane_geometry(
    note_count: int,
    band_height: float,
    overlap_ratio: float,
) -> Tuple[float, float]:
    """Return (note_height, lane_step) for a pitch lane stack with optional overlap."""
    if note_count <= 1:
        h = max(0.06, band_height)
        return h, 0.0
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
    """Map MIDI note to Y position inside the top waterfall band."""
    lane_idx = max_note - note
    y = top_y + note_height / 2 + lane_idx * lane_step
    lower = top_y + note_height / 2
    upper = top_y + band_height - note_height / 2
    return clamp(y, lower, upper)


def draw_rounded_rect(
    image: np.ndarray,
    center: Tuple[float, float],
    size: Tuple[float, float],
    color: Tuple[int, int, int],
    thickness: int = -1,
    alpha: float = 1.0,
) -> None:
    """Draw a true rounded rectangle on image with optional transparency."""
    x, y = center
    w, h = size

    if w <= 1 or h <= 1:
        return

    x1 = int(x - w / 2)
    y1 = int(y - h / 2)
    x2 = int(x + w / 2)
    y2 = int(y + h / 2)

    if x2 <= x1 or y2 <= y1:
        return

    # Use a large radius so corners are visibly rounded (pill-like when possible).
    radius = int(min((x2 - x1), (y2 - y1)) * 0.48)
    radius = max(1, radius)

    def _draw_round_rect(dst: np.ndarray) -> None:
        if thickness < 0:
            # Filled rounded rectangle: 2 body rects + 4 corner circles
            cv2.rectangle(dst, (x1 + radius, y1), (x2 - radius, y2), color, -1)
            cv2.rectangle(dst, (x1, y1 + radius), (x2, y2 - radius), color, -1)
            cv2.circle(dst, (x1 + radius, y1 + radius), radius, color, -1, lineType=cv2.LINE_AA)
            cv2.circle(dst, (x2 - radius, y1 + radius), radius, color, -1, lineType=cv2.LINE_AA)
            cv2.circle(dst, (x1 + radius, y2 - radius), radius, color, -1, lineType=cv2.LINE_AA)
            cv2.circle(dst, (x2 - radius, y2 - radius), radius, color, -1, lineType=cv2.LINE_AA)
            return

        stroke = max(1, int(thickness))
        # Edge lines
        cv2.line(dst, (x1 + radius, y1), (x2 - radius, y1), color, stroke, lineType=cv2.LINE_AA)
        cv2.line(dst, (x1 + radius, y2), (x2 - radius, y2), color, stroke, lineType=cv2.LINE_AA)
        cv2.line(dst, (x1, y1 + radius), (x1, y2 - radius), color, stroke, lineType=cv2.LINE_AA)
        cv2.line(dst, (x2, y1 + radius), (x2, y2 - radius), color, stroke, lineType=cv2.LINE_AA)
        # Corner arcs
        cv2.ellipse(dst, (x1 + radius, y1 + radius), (radius, radius), 180, 0, 90, color, stroke, lineType=cv2.LINE_AA)
        cv2.ellipse(dst, (x2 - radius, y1 + radius), (radius, radius), 270, 0, 90, color, stroke, lineType=cv2.LINE_AA)
        cv2.ellipse(dst, (x1 + radius, y2 - radius), (radius, radius), 90, 0, 90, color, stroke, lineType=cv2.LINE_AA)
        cv2.ellipse(dst, (x2 - radius, y2 - radius), (radius, radius), 0, 0, 90, color, stroke, lineType=cv2.LINE_AA)

    if alpha < 1.0:
        overlay = image.copy()
        _draw_round_rect(overlay)
        cv2.addWeighted(overlay, alpha, image, 1 - alpha, 0, image)
    else:
        _draw_round_rect(image)


class MidiVisualizerOpenCV:
    def __init__(
        self,
        moras: list[Mora],
        scroll_speed: float,
        timeline_x_ratio: float,
        waterfall_coverage: float,
        lane_overlap_ratio: float,
        note_gap_s: float,
        start_at_midi_zero: bool = True,
        fps: int = 60,
        width: int = 1280,
        height: int = 720,
    ) -> None:
        self.moras = moras
        self.scroll_speed = scroll_speed
        self.timeline_x_ratio = clamp(timeline_x_ratio, 0.0, 1.0)
        self.waterfall_coverage = clamp(waterfall_coverage, 0.05, 1.0)
        self.lane_overlap_ratio = clamp(lane_overlap_ratio, 0.0, 0.95)
        self.note_gap_s = max(0.0, note_gap_s)
        self.start_at_midi_zero = start_at_midi_zero
        self.fps = fps
        self.width = width
        self.height = height

        if self.scroll_speed <= 0:
            raise ValueError("scroll_speed must be > 0")

        # Calculate min and max notes
        notes = [m.note for m in moras]
        self.min_note = min(notes)
        self.max_note = max(notes)
        self.note_count = self.max_note - self.min_note + 1

        # Screen bounds (in pixels)
        self.left_x = 0.0
        self.right_x = float(self.width)
        self.top_y = 0.0
        self.bottom_y = float(self.height)

        # Judge line position (in pixels)
        self.judge_x = self.left_x + self.timeline_x_ratio * self.width

        # Waterfall occupies the top portion of screen height
        self.waterfall_height = self.height * self.waterfall_coverage

        # Calculate lane geometry
        self.note_height, self.lane_step = get_lane_geometry(
            self.note_count,
            self.waterfall_height,
            self.lane_overlap_ratio,
        )

        # Timeline calculation
        min_start_s = min(m.start for m in moras) / 1000
        max_end_s = max(m.end for m in moras) / 1000

        # Distance to travel from right edge to judge line, and judge line to left edge
        time_before_judge = (self.right_x - self.judge_x) / self.scroll_speed
        time_after_judge = (self.judge_x - self.left_x) / self.scroll_speed
        # By default, video timeline starts at MIDI t=0 for easier sync with source media.
        if self.start_at_midi_zero:
            self.start_time_s = 0.0
        else:
            self.start_time_s = min_start_s - time_before_judge
        self.end_time_s = max_end_s + time_after_judge
        self.total_time_s = max(0.1, self.end_time_s - self.start_time_s)

        # Total frames
        self.total_frames = int(self.total_time_s * self.fps)
        self.timeline_intervals = build_timeline_intervals(
            self.moras,
            self.note_gap_s,
        )

    def world_to_pixel_x(self, time_s: float) -> float:
        """Convert world time to pixel x coordinate."""
        return self.judge_x - (self.start_time_s - time_s) * self.scroll_speed

    def get_note_y_pixel(self, note: int) -> float:
        """Get pixel Y position for a note."""
        return get_note_y_position(
            note,
            self.max_note,
            self.top_y,
            self.waterfall_height,
            self.note_height,
            self.lane_step,
        )

    def render_frame(self, frame_idx: int) -> np.ndarray:
        """Render a single frame."""
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        image[:] = (0, 0, 0)  # Dark gray background

        # Current time in seconds
        t = self.start_time_s + (frame_idx / self.fps)

        # Draw waterfall background (semi-transparent)
        overlay = image.copy()
        cv2.rectangle(
            overlay,
            (0, 0),
            (self.width, int(self.waterfall_height)),
            (30, 30, 30),
            -1,
        )
        cv2.addWeighted(overlay, 0.3, image, 0.7, 0, image)

        # Timeline turns gray when no note is currently crossing the judge line.
        timeline_active = False
        for start_s, end_s in self.timeline_intervals:
            if start_s <= t <= end_s:
                timeline_active = True
                break
            if start_s > t:
                break

        # Draw notes
        for _note_idx, mora in enumerate(self.moras):
            start_s = mora.start / 1000
            end_s = mora.end / 1000
            display_start_s = start_s + self.note_gap_s / 2
            display_end_s = end_s - self.note_gap_s / 2
            if display_end_s < display_start_s:
                mid = (start_s + end_s) / 2
                display_start_s = mid
                display_end_s = mid

            # Check if note is visible
            note_center_x_at_t = self.judge_x + (start_s + end_s) / 2 * self.scroll_speed - t * self.scroll_speed
            note_width = max((end_s - start_s - self.note_gap_s) * self.scroll_speed, 0.04)

            if note_center_x_at_t + note_width / 2 < self.left_x or note_center_x_at_t - note_width / 2 > self.right_x:
                continue

            # Get color
            color_hex = get_note_color(mora.note)
            color_bgr = hex_to_bgr(color_hex)
            darker_color_hex = darken_color(color_hex, factor=0.5)
            darker_bgr = hex_to_bgr(darker_color_hex)
            gray_color = UNIFORM_NOTE_GRAY_FILL
            gray_darker = UNIFORM_NOTE_GRAY_EDGE

            if t > display_end_s:
                color_bgr = gray_color
                darker_bgr = gray_darker

            # Get Y position
            y_pos = self.get_note_y_pixel(mora.note)

            # Draw shadow
            shadow_x = note_center_x_at_t + 2
            shadow_y = y_pos + 4
            draw_rounded_rect(
                image,
                (shadow_x, shadow_y),
                (note_width, self.note_height),
                darker_bgr,
                thickness=-1,
                alpha=0.3,
            )

            # Draw main note (filled)
            draw_rounded_rect(
                image,
                (note_center_x_at_t, y_pos),
                (note_width, self.note_height),
                color_bgr,
                thickness=-1,
                alpha=0.82,
            )

            # Draw note stroke (outline)
            draw_rounded_rect(
                image,
                (note_center_x_at_t, y_pos),
                (note_width, self.note_height),
                darker_bgr,
                thickness=1,
                alpha=1.0,
            )

            if display_start_s <= t <= display_end_s:
                apply_passed_gray_overlay(
                    image,
                    center_x=note_center_x_at_t,
                    center_y=y_pos,
                    note_width=note_width,
                    note_height=self.note_height,
                    judge_x=self.judge_x,
                    gray_color=gray_color,
                    gray_edge_color=gray_darker,
                )

        # Draw judge line only within the waterfall band.
        judge_x_int = int(self.judge_x)
        line_bottom = int(self.waterfall_height)
        line_color = (0, 165, 255) if timeline_active else (130, 130, 130)
        cv2.line(image, (judge_x_int, 0), (judge_x_int, line_bottom), line_color, 3)

        return image

    def _build_worker_state(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "start_time_s": self.start_time_s,
            "waterfall_height": self.waterfall_height,
            "note_gap_s": self.note_gap_s,
            "judge_x": self.judge_x,
            "scroll_speed": self.scroll_speed,
            "left_x": self.left_x,
            "right_x": self.right_x,
            "max_note": self.max_note,
            "top_y": self.top_y,
            "note_height": self.note_height,
            "lane_step": self.lane_step,
            "moras": [(m.start / 1000, m.end / 1000, m.note, i) for i, m in enumerate(self.moras)],
            "timeline_intervals": self.timeline_intervals,
        }

    def render_video(
        self,
        output_path: Path,
        workers: int = 1,
        use_vaapi: bool = False,
        vaapi_device: str = "/dev/dri/renderD128",
        vaapi_qp: int = 24,
    ) -> None:
        """Render the entire video using ffmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workers = max(1, int(workers))

        # Use ffmpeg pipe to write video
        cmd = [
            "ffmpeg",
            "-f", "rawvideo",
            "-video_size", f"{self.width}x{self.height}",
            "-pixel_format", "bgr24",
            "-framerate", str(self.fps),
            "-i", "-",
        ]

        if use_vaapi:
            cmd.extend(
                [
                    "-vaapi_device", vaapi_device,
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "h264_vaapi",
                    "-qp", str(max(0, min(vaapi_qp, 51))),
                ]
            )
        else:
            cmd.extend(
                [
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "24",
                ]
            )

        cmd.extend(["-y", str(output_path)])

        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            print("[!] ffmpeg not found. Trying cv2.VideoWriter instead...")
            return self._render_video_cv2(output_path, workers=1)

        if proc.stdin is None:
            raise RuntimeError("ffmpeg stdin pipe is unavailable")

        encode_mode = "VAAPI(h264_vaapi)" if use_vaapi else "CPU(libx264)"
        print(
            f"[*] Rendering {self.total_frames} frames at {self.fps} fps with {workers} process(es), encoder={encode_mode}"
        )
        if workers == 1:
            for frame_idx in range(self.total_frames):
                if (frame_idx + 1) % max(1, self.total_frames // 10) == 0:
                    print(f"[*] Progress: {frame_idx + 1}/{self.total_frames}")

                frame = self.render_frame(frame_idx)
                proc.stdin.write(frame.tobytes())
        else:
            state = self._build_worker_state()
            ctx = mp.get_context("spawn")
            # Use unbounded queues to avoid producer/consumer circular blocking.
            task_queues = [ctx.Queue() for _ in range(workers)]
            result_queue = ctx.Queue()
            processes = [
                ctx.Process(
                    target=_queue_worker_loop,
                    args=(task_queues[i], result_queue, state),
                )
                for i in range(workers)
            ]

            for p in processes:
                p.start()

            # Distribute frames by i % workers.
            for frame_idx in range(self.total_frames):
                task_queues[frame_idx % workers].put(frame_idx)
            for q in task_queues:
                q.put(None)

            pending: dict[int, bytes] = {}
            next_to_write = 0
            written = 0

            while written < self.total_frames:
                frame_idx, frame_bytes = result_queue.get()
                pending[frame_idx] = frame_bytes

                while next_to_write in pending:
                    proc.stdin.write(pending.pop(next_to_write))
                    next_to_write += 1
                    written += 1
                    if written % max(1, self.total_frames // 10) == 0:
                        print(f"[*] Progress: {written}/{self.total_frames}")

            for p in processes:
                p.join()

            # Ensure queue feeder threads are cleaned up deterministically.
            for q in task_queues:
                q.close()
                q.join_thread()
            result_queue.close()
            result_queue.join_thread()

        proc.stdin.close()
        proc.wait()

        if proc.returncode == 0:
            print(f"[+] Video written to {output_path}")
        else:
            stderr_text = proc.stderr.read().decode() if proc.stderr else "unknown error"
            print(f"[!] ffmpeg error: {stderr_text}")

    def _render_video_cv2(self, output_path: Path, workers: int = 1) -> None:
        """Fallback: use cv2.VideoWriter if ffmpeg is not available."""
        if workers > 1:
            print("[!] cv2.VideoWriter fallback currently uses single-process mode")
        fourcc = cv2.VideoWriter.fourcc(*"mp4v")
        out = cv2.VideoWriter(str(output_path), fourcc, self.fps, (self.width, self.height))

        print(f"[*] Rendering {self.total_frames} frames at {self.fps} fps...")
        for frame_idx in range(self.total_frames):
            if (frame_idx + 1) % max(1, self.total_frames // 10) == 0:
                print(f"[*] Progress: {frame_idx + 1}/{self.total_frames}")

            frame = self.render_frame(frame_idx)
            out.write(frame)

        out.release()
        print(f"[+] Video written to {output_path}")


def render_midi_video(
    midi_file: Path,
    output_path: Path,
    offset_ms: int,
    scroll_speed: float,
    timeline_x_ratio: float,
    waterfall_coverage: float,
    lane_overlap_ratio: float,
    note_gap_s: float,
    fps: int,
    width: int,
    height: int,
    workers: int,
    use_vaapi: bool,
    vaapi_device: str,
    vaapi_qp: int,
    start_at_midi_zero: bool,
) -> Path:
    moras = list(read_midi(midi_file, offset_ms=offset_ms))
    if not moras:
        raise RuntimeError("No notes parsed from MIDI")

    # read_midi may yield negative starts (e.g., parser pre-count); normalize to keep MIDI t=0 aligned.
    min_start_ms = min(m.start for m in moras)
    if min_start_ms < 0:
        shift_ms = -min_start_ms
        moras = [
            Mora(
                start=m.start + shift_ms,
                end=m.end + shift_ms,
                continous=m.continous,
                note=m.note,
            )
            for m in moras
        ]
        print(f"[*] Shifted note times by +{shift_ms} ms to normalize MIDI start")

    min_note = min(m.note for m in moras)
    max_note = max(m.note for m in moras)
    print(f"[*] Loaded {len(moras)} notes")
    print(f"[*] Note range: {min_note} - {max_note}")

    visualizer = MidiVisualizerOpenCV(
        moras=moras,
        scroll_speed=scroll_speed,
        timeline_x_ratio=timeline_x_ratio,
        waterfall_coverage=waterfall_coverage,
        lane_overlap_ratio=lane_overlap_ratio,
        note_gap_s=note_gap_s,
        start_at_midi_zero=start_at_midi_zero,
        fps=fps,
        width=width,
        height=height,
    )

    visualizer.render_video(
        output_path.with_suffix(".mp4"),
        workers=workers,
        use_vaapi=use_vaapi,
        vaapi_device=vaapi_device,
        vaapi_qp=vaapi_qp,
    )
    return output_path.with_suffix(".mp4")


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
    default=500,
    show_default=True,
    help="Units per second from right to left",
)
@click.option(
    "--timeline-ratio",
    default=0.35,
    show_default=True,
    help="Judge line position in horizontal ratio [0,1] from left to right",
)
@click.option(
    "--waterfall-coverage",
    default=0.4,
    show_default=True,
    help="Vertical ratio [0,1] occupied by the waterfall from top edge",
)
@click.option(
    "--lane-overlap",
    default=0.25,
    show_default=True,
    help="Pitch-lane overlap ratio [0,1), larger means tighter stacking",
)
@click.option(
    "--note-gap",
    default=0.03,
    show_default=True,
    help="Seconds trimmed from each note duration to leave visible gaps",
)
@click.option("--fps", default=60, show_default=True, help="Output frame rate")
@click.option("--width", default=1280, show_default=True, help="Output width in pixels")
@click.option(
    "--height", default=720, show_default=True, help="Output height in pixels"
)
@click.option(
    "--workers",
    default=1,
    show_default=True,
    type=click.IntRange(1, None),
    help="Number of rendering processes (1 means single-process)",
)
@click.option(
    "--vaapi/--no-vaapi",
    "use_vaapi",
    default=False,
    show_default=True,
    help="Use VAAPI hardware encoding with ffmpeg (h264_vaapi)",
)
@click.option(
    "--vaapi-device",
    default="/dev/dri/renderD128",
    show_default=True,
    help="VAAPI device path",
)
@click.option(
    "--vaapi-qp",
    default=24,
    show_default=True,
    type=click.IntRange(0, 51),
    help="VAAPI quantizer parameter (lower is better quality)",
)
@click.option(
    "--start-at-midi-zero/--legacy-start",
    "start_at_midi_zero",
    default=True,
    show_default=True,
    help="Start video timeline at MIDI t=0 for alignment with source media",
)
def main(
    midi_file: Path,
    output_path: Path,
    offset_ms: int,
    scroll_speed: float,
    timeline_ratio: float,
    waterfall_coverage: float,
    lane_overlap: float,
    note_gap: float,
    fps: int,
    width: int,
    height: int,
    workers: int,
    use_vaapi: bool,
    vaapi_device: str,
    vaapi_qp: int,
    start_at_midi_zero: bool,
) -> None:
    """Render a MIDI waterfall video using OpenCV (fast & efficient)."""

    render_midi_video(
        midi_file=midi_file,
        output_path=output_path,
        offset_ms=offset_ms,
        scroll_speed=scroll_speed,
        timeline_x_ratio=timeline_ratio,
        waterfall_coverage=waterfall_coverage,
        lane_overlap_ratio=lane_overlap,
        note_gap_s=note_gap,
        fps=fps,
        width=width,
        height=height,
        workers=workers,
        use_vaapi=use_vaapi,
        vaapi_device=vaapi_device,
        vaapi_qp=vaapi_qp,
        start_at_midi_zero=start_at_midi_zero,
    )


if __name__ == "__main__":
    main()
