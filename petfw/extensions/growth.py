"""Git 经验成长系统：读本地仓库提交数换称号，纯本地零网络。

等级由【今日提交数】决定（每天清零重来，鼓励日拱一卒）：
  0~4   咸鱼蛋      5~14  勤快蛋
  15~29 卷王蛋      30+   代码之蛋
"""
import subprocess

TITLES = ["咸鱼蛋", "勤快蛋", "卷王蛋", "代码之蛋"]
THRESHOLDS = (0, 5, 15, 30)


def parse_count(text):
    """把 git rev-list --count 的输出转成整数；任何异常形态返回 None。"""
    if not text:
        return None
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


def level_for(commits: int):
    """返回 (等级, 称号)。"""
    level = 1
    for i, threshold in enumerate(THRESHOLDS):
        if commits >= threshold:
            level = i + 1
    return level, TITLES[level - 1]


def _default_runner(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


class GrowthTracker:
    """扫一个 git 目录的当日提交数；runner 可注入以便测试。"""

    def __init__(self, repo_dir=".", runner=None):
        self.repo_dir = str(repo_dir)
        self.runner = runner or _default_runner

    def scan_today(self):
        out = self.runner(["git", "-C", self.repo_dir,
                           "rev-list", "--count", "HEAD", "--since=midnight"])
        n = parse_count(out)
        return n  # None = 不是仓库/git 不可用
