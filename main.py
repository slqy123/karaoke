import time
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.panel import Panel

# --- 核心渲染函数 ---

def get_karaoke_display(
    current_time: float,
    lyrics: list[str],
    timestamps: list[list[float]],
    current_line_index: int,
    passed_style="bold green",  # 已唱部分的样式
    upcoming_style="white",      # 未唱部分的样式
    passed_bg_style="on black",  # 保持背景一致
    upcoming_bg_style="on black",
) -> Panel:
    """
    根据当前时间，生成歌词显示的 Rich Panel 对象。
    
    Args:
        current_time: 当前时间（相对起始时间）。
        lyrics: 所有歌词行列表。
        timestamps: 每行歌词的字符时间戳列表。
        current_line_index: 当前正在演唱的歌词行索引。
    
    Returns:
        一个包含当前歌词渲染状态的 Panel 对象。
    """
    
    # 确保索引在有效范围内
    if current_line_index < 0 or current_line_index >= len(lyrics):
        return Panel(Text("等待开始...", justify="center"))

    current_line = lyrics[current_line_index]
    current_line_timestamps = timestamps[current_line_index]
    
    # 1. 确定高亮字符数量
    
    # 检查当前时间超过了哪个字符的起始时间
    highlight_char_count = 0
    
    # 由于时间戳是递增的，我们从头开始查找
    for ts in current_line_timestamps:
        if current_time >= ts:
            highlight_char_count += 1
        else:
            # 时间未到，停止计数
            break

    # 2. 构建 Rich Text 对象
    
    # 获取已唱部分和未唱部分的文本
    passed_text = current_line[:highlight_char_count]
    upcoming_text = current_line[highlight_char_count:]

    # 创建完整的 Text 对象
    display_text = Text(justify="center") # 居中对齐

    # 添加已唱部分 (高亮颜色)
    # 注意：我们混合使用 passed_style 和 passed_bg_style 来确保文本在 Panel 中有背景色
    display_text.append(passed_text, style=f"{passed_style} {passed_bg_style}")
    
    # 添加未唱部分 (普通颜色)
    display_text.append(upcoming_text, style=f"{upcoming_style} {upcoming_bg_style}")
    
    # 3. 添加上下行歌词（灰色）
    
    full_display = Text(justify="center")
    
    # 前一行歌词（如果存在）
    if current_line_index > 0:
        full_display.append(lyrics[current_line_index - 1], style="dim grey\non black")
        full_display.append("\n") # 换行

    # 当前行歌词（高亮渲染）
    full_display.append(display_text)
    full_display.append("\n") # 换行
    
    # 后一行歌词（如果存在）
    if current_line_index < len(lyrics) - 1:
        full_display.append(lyrics[current_line_index + 1], style="dim grey\non black")

    # 4. 包装成 Panel
    return Panel(
        full_display, 
        title="🎵 终端卡拉 OK 🎵", 
        border_style="cyan",
        # 设置最小宽度以确保 Panel 足够大，并适应终端变化
        width=None # 默认 None，Rich 会自动适应终端宽度
    )

# --- 示例数据 ---

# 假设一首歌曲
LYRICS = [
    "晚风吹起你鬓角的头发",
    "彷佛昨日又再现",
    "歌声还萦绕耳旁",
    "我们却早已分散在天涯",
]

# 对应字符的时间戳 (单位: 秒)
# 注意：相邻时间戳相同 (如 4.0, 4.0) 表示两个字同时出现。
TIMESTAMPS = [
    [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9, 2.1, 2.3], # 晚风吹起你鬓角的头发
    [3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2],               # 彷佛昨日又再现
    [5.0, 5.2, 5.4, 5.6, 5.8, 6.0, 6.2, 6.4],          # 歌声还萦绕耳旁
    [7.0, 7.2, 7.4, 7.6, 7.8, 8.0, 8.2, 8.4, 8.6, 8.8], # 我们却早已分散在天涯
]


# --- 主运行逻辑 ---

def run_karaoke_renderer():
    """主渲染循环"""
    
    console = Console()
    
    # 记录起始时间
    start_time = time.monotonic()
    
    # 初始化歌词行索引
    current_line_index = 0
    
    console.print("[bold yellow]KARAOKE 启动:[/bold yellow] 按 Ctrl+C 停止.")

    # Live 模式，每秒刷新 10 次，以保证流畅的字符过渡
    # screen=False 确保我们只占用 Panel 所需的空间
    with Live(
        get_karaoke_display(0, LYRICS, TIMESTAMPS, -1), 
        refresh_per_second=10, 
        screen=False
    ) as live:
        
        while current_line_index < len(LYRICS):
            # 1. 计算当前时间
            elapsed_time = time.monotonic() - start_time
            
            # 2. 检查是否需要切换到下一行歌词
            
            # 如果当前行的时间戳列表不为空，且当前时间超过了该行最后一个字符的时间戳
            current_line_timestamps = TIMESTAMPS[current_line_index]
            if current_line_timestamps and elapsed_time > current_line_timestamps[-1] + 0.5:
                 # 加上 0.5 秒的缓冲时间，让最后几个字能完整显示一下
                current_line_index += 1
                
                # 如果已经到达最后一行，退出循环
                if current_line_index >= len(LYRICS):
                    break

            # 3. 更新 Live 显示
            # 传入当前的 elapsed_time 作为渲染的参考时间
            live.update(get_karaoke_display(elapsed_time, LYRICS, TIMESTAMPS, current_line_index))

            # 小步长等待，由 Live 机制自动控制刷新率
            time.sleep(0.01) # 增加平滑性

    console.print("\n[bold cyan]--- 歌曲播放完毕 ---[/bold cyan]")


if __name__ == "__main__":
    run_karaoke_renderer()
