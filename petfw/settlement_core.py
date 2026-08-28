"""结算画面纯逻辑核心：文案生成 / 定时毫秒差 / BGM 探测。

刻意不依赖 Qt——宿主与测试都可以无头调用；表现层（settlement_window）
只消费这里输出的字符串，所有「今天结算成什么样」的计算都收敛在本文件。
"""
import datetime
from pathlib import Path

# 默认每日播报时刻；config.ini [settlement] daily_time 非法时也回落到它
DEFAULT_DAILY_TIME = "18:00"

# 文案框架行（tests 逐条断言，改字样要连测试一起改）
HEADER_LINE = "━━━━ 今日回合 ━━━━"
FOOTER_LINE = "———— 按任意处结束回放 ————"
LEVELUP_LINE = "↻ 称号进化发生 ↻"
FALLBACK_TITLE = "神秘蛋"


def build_settlement_lines(commits, title, leveled_up, date_str) -> list:
    """生成游戏风走马灯文案行序列。

    任何输入异常值都不炸：commits 转不成整数按 0 并夹到非负，
    title/date_str 转不成字符串用占位符。
    """
    try:
        n = max(0, int(commits))
    except (TypeError, ValueError):
        n = 0
    text_title = str(title).strip() if title is not None else ""
    text_title = text_title or FALLBACK_TITLE
    text_date = str(date_str).strip() if date_str is not None else "?"
    text_date = text_date or "?"

    lines = [
        HEADER_LINE,
        f"日期 {text_date}",
        "",
        f"KO 提交数 ×{n}",
        f"MVP 称号「{text_title}」",
    ]
    if leveled_up:
        lines.append(LEVELUP_LINE)
    lines += ["", FOOTER_LINE]
    return lines


def _parse_hhmm(text) -> "datetime.time":
    """解析 HH:MM；任何不合法输入回落 DEFAULT_DAILY_TIME。"""
    try:
        parts = str(text or "").strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        return datetime.time(hour, minute)
    except (ValueError, IndexError, TypeError):
        hour, minute = DEFAULT_DAILY_TIME.split(":")
        return datetime.time(int(hour), int(minute))


def next_delay_ms(now: "datetime.datetime", daily_time=DEFAULT_DAILY_TIME) -> int:
    """当前时刻到最近一个 daily_time 的毫秒差（单发 QTimer 用）。

    已过点（含恰好压点）则排明天 —— 触发后在 timeout 里再算下一次，
    天然跨天自续，不需要周期定时器。
    """
    hhmm = _parse_hhmm(daily_time)
    target = datetime.datetime.combine(now.date(), hhmm)
    if target <= now:
        target += datetime.timedelta(days=1)
    return int((target - now).total_seconds() * 1000)


# 探测顺序即优先级；两条都只是 owner 放在 assets/local/ 的本地私有音频
BGM_CANDIDATES = ("bgm.mp3", "bgm.m4a")


def find_bgm(assets_dir, extra_dirs=()) -> "Path | None":
    """在 assets/local/ 下找 BGM 音频文件；没有就返回 None。

    素材防火墙：该目录整体被 .gitignore 排除，绝不入库也不进 exe datas，
    所以这里找不到文件是完全正常的默认形态。
    extra_dirs：frozen 分发场景的兜底查找位置（如 exe 旁的 assets/local），
    让朋友拿到 exe 后自行投放 mp3 也能生效。
    """
    try:
        dirs = [Path(assets_dir) / "local"]
        dirs += [Path(d) / "local" for d in extra_dirs]
        for d in dirs:
            for name in BGM_CANDIDATES:
                candidate = d / name
                if candidate.is_file():
                    return candidate
    except (OSError, TypeError):
        return None
    return None
