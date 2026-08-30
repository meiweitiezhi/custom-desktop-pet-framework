"""配置读写：config.ini 不存在时自动生成模板并注入随机 token。

红线：
- 模板里不放任何厂商地址/模型名，具体填法只存在于本地未入库的说明文档；
- config.ini 含密钥，已被 .gitignore 排除，绝不入库。
"""
import configparser
import secrets
from pathlib import Path

from .paths import CONFIG_PATH  # frozen 态指向 exe 同目录，开发态指向仓库根

TEMPLATE = """\
[pet]
name = 团子
display_size = 96
# 渲染节拍（毫秒/帧）：缺省 33 = 30fps 载波（配合 30fps 密度烘焙最丝滑）；省电可改 66
tick_ms = 33

[brain]
# rule = 本地规则大脑（离线可用）；llm = 调大模型说话 + 决定表情
mode = rule
# OpenAI 兼容网关地址（具体填什么见 docs_local/*.local.md，本文件不入库）
api_base =
api_key =
model =

[bridge]
enabled = true
port = 8321
# ZCode hook 调用时需要带上它证明身份；留空则首次运行自动生成
token =

[reminders]
enabled = true
interval_minutes = 45

[settlement]
# 收工全屏结算画面：hook 的 done 信号与每日定时（daily_time）都会触发；
# 有本地 BGM（assets/local/bgm.mp3，永不入库）时循环播放，播不了静默无声。
enabled = true
daily_time = 18:00
bgm = true
# BGM 变速倍率（合法 0.5~4.0）：默认 2.5 倍速最鬼畜；嫌太鬼畜调回 1.5 或 1.0
bgm_rate = 2.5

[weather]
# 可选玩法：天气心情灯。联网抓取由你自己的本地脚本完成（拿到标准 /weather
# 结构 JSON 后可用 petfw/extensions/weather.extract_state 解析出状态），
# 本仓库因此不出现在何厂商地址与密钥。
enabled = false
city =

[sound]
# 互动反馈音效：运行期程序合成（stdlib 实时算出 WAV 落临时目录），
# 零素材文件、离线可用；enabled = false 时整体安静。
enabled = true
volume = 0.6
# 单击专属音效：填本地 wav 路径（放 assets/local/click.wav 之类），留空则用内置 pop
click_sfx = assets/local/click.wav
# 点歌整首（单击触发）：默认曲目 assets/local/bgm.mp3（本地私有、永不入库），
# music_volume 只管这首歌的音量，与上面互动音效的 volume 互不影响；
# 文件缺失或多媒体后端不可用时，单击回落「戳我」定格演出（全程静默不崩）。
music_file = assets/local/bgm.mp3
# BGM 全量下线（朋友嫌吵）；想恢复整套音乐改 true
enabled_music = false
music_volume = 0.6
"""


def load(path: Path = CONFIG_PATH) -> configparser.ConfigParser:
    """读取配置；文件不存在就生成带随机 token 的模板再读。"""
    if not path.exists():
        path.write_text(
            TEMPLATE.replace("token =", f"token = {secrets.token_hex(8)}"),
            encoding="utf-8",
        )
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    _ensure_token(cp, path)
    return cp


def _ensure_token(cp: configparser.ConfigParser, path: Path) -> None:
    token = (cp.get("bridge", "token", fallback="") or "").strip()
    if not token:
        token = secrets.token_hex(8)
        if not cp.has_section("bridge"):
            cp.add_section("bridge")
        cp.set("bridge", "token", token)
        with open(path, "w", encoding="utf-8") as f:
            cp.write(f)


def rotate_token(path: Path = CONFIG_PATH) -> str:
    """轮换 bridge token 并落盘。

    每次启动都换新 —— 外部脚本不要写死旧 token，
    统一走 `python -m petfw.react <event>`（它每次现读 ini）。
    """
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    token = secrets.token_hex(8)
    if not cp.has_section("bridge"):
        cp.add_section("bridge")
    cp.set("bridge", "token", token)
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)
    return token


def llm_ready(cp: configparser.ConfigParser) -> bool:
    """三要素齐了才允许 llm 大脑上线，缺任何一个自动降级规则脑。"""
    return all((cp.get("brain", k, fallback="") or "").strip()
               for k in ("api_base", "api_key", "model"))


def brain_mode(cp: configparser.ConfigParser) -> str:
    """最终生效的大脑模式：配置不全一律回落 rule，保证离线永远可玩。"""
    mode = (cp.get("brain", "mode", fallback="rule") or "").strip().lower()
    return "llm" if mode == "llm" and llm_ready(cp) else "rule"


def llm_kwargs(cp: configparser.ConfigParser) -> dict:
    """LLM 驱动需要的配置切片；字段可能为空，由驱动自行判定。"""
    return {
        "api_base": cp.get("brain", "api_base", fallback="").strip(),
        "api_key": cp.get("brain", "api_key", fallback="").strip(),
        "model": cp.get("brain", "model", fallback="").strip(),
    }
