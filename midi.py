#!/bin/python

from pathlib import Path
from typing import Any, Literal
import click

from mido import MidiFile, tempo2bpm

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

def format_midi(midi_path: Path, wfd_path: Path, min_beat: Literal[4, 8, 16]):
    from struct import pack
    min_beat_time = TICKS_PER_BEAT // (min_beat // 4)

    midi = MidiFile(midi_path)
    for m in midi.tracks[0]:
        if m.type == "set_tempo":
            bpm = float(tempo2bpm(m.tempo))
            print(f"BPM: {bpm:.1f}")
            break
    else:
        raise Exception("no tempo")

    tick2ms = lambda ticks: int(ticks / TICKS_PER_BEAT / bpm * 60 * 1000)  # noqa: E731
    tick2ms_format = lambda ticks: tick2ms(  # noqa: E731
        round(ticks / min_beat_time) * min_beat_time
    )  

    current_time = 0
    moras: list[Mora] = []
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

        mora = Mora(
            start=tick2ms_format(current_time),
            end=tick2ms_format(current_time + note_time),
            continous=msg1.time == 0,
            note=msg1.note,
        )
        if mora.start == mora.end:
            continue
        moras.append(mora)

    data: list[bytes] = [pack("<I", len(moras))]
    for mora in moras:
        data.append(
            pack("<IIBBHI", mora.start, mora.duration, 0x90, mora.note, 0x64, 0x0)
        )


    assert wfd_path.exists() and wfd_path.suffix == '.wfd'
    from wfd import WaveToneDataType, WaveToneFormatData
    wfd_data: Any = WaveToneFormatData.parse_file(wfd_path)
    wfd_path.rename(wfd_path.with_suffix(".wfd.bak"))

    note_list_index = -1
    for i, (index, _) in enumerate(zip(wfd_data.indexes, wfd_data.data_bodies)):
        if int(index.data_type) == WaveToneDataType.NOTE_LIST:
            note_list_index = i
            break
    assert note_list_index >= 0

    data_bytes = b''.join(data)
    wfd_data.data_bodies[note_list_index] = data_bytes 
    wfd_data.indexes[note_list_index] = {
        "data_type": WaveToneDataType.NOTE_LIST,
        "size": len(data_bytes)
    }
    wfd_path.write_bytes(WaveToneFormatData.build(wfd_data))


@click.group()
def midi():...

@midi.command()
@click.argument("midi_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("wfd_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--min-beat", "-m", type=click.Choice([4, 8, 16]))
def embed(midi_path: Path, wfd_path:Path, min_beat: Literal[4, 8, 16]):
    format_midi(midi_path, wfd_path, min_beat)
if __name__ == "__main__":
    midi()
    # format_midi(Path("./results/vocal.mid"), Path("/home/quy/Music/sheet/ラムネ色スケッチブック/vocal.wfd"), 8)
    # midi_file_path = Path("/home/quy/Music/sheet/ともしびのうた/res.mid")
    # print(*read_midi(midi_file_path, offset_ms=-880), sep="\n")
