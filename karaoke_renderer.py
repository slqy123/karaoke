#!/bin/python
from pathlib import Path
import sys

if (p := str(Path(__file__).parent)) not in sys.path:
    sys.path.append(p)
import time
from typing import Optional
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.panel import Panel
import click
import threading
import sys
import select
import termios
import tty

from parser import Chapter, Word
from midi import Mora, read_midi

# 尝试导入音频播放库
try:
    import sounddevice as sd
    import soundfile as sf

    sd.default.device = None, sd.query_devices("pulse")["index"]

    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False


class WordTiming:
    """单个词的时间信息"""

    def __init__(self, word: Word, start_ms: int, end_ms: int):
        self.word = word
        self.start_ms = start_ms  # 毫秒
        self.end_ms = end_ms  # 毫秒
        self.duration_ms = end_ms - start_ms

    def __repr__(self):
        return f"WordTiming(text='{self.word.text}', start={self.start_ms / 1000:.3f}s, end={self.end_ms / 1000:.3f}s)"


class LineTimings:
    """单行歌词的时间信息"""

    def __init__(self, word_timings: list[WordTiming]):
        self.word_timings = word_timings

    def get_text(self) -> str:
        """获取这一行的文本"""
        return "".join(wt.word.text for wt in self.word_timings)

    def get_line_end_time_ms(self) -> int:
        """获取这一行的结束时间"""
        if not self.word_timings:
            return 0
        return self.word_timings[-1].end_ms


class AudioPlayer:
    """使用 sounddevice + soundfile 的音频播放器"""

    def __init__(self, audio_file: Path, offset_ms: int = 0):
        self.audio_file = audio_file
        self.offset_ms = offset_ms
        self.is_playing = False
        self.lock = threading.Lock()
        self.current_position_ms = 0
        self.play_thread: Optional[threading.Thread] = None
        self.should_stop = False

        if not AUDIO_AVAILABLE:
            raise RuntimeError(
                "sounddevice 和 soundfile 未安装，请运行: pip install sounddevice soundfile\n或者不使用 --audio 参数"
            )

        # 加载音频文件
        try:
            self.audio_data, self.samplerate = sf.read(str(self.audio_file))
            print(f"[*] 音频采样率: {self.samplerate} Hz")
            print(f"[*] 音频时长: {len(self.audio_data) / self.samplerate:.2f}s")
        except Exception as e:
            raise RuntimeError(f"无法加载音频文件: {e}")

    def play(self, start_position_ms: int = 0):
        """播放音频，可选从指定位置开始"""
        with self.lock:
            self.should_stop = False
            self.current_position_ms = start_position_ms

            try:
                # 计算实际播放位置（考虑偏移）
                actual_position_ms = max(0, start_position_ms + self.offset_ms)
                start_sample = int(actual_position_ms * self.samplerate / 1000)
                start_sample = max(0, min(start_sample, len(self.audio_data) - 1))

                # 停止之前的播放
                self.should_stop = True
                if self.play_thread and self.play_thread.is_alive():
                    self.play_thread.join(timeout=0.2)

                # 准备播放数据
                self.should_stop = False
                audio_segment = self.audio_data[start_sample:]

                # 在后台线程中播放
                self.play_thread = threading.Thread(
                    target=self._play_audio, args=(audio_segment,), daemon=True
                )
                self.play_thread.start()
                self.is_playing = True
            except Exception as e:
                print(f"[!] 音频播放错误: {e}")
                self.is_playing = False

    def _play_audio(self, audio_segment):
        """在线程中播放音频"""
        try:
            sd.play(audio_segment, self.samplerate)
            # 等待播放完成或被中断
            while sd.get_stream().active and not self.should_stop:
                time.sleep(0.01)
            if self.should_stop:
                sd.stop()
        except Exception as e:
            print(f"[!] 后台播放错误: {e}")
        finally:
            self.is_playing = False

    def seek(self, position_ms: int):
        """跳转到指定位置"""
        self.play(start_position_ms=position_ms)

    def stop(self):
        """停止播放"""
        with self.lock:
            self.should_stop = True
            self.is_playing = False
            try:
                sd.stop()
            except:
                pass

    def cleanup(self):
        """清理资源"""
        self.stop()
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=0.5)


def chapter_to_timings(
    chapter: Chapter,
    moras: list[Mora],
    start_mora_index: int = 0,
) -> tuple[list[LineTimings], int]:
    """
    将 Chapter 和 MIDI Mora 序列转换为时间戳信息。

    Args:
        chapter: 歌词章节
        moras: MIDI 读取的 Mora 列表

    Returns:
        每一行歌词的时间信息列表
    """
    # 收集所有的 Word 及其所需的 mora 数量
    word_mora_list = []
    for line in chapter.lines:
        for word in line.words:
            word_mora_list.append((word, word.mora))

    # 验证 mora 总数是否匹配（仅提示，不中断）
    total_word_moras = sum(mora_count for _, mora_count in word_mora_list)
    total_midi_moras = len(moras)
    if total_word_moras + start_mora_index > total_midi_moras:
        print(
            f"警告: 歌词总 mora 数 ({total_word_moras}) + 偏移 {start_mora_index} 超出 MIDI Mora 数 ({total_midi_moras})"
        )

    # 为每个 Word 分配时间
    line_timings_list = []
    mora_index = start_mora_index

    for line in chapter.lines:
        # 如果这一行需要的 mora 总数大于剩余的 mora，则丢弃该行及后续行
        required_mora_for_line = sum(w.mora for w in line.words)
        remaining_mora = len(moras) - mora_index
        if required_mora_for_line > remaining_mora:
            print(
                f"警告: 剩余 mora 数 {remaining_mora} 不足以渲染当前行，"
                "该行及后续行将被忽略"
            )
            break

        word_timings = []

        for word in line.words:
            mora_count = word.mora

            # 如果 mora 数为 0（不可打印字符），使用前一个或后一个 mora 的时间
            if mora_count == 0:
                # 使用最后一个已分配的 mora 的结束时间
                if word_timings:
                    start_time = word_timings[-1].end_ms
                    end_time = start_time
                else:
                    # 行首且 mora 为 0：对齐到即将到来的下一个 mora 的起始时间，避免 0 起始
                    if mora_index < len(moras):
                        start_time = moras[mora_index].start
                    else:
                        start_time = 0
                    end_time = start_time
            else:
                # 从 mora 序列中取出相应数量的 mora
                start_time = moras[mora_index].start
                end_time = moras[mora_index + mora_count - 1].end
                mora_index += mora_count

            word_timings.append(WordTiming(word, start_time, end_time))

        if word_timings:
            line_timings_list.append(LineTimings(word_timings))

    return line_timings_list, mora_index


class KeyboardListener:
    """非阻塞键盘监听器"""

    def __init__(self):
        self.key_pressed = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.old_settings = None

    def start(self):
        """启动监听线程"""
        self.running = True
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def _listen(self):
        """监听键盘输入"""
        # 保存终端设置
        fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(fd)

        try:
            tty.setcbreak(fd)
            while self.running:
                # 非阻塞检查是否有输入
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    self.key_pressed = ch
        finally:
            # 恢复终端设置
            if self.old_settings:
                termios.tcsetattr(fd, termios.TCSADRAIN, self.old_settings)

    def get_key(self) -> Optional[str]:
        """获取按下的键，并清除"""
        key = self.key_pressed
        self.key_pressed = None
        return key

    def stop(self):
        """停止监听"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)


def calc_mora_offset(chapters: list[Chapter], target_index: int) -> int:
    """计算目标章节在整首歌中的 mora 偏移量"""
    offset = 0
    for idx, chapter in enumerate(chapters):
        if idx >= target_index:
            break
        for line in chapter.lines:
            for word in line.words:
                offset += word.mora
    return offset


def get_karaoke_display(
    current_time_ms: float,
    line_timings_list: list[LineTimings],
    current_line_index: int,
    passed_style="bold green",
    upcoming_style="white",
    passed_bg_style="on black",
    upcoming_bg_style="on black",
) -> Panel:
    """
    根据当前时间生成卡拉OK显示面板。

    Args:
        current_time_ms: 当前时间（毫秒）
        line_timings_list: 所有行的时间信息
        current_line_index: 当前行索引
        passed_style: 已唱部分的样式
        upcoming_style: 未唱部分的样式

    Returns:
        显示面板
    """
    if current_line_index < 0 or current_line_index >= len(line_timings_list):
        return Panel(Text("等待开始...", justify="center"))

    current_line_timings = line_timings_list[current_line_index]

    # 确定当前已唱的词数
    highlight_word_count = 0
    for wt in current_line_timings.word_timings:
        if current_time_ms >= wt.start_ms:
            highlight_word_count += 1
        else:
            break

    # 构建显示文本
    display_text = Text(justify="center")

    # 添加已唱部分
    passed_words = current_line_timings.word_timings[:highlight_word_count]
    passed_text = "".join(wt.word.text for wt in passed_words)
    display_text.append(passed_text, style=f"{passed_style} {passed_bg_style}")

    # 添加未唱部分
    upcoming_words = current_line_timings.word_timings[highlight_word_count:]
    upcoming_text = "".join(wt.word.text for wt in upcoming_words)
    display_text.append(upcoming_text, style=f"{upcoming_style} {upcoming_bg_style}")

    # 构建完整显示（包含上下文行）
    full_display = Text(justify="center")

    # 前一行歌词
    if current_line_index > 0:
        prev_line_text = line_timings_list[current_line_index - 1].get_text()
        full_display.append(prev_line_text, style="dim grey\non black")
        full_display.append("\n")

    # 当前行
    full_display.append(display_text)
    full_display.append("\n")

    # 后一行歌词
    if current_line_index < len(line_timings_list) - 1:
        next_line_text = line_timings_list[current_line_index + 1].get_text()
        full_display.append(next_line_text, style="dim grey\non black")

    return Panel(
        full_display,
        title="🎵 终端卡拉 OK 🎵",
        border_style="cyan",
        width=None,
    )


def run_karaoke(
    chapter: Chapter,
    midi_file_path: Path,
    offset_ms: int = 0,
    audio_file_path: Optional[Path] = None,
    audio_delay_ms: int = 0,
    start_mora_index: int = 0,
):
    """
    运行卡拉OK渲染器。

    Args:
        chapter: 歌词章节
        midi_file_path: MIDI 文件路径
        offset_ms: 时间偏移量（毫秒）
        audio_file_path: 可选的音频文件路径（用于同步播放）
        audio_delay_ms: 音频相对于字幕的延迟（正数=音频延后，负数=音频提前）
        start_mora_index: 当前章节在完整 MIDI 序列中的起始 mora 下标
    """
    # 读取 MIDI 文件并获取 Mora 列表
    print("[*] 正在读取 MIDI 文件...")
    moras = list(read_midi(midi_file_path, offset_ms=offset_ms))
    print(f"[*] 读取到 {len(moras)} 个 Mora")

    # 转换为时间信息（传入起始 mora 偏移，使章节与完整 MIDI 对齐）
    print("[*] 正在处理歌词...")
    line_timings_list, _ = chapter_to_timings(chapter, moras, start_mora_index)
    print(f"[*] 处理完成，共 {len(line_timings_list)} 行")

    if not line_timings_list:
        print("[!] 错误: 没有可渲染的歌词行")
        return

    # 初始化音频播放器（如果提供了音频文件）
    audio_player: Optional[AudioPlayer] = None
    if audio_file_path:
        try:
            audio_player = AudioPlayer(audio_file_path, offset_ms=offset_ms)
            print("[*] 正在初始化音频播放器...")
        except RuntimeError as e:
            print(f"[!] {e}")
            audio_player = None

    # 开始渲染
    console = Console()

    # 计算第一行开始时间，默认跳过前奏（从第一行开始前1秒开始）
    first_line_start_ms = line_timings_list[0].word_timings[0].start_ms
    initial_offset_ms = max(0, first_line_start_ms - 1000)  # 第一行前1秒

    # 应用音频延迟调整
    audio_start_position_ms = max(0, initial_offset_ms - audio_delay_ms)

    current_line_index = 0

    # 启动键盘监听
    keyboard_listener = KeyboardListener()
    keyboard_listener.start()

    console.print(
        "[bold yellow]KARAOKE 启动:[/bold yellow] 按 'j'/'k' 跳转行，Ctrl+C 停止."
    )

    # 启动音频播放（从跳过前奏的位置开始）
    if audio_player:
        print("[*] 启动音频播放...")
        if audio_delay_ms != 0:
            print(f"[*] 音频延迟调整: {audio_delay_ms}ms")
        audio_player.play(start_position_ms=audio_start_position_ms)
        # 等待音频实际开始播放，减少启动延迟影响
        time.sleep(0.1)

    # 音频启动后再开始计时，确保同步
    start_time_ms = time.monotonic() * 1000 - initial_offset_ms

    with Live(
        get_karaoke_display(initial_offset_ms, line_timings_list, 0),
        refresh_per_second=10,
        screen=False,
    ) as live:
        try:
            while current_line_index < len(line_timings_list):
                # 检查键盘输入
                key = keyboard_listener.get_key()
                if key == "j" and current_line_index < len(line_timings_list) - 1:
                    # 下一行
                    current_line_index += 1
                    jump_to_ms = (
                        line_timings_list[current_line_index].word_timings[0].start_ms
                        - 1000
                    )
                    jump_to_ms = max(0, jump_to_ms)
                    # 同步音频跳转
                    if audio_player:
                        audio_player.seek(max(0, jump_to_ms - audio_delay_ms))
                        time.sleep(0.05)  # 等待音频跳转完成
                    start_time_ms = time.monotonic() * 1000 - jump_to_ms
                elif key == "k" and current_line_index > 0:
                    # 上一行
                    current_line_index -= 1
                    jump_to_ms = (
                        line_timings_list[current_line_index].word_timings[0].start_ms
                        - 1000
                    )
                    jump_to_ms = max(0, jump_to_ms)
                    # 同步音频跳转
                    if audio_player:
                        audio_player.seek(max(0, jump_to_ms - audio_delay_ms))
                        time.sleep(0.05)  # 等待音频跳转完成
                    start_time_ms = time.monotonic() * 1000 - jump_to_ms

                # 计算当前时间
                elapsed_time_ms = (time.monotonic() * 1000) - start_time_ms

                # 检查是否需要切换到下一行
                current_line_end_ms = line_timings_list[
                    current_line_index
                ].get_line_end_time_ms()
                if elapsed_time_ms >= current_line_end_ms:
                    current_line_index += 1
                    if current_line_index >= len(line_timings_list):
                        break

                # 更新显示
                live.update(
                    get_karaoke_display(
                        elapsed_time_ms, line_timings_list, current_line_index
                    )
                )

                time.sleep(0.01)

        except KeyboardInterrupt:
            pass
        finally:
            # 停止键盘监听
            keyboard_listener.stop()
            # 停止音频播放
            if audio_player:
                audio_player.stop()
                audio_player.cleanup()

    console.print("\n[bold cyan]--- 歌曲播放完毕 ---[/bold cyan]")


@click.command()
@click.option(
    "--midi",
    "-m",
    type=click.Path(exists=True),
    default="./vocal.mid",
    help="MIDI 文件路径（默认: ./vocal.mid）",
)
@click.option(
    "--lyrics",
    "-l",
    type=click.Path(exists=True),
    default="./lyrics.md",
    help="歌词文件路径（默认: ./lyrics.md）",
)
@click.option(
    "--audio",
    "-a",
    type=click.Path(exists=True),
    default=Path("./vocal.wav"),
    help="音频文件路径（支持 mp3, wav, ogg 等，可选）",
)
@click.option(
    "--chapter",
    "-c",
    type=int,
    default=0,
    help="选择章节索引（默认: 0）",
)
@click.option(
    "--offset",
    "-o",
    type=int,
    default=0,
    help="时间偏移量（毫秒，默认: 0）",
)
@click.option(
    "--audio-delay",
    "-d",
    type=int,
    default=0,
    help="音频延迟调整（毫秒，正数=音频延后，负数=音频提前，默认: 0）",
)
def cli_karaoke(
    midi: str,
    lyrics: str,
    audio: Optional[str],
    chapter: int,
    offset: int,
    audio_delay: int,
):
    """
    终端卡拉OK渲染器

    使用方法:
        python karaoke_renderer.py <MIDI文件路径> [选项]

    示例:
        python karaoke_renderer.py song.mid
        python karaoke_renderer.py song.mid -l lyrics.md -c 0 -o 100
    """
    from parser import LyricsTransformer, parser

    midi_path = Path(midi)
    lyrics_path = Path(lyrics)
    audio_path = Path(audio) if audio else None

    try:
        # 读取并解析歌词
        click.echo(f"[*] 正在读取歌词文件: {lyrics_path}")
        lyrics_text = lyrics_path.read_text(encoding="utf-8")
        parse_tree = parser.parse(lyrics_text)
        lyrics_obj = LyricsTransformer().transform(parse_tree)

        # 验证章节索引
        if chapter < 0 or chapter >= len(lyrics_obj.chapters):
            click.echo(
                f"[!] 错误: 章节索引 {chapter} 超出范围 (共 {len(lyrics_obj.chapters)} 章)",
                err=True,
            )
            return

        selected_chapter = lyrics_obj.chapters[chapter]
        mora_offset = calc_mora_offset(lyrics_obj.chapters, chapter)
        click.echo(f"[*] 已选择第 {chapter + 1} 章节")
        click.echo(f"[*] 章节前累计 mora 偏移: {mora_offset}")
        print(selected_chapter)

        # 验证音频文件
        if audio_path and not audio_path.exists():
            click.echo(f"[!] 错误: 音频文件不存在 {audio_path}", err=True)
            return

        if audio_path:
            click.echo(f"[*] 将使用音频文件: {audio_path}")

        # 运行卡拉OK
        run_karaoke(
            selected_chapter,
            midi_path,
            offset_ms=offset,
            audio_file_path=audio_path,
            audio_delay_ms=audio_delay,
            start_mora_index=mora_offset,
        )

    except FileNotFoundError as e:
        click.echo(f"[!] 文件不存在: {e}", err=True)
    except Exception as e:
        click.echo(f"[!] 错误: {e}", err=True)


if __name__ == "__main__":
    cli_karaoke()
