"""团子发布门禁：一次跑齐三道闸，任一失败退出码非零。

1. 单元测试全绿（无 GUI 无网络）
2. 版权红线：仓库跟踪文件里不允许出现任何图片
3. 厂商红线：仓库跟踪文件里不允许出现真实 API 地址/密钥痕迹字样

用法：python tools/check.py
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 黑名单正则按片段动态拼装，避免本文件自身被扫描命中；拼接结果与原字面量一致
_VENDOR_FRAGMENTS = (
    r"z\." + "ai",
    "gl" + "m",
    "bigmo" + "del",
    "bea" + r"rer\s+[a-z0-9]",
    "sk-" + "[a-z0-9]",
    "anthro" + "pic",
    "opena" + r"i\.com",
)
VENDOR_PATTERN = re.compile("|".join(_VENDOR_FRAGMENTS), re.IGNORECASE)

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".ini.example"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def tracked_files():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def scan_red_lines():
    problems = []
    for rel in tracked_files():
        suffix = pathlib.Path(rel).suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            problems.append(f"[版权图] {rel} —— 表情包图片不许入库")
            continue
        if suffix not in TEXT_SUFFIXES:
            continue
        content = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(content.splitlines(), 1):
            if VENDOR_PATTERN.search(line):
                problems.append(f"[厂商字样] {rel}:{lineno}: {line.strip()[:80]}")
    return problems


def run_tests():
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests",
         "-t", "."], cwd=str(ROOT))
    return r.returncode == 0


def main():
    ok = True
    print("== 闸门1：单元测试 ==")
    if not run_tests():
        ok = False
        print("  ✗ 测试未全绿")
    else:
        print("  ✓ 全绿")

    print("== 闸门2&3：版权图 / 厂商字样扫描（基于 git ls-files）==")
    problems = scan_red_lines()
    if problems:
        ok = False
        for p in problems:
            print("  ✗", p)
    else:
        print("  ✓ 零命中")

    print("\n结论:", "PASS ✅ 可提交" if ok else "FAIL ❌ 先修上面的问题")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
