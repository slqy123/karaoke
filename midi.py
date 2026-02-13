from mido import MidiFile, MidiTrack, Message, tempo2bpm
from pathlib import Path

TICKS_PER_BEAT = 480
BEAT4 = TICKS_PER_BEAT
BEAT8 = TICKS_PER_BEAT // 2
BEAT16 = TICKS_PER_BEAT // 4


class Mora:
    def __init__(self, start: int, end: int, continous: bool, note: int = 0):
        self.start = start
        self.end = end
        self.duration = end - start
        self.continous = continous
        self.note = note

    def __repr__(self):
        return f"Mora(start={self.start / 1000:.3f}, end={self.end / 1000:.3f}, duration={self.duration / 1000:.3f}, continous={self.continous}, note={self.note})"


def read_midi(file_path: Path, offset_ms: int = 0):
    midi = MidiFile(file_path)
    assert midi.ticks_per_beat == TICKS_PER_BEAT
    for m in midi.tracks[0]:
        if m.type == "set_tempo":
            bpm = float(tempo2bpm(m.tempo))
            print(f"BPM: {bpm:.1f}")
            break
    else:
        raise Exception("no tempo")

    tick2ms = lambda ticks: int(ticks / TICKS_PER_BEAT / bpm * 60 * 1000) + offset_ms  # noqa: E731

    current_time = -TICKS_PER_BEAT * 4
    for i in range(len(midi.tracks[0]) - 2):
        msg1 = midi.tracks[0][i]
        msg2 = midi.tracks[0][i + 1]
        current_time += msg1.time
        note_time: int = msg2.time
        note_type: str = msg1.type
        if note_type != "note_on":
            continue
        if note_time < BEAT16:
            continue
        if msg1.velocity == 0:
            msg1.note = 0
        if msg1.note == 0:
            continue
        yield Mora(
            start=tick2ms(current_time),
            end=tick2ms(current_time + note_time),
            continous=msg1.time == 0,
            note=msg1.note,
        )


if __name__ == "__main__":
    midi_file_path = Path("/home/quy/Music/sheet/ともしびのうた/res.mid")
    print(*read_midi(midi_file_path, offset_ms=-880), sep="\n")
