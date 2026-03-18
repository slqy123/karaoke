#!/bin/python
import os
from pathlib import Path
import sys

if (p := str(Path(__file__).parent)) not in sys.path:
    sys.path.append(p)

from parser import Chapter, Lyrics, LyricsTransformer, parser
from midi import read_midi
from karaoke_renderer import chapter_to_timings, calc_mora_offset

# 借用时长时的最小保留时长（单位：厘秒）
MIN_WORD_DURATION = 8


def format_ass_time(ms: int) -> str:
    """将毫秒转换为 ASS 时间格式 (H:MM:SS.CC)"""
    centiseconds = ms // 10
    seconds = centiseconds // 100
    minutes = seconds // 60
    hours = minutes // 60

    cs = centiseconds % 100
    s = seconds % 60
    m = minutes % 60
    h = hours

    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass_karaoke(
    lyrics: Lyrics,
    midi_file_path: Path,
    output_path: Path,
    offset_ms: int = 0,
    lead_time_ms: int = 5000,  # 提前5秒出现
    guide_dot_duration_ms: int = 1000,  # 引导点的时长
):
    """
    生成 ASS 卡拉OK字幕文件

    Args:
        lyrics: 完整歌词对象
        midi_file_path: MIDI 文件路径
        output_path: 输出 ASS 文件路径
        offset_ms: MIDI 时间偏移
        lead_time_ms: 每行提前出现的时间（毫秒）
        guide_dot_duration_ms: 引导点的时长（毫秒）
    """
    # 读取 MIDI
    print("[*] 正在读取 MIDI 文件...")
    moras = list(read_midi(midi_file_path, offset_ms=offset_ms))
    print(f"[*] 读取到 {len(moras)} 个 Mora")
    print(f"[*] 起始时间: {moras[0].start if moras else '无'} 毫秒")

    # ===== 第一阶段：收集所有章节的行信息 =====
    class LineInfo:
        def __init__(self, chapter_idx, line_idx, line_timing, mora_offset, style):
            self.chapter_idx = chapter_idx
            self.line_idx = line_idx
            self.line_timing = line_timing
            self.mora_offset = mora_offset
            self.style = style
            self.first_word_start = line_timing.word_timings[0].start_ms
            self.line_end_ms = line_timing.word_timings[-1].end_ms
            self.line_start_ms = None  # 稍后计算
            self.line_mora_count = sum(wt.word.mora for wt in line_timing.word_timings)
    
    k1_lines = []
    k2_lines = []
    current_mora_offset = 0
    
    for chapter_idx, chapter in enumerate(lyrics.chapters):
        print(f"[*] 处理章节 {chapter_idx + 1}...")
        
        line_timings_list, next_mora_offset = chapter_to_timings(
            chapter, moras, current_mora_offset
        )
        
        if not line_timings_list:
            current_mora_offset = next_mora_offset
            continue
        
        # 收集所有行信息
        line_mora_offset = current_mora_offset
        for line_idx, line_timing in enumerate(line_timings_list):
            style = "K1" if line_idx % 2 == 0 else "K2"
            line_info = LineInfo(chapter_idx, line_idx, line_timing, line_mora_offset, style)
            
            if style == "K1":
                k1_lines.append(line_info)
            else:
                k2_lines.append(line_info)
            
            line_mora_offset += line_info.line_mora_count
        
        current_mora_offset = next_mora_offset
    
    # ===== 第二阶段：为 K1 和 K2 分别计算 lead_time =====
    min_gap_ms = ASS_CONFIG.FADE_IN_MS + ASS_CONFIG.FADE_OUT_MS + 100
    
    def calculate_lead_times(lines, style_name):
        """为一组同样式的行计算起始时间"""
        for i, line_info in enumerate(lines):
            ideal_start_ms = line_info.first_word_start - lead_time_ms
            
            if i == 0:
                # 第一行：如果理想起始时间小于 0，则从 0 开始并缩短 lead_time
                if ideal_start_ms < 0:
                    line_info.line_start_ms = 0
                else:
                    line_info.line_start_ms = ideal_start_ms
            else:
                # 检查与上一行的间隔
                prev_line = lines[i - 1]
                required_start_ms = prev_line.line_end_ms + min_gap_ms
                
                if ideal_start_ms < required_start_ms:
                    # 间隔不足，尝试缩短 lead_time
                    if line_info.first_word_start >= required_start_ms:
                        line_info.line_start_ms = required_start_ms
                        print(f"[!] {style_name} 行 {i + 1}: lead_time 缩短为 {line_info.first_word_start - required_start_ms}ms")
                    else:
                        # 即使 lead_time = 0 也无法满足间隔要求
                        raise ValueError(
                            f"{style_name} 行 {i + 1}: 与上一行间隔不足 {min_gap_ms}ms，"
                            f"上一行结束于 {prev_line.line_end_ms}ms，"
                            f"当前行第一个词在 {line_info.first_word_start}ms 开始，"
                            f"需要至少 {required_start_ms - line_info.first_word_start}ms 的额外时间"
                        )
                else:
                    line_info.line_start_ms = ideal_start_ms
    
    calculate_lead_times(k1_lines, "K1")
    calculate_lead_times(k2_lines, "K2")
    
    # ===== 第三阶段：生成引导点 =====
    all_dialogues = []
    
    # 为每个章节生成引导点
    chapters_first_lines = {}
    for line_info in k1_lines + k2_lines:
        if line_info.chapter_idx not in chapters_first_lines:
            chapters_first_lines[line_info.chapter_idx] = line_info
        elif line_info.line_idx < chapters_first_lines[line_info.chapter_idx].line_idx:
            chapters_first_lines[line_info.chapter_idx] = line_info
    
    for chapter_idx in sorted(chapters_first_lines.keys()):
        first_line = chapters_first_lines[chapter_idx]
        first_word_start_ms = first_line.first_word_start
        
        guide_total_duration = guide_dot_duration_ms * 3
        if first_word_start_ms >= guide_total_duration:
            guide_start_ms = first_word_start_ms - guide_total_duration
            guide_end_ms = first_word_start_ms
            
            guide_text = ""
            for dot_idx in range(3):
                dot_start_cs = guide_dot_duration_ms // 10
                guide_text += f"{{\\k{dot_start_cs}}}●"
            
            guide_dialogue = (
                f"Dialogue: 0,"
                f"{format_ass_time(guide_start_ms)},"
                f"{format_ass_time(guide_end_ms)},"
                f"LEAD,,0,0,0,,{guide_text}"
            )
            all_dialogues.append(guide_dialogue)
    
    # ===== 第四阶段：生成所有 Dialogue =====
    all_lines = k1_lines + k2_lines
    all_lines.sort(key=lambda x: (x.chapter_idx, x.line_idx))
    
    for line_info in all_lines:
        # 计算到下一个同样式行的时间（用于延长显示）
        same_style_lines = k1_lines if line_info.style == "K1" else k2_lines
        current_idx = same_style_lines.index(line_info)
        
        next_same_style_start_ms = None
        if current_idx + 1 < len(same_style_lines):
            next_same_style_start_ms = same_style_lines[current_idx + 1].line_start_ms
        
        # 生成卡拉OK标记文本
        actual_lead_time = line_info.first_word_start - line_info.line_start_ms
        karaoke_text = generate_karaoke_text(
            line_info.line_timing,
            actual_lead_time,
            next_same_style_start_ms,
            line_info.line_end_ms,
            moras,
            line_info.mora_offset,
        )
        
        # 创建 Dialogue 行
        dialogue = (
            f"Dialogue: 0,"
            f"{format_ass_time(max(0, line_info.line_start_ms))},"
            f"{format_ass_time(line_info.line_end_ms)},"
            f"{line_info.style},,0,0,0,,{karaoke_text}"
        )
        
        all_dialogues.append(dialogue)

    # 生成 ASS 头部
    ass_content = generate_ass_header()

    # 添加所有 Dialogue 到内容
    ass_content += "\n[Events]\n"
    ass_content += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ass_content += "\n".join(all_dialogues)

    # 写入文件
    output_path.write_text(ass_content, encoding="utf-8")
    print(f"[*] ASS 字幕已生成: {output_path}")


def generate_karaoke_text(
    line_timing,
    lead_time_ms: int,
    next_same_style_start_ms: int | None,
    line_end_ms: int,
    moras: list,
    mora_offset: int,
) -> str:
    """生成单行的卡拉OK标记文本"""
    parts = []

    # 添加前导时间
    lead_time_cs = lead_time_ms // 10  # 转换为厘秒
    parts.append(f"{{\\k{lead_time_cs}}}")

    # 当前处理的 mora 索引
    current_mora_idx = mora_offset

    # ===== 预处理：计算每个 Word 的实际时长（考虑借与被借）=====
    word_timings = line_timing.word_timings
    durations_cs = [wt.duration_ms // 10 for wt in word_timings]

    # 第一遍：尝试从前一个 Word 借
    for i, wt in enumerate(word_timings):
        word = wt.word
        is_transparent = all(w in " 　" for w in word.text)  # 透明字符判断

        if durations_cs[i] == 0 and not is_transparent:
            print(wt, word)
            # 需要借时长，优先从前面借
            borrowed = False
            for j in range(i - 1, -1, -1):
                if (
                    durations_cs[j] >= 2 * MIN_WORD_DURATION
                ):  # 前一个 Word 可以借（保留至少 MIN_WORD_DURATION）
                    durations_cs[j] -= MIN_WORD_DURATION
                    durations_cs[i] = MIN_WORD_DURATION
                    borrowed = True
                    break

            # 如果前面借不到，标记需要从后面借
            if not borrowed:
                durations_cs[i] = -1  # 标记为待借状态

    # 第二遍：对于还未借到的 Word，尝试从后面借
    for i, wt in enumerate(word_timings):
        word = wt.word
        is_transparent = all(w in " 　" for w in word.text)

        if durations_cs[i] == -1 and not is_transparent:
            # 需要从后面借
            borrowed = False
            for j in range(i + 1, len(durations_cs)):
                if durations_cs[j] > 2 * MIN_WORD_DURATION:
                    durations_cs[j] -= MIN_WORD_DURATION
                    durations_cs[i] = MIN_WORD_DURATION
                    borrowed = True
                    break

            # 还是借不到，就设为 1
            if not borrowed:
                durations_cs[i] = 1

    # ===== 生成卡拉OK标记 =====
    for word_idx, (word_timing, duration_cs) in enumerate(
        zip(word_timings, durations_cs)
    ):
        word = word_timing.word
        mora_count = word.mora

        # 检查当前词的第一个 mora 是否与前一个 mora 连续
        if (
            current_mora_idx > mora_offset and mora_count > 0
        ):  # 不是行的第一个词且有 mora
            prev_mora = moras[current_mora_idx - 1]
            current_mora = moras[current_mora_idx]

            # 如果不连续，插入间隙时间
            if not current_mora.continous:
                gap_ms = current_mora.start - prev_mora.end
                if gap_ms > 0:
                    gap_cs = gap_ms // 10
                    parts.append(f"{{\\k{gap_cs}}}")

        # 添加词的卡拉OK标记
        text = word.text

        # 处理 ruby 注音
        if word.ruby:
            text = f"{text}|<{word.ruby}"

        parts.append(f"{{\\k{duration_cs}}}{text}")

        # 更新 mora 索引（只有 mora > 0 时才增加）
        if mora_count > 0:
            current_mora_idx += mora_count

    # 计算末尾延长时间
    if next_same_style_start_ms is not None:
        # 有下一个同样式行，计算延长时间
        extension_ms = next_same_style_start_ms - line_end_ms

        # 只有当间隔大于等于提前时间时才延长
        if extension_ms >= lead_time_ms:
            extension_cs = extension_ms // 10
            parts.append(f"{{\\k{extension_cs}}}")

    return "".join(parts)


def hex_color_to_ass(color: str) -> str:
    return color[4:] + color[2:4] + color[:2]


class AssConfig:
    BORD = int(os.getenv("BORD", 5))
    BORD_FURI = int(os.getenv("BORD_FURI", 3))
    FADE_IN_MS = int(os.getenv("FADE_IN_MS", 800))
    FADE_OUT_MS = int(os.getenv("FADE_OUT_MS", 200))
    FONTSIZE = int(os.getenv("FONTSIZE", 96))
    MARGIN_H = int(os.getenv("MARGIN_H", 64))
    MARGIN_V = int(os.getenv("MARGIN_V", 48))
    RUBY_OFFSET_INT = int(os.getenv("RUBY_OFFSET", 10))
    OVERLAY_COLOR: str = hex_color_to_ass(os.getenv("OVERLAY_COLOR", "0000FF"))

    @property
    def RUBY_OFFSET(self):
        if self.RUBY_OFFSET_INT == 0:
            return ""
        return ("+" if self.RUBY_OFFSET_INT > 0 else "-") + str(
            abs(self.RUBY_OFFSET_INT)
        )

    @property
    def MARGIN_K1(self) -> int:
        return self.MARGIN_V * 2 + self.FONTSIZE // 2 * 3

    @property
    def MARGIN_LEAD(self) -> int:
        return self.MARGIN_V * 3 + self.FONTSIZE * 3


ASS_CONFIG = AssConfig()


def generate_ass_header() -> str:
    """生成 ASS 文件头部"""
    import re

    return re.sub(
        r"__([A-Z][A-Z0-9]*(_[A-Z][A-Z0-9]*)*)__",
        lambda s: str(getattr(ASS_CONFIG, s[1])),
        r"""[Script Info]
Title: Karaoke Subtitle
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: 1080

[Aegisub Project Garbage]
Audio File: origin.wav
Video File: output.mp4
Video AR Mode: 4
Video AR Value: 1.777778
Video Zoom Percent: 0.500000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: K1,sans-serif,__FONTSIZE__,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,1,__MARGIN_H__,__MARGIN_H__,__MARGIN_K1__,1
Style: K2,sans-serif,__FONTSIZE__,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,3,__MARGIN_H__,__MARGIN_H__,__MARGIN_V__,1
Style: LEAD,sans-serif,__FONTSIZE__,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,1,__MARGIN_H__,__MARGIN_H__,__MARGIN_LEAD__,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,code syl all,fxgroup.kara=syl.inline_fx==""
Comment: 1,0:00:00.00,0:00:00.00,K1,overlay,0,0,0,template syl noblank all fxgroup kara,!retime("line",-100,500)!{\pos($center,$middle)\an5\shad0\fad(__FADE_IN_MS__,__FADE_OUT_MS__)\1c&H__OVERLAY_COLOR__&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord__BORD__}
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,template syl all fxgroup kara,!retime("line",-500,500)!{\pos($center,$middle)\an5\fad(__FADE_IN_MS__,__FADE_OUT_MS__)}
Comment: 1,0:00:18.65,0:00:20.65,K1,overlay,0,0,0,template furi all,!retime("line",-100,500)!{\pos($center,!$middle__RUBY_OFFSET__!)\an5\shad0\fad(__FADE_IN_MS__,__FADE_OUT_MS__)\1c&H__OVERLAY_COLOR__&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord__BORD_FURI__}
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,template furi all,!retime("line",-500,500)!{\pos($center,!$middle__RUBY_OFFSET__!)\an5\fad(__FADE_IN_MS__,__FADE_OUT_MS__)}
Comment: 0,0:00:00.00,0:00:00.00,K1,music,0,0,0,template fx no_k,!retime("line",-500,500)!{\pos($center,!$middle!)\an5\1c&H505050&\3c&HFFFFFFF&}
Comment: 0,0:00:00.00,0:00:00.00,K2,,0,0,0,code syl all,fxgroup.kara=syl.inline_fx==""
Comment: 1,0:00:00.00,0:00:00.00,K2,overlay,0,0,0,template syl noblank all fxgroup kara,!retime("line",-100,500)!{\pos($center,$middle)\an5\shad0\fad(__FADE_IN_MS__,__FADE_OUT_MS__)\1c&H__OVERLAY_COLOR__&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord__BORD__}
Comment: 0,0:00:00.00,0:00:00.00,K2,,0,0,0,template syl all fxgroup kara,!retime("line",-500,500)!{\pos($center,$middle)\an5\fad(__FADE_IN_MS__,__FADE_OUT_MS__)}
Comment: 1,0:00:18.65,0:00:20.65,K2,overlay,0,0,0,template furi all,!retime("line",-100,500)!{\pos($center,!$middle__RUBY_OFFSET__!)\an5\shad0\fad(__FADE_IN_MS__,__FADE_OUT_MS__)\1c&H__OVERLAY_COLOR__&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord__BORD_FURI__}
Comment: 0,0:00:00.00,0:00:00.00,K2,,0,0,0,template furi all,!retime("line",-500,500)!{\pos($center,!$middle__RUBY_OFFSET__!)\an5\fad(__FADE_IN_MS__,__FADE_OUT_MS__)}
Comment: 0,0:00:00.00,0:00:00.00,K2,music,0,0,0,template fx no_k,!retime("line",-500,500)!{\pos($center,!$middle!)\an5\1c&H505050&\3c&HFFFFFFF&}
""",
    )


if __name__ == "__main__":
    import click

    @click.command()
    @click.option(
        "--lyrics",
        "-l",
        type=click.Path(exists=True),
        default=Path("./lyrics.md"),
        help="歌词文件路径",
    )
    @click.option(
        "--midi",
        "-m",
        type=click.Path(exists=True),
        default=Path("./vocal.mid"),
        help="MIDI 文件路径",
    )
    @click.option(
        "--output",
        "-o",
        type=click.Path(),
        default="output.ass",
        help="输出 ASS 文件路径（默认: output.ass）",
    )
    @click.option(
        "--offset",
        type=int,
        default=0,
        help="MIDI 时间偏移（毫秒，默认: 0）",
    )
    @click.option(
        "--lead-time",
        type=int,
        default=5000,
        help="每行提前出现时间（毫秒，默认: 5000）",
    )
    @click.option(
        "--guide-dot-duration",
        type=int,
        default=1000,
        help="引导点的时长（毫秒，默认: 1000）",
    )
    def cli(
        lyrics: str,
        midi: str,
        output: str,
        offset: int,
        lead_time: int,
        guide_dot_duration: int,
    ):
        """生成 ASS 卡拉OK字幕文件"""
        lyrics_path = Path(lyrics)
        midi_path = Path(midi)
        output_path = Path(output)

        # 读取并解析歌词
        click.echo(f"[*] 正在读取歌词文件: {lyrics_path}")
        lyrics_text = lyrics_path.read_text(encoding="utf-8")
        parse_tree = parser.parse(lyrics_text)
        lyrics_obj = LyricsTransformer().transform(parse_tree)

        click.echo(f"[*] 解析到 {len(lyrics_obj.chapters)} 个章节")

        # 生成 ASS 字幕
        generate_ass_karaoke(
            lyrics_obj,
            midi_path,
            output_path,
            offset_ms=offset,
            lead_time_ms=lead_time,
            guide_dot_duration_ms=guide_dot_duration,
        )

        click.echo(f"[✓] 完成！输出文件: {output_path}")

    cli()
