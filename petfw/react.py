"""命令行给桌宠发事件（自动从 config.ini 读端口与 token）。

用例：
  python -m petfw.react edit            # 最简
  python -m petfw.react done 收工啦     # 带备注
  python -m petfw.react --ini 其他.ini edit   # 指定配置

hook 里永远别写死 token——每次启动会轮换；统一用本命令即可。
"""
import configparser
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import CONFIG_PATH  # frozen 态指向 exe 同目录，开发态指向仓库根


def send(cfg_path, event: str, message: str = "") -> bool:
    cp = configparser.ConfigParser()
    cp.read(cfg_path, encoding="utf-8")
    port = int(cp.get("bridge", "port", fallback="8321"))
    token = cp.get("bridge", "token", fallback="")
    qs = urllib.parse.urlencode(
        {"event": event[:40], "message": str(message)[:200], "token": token})
    url = f"http://127.0.0.1:{port}/react?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = CONFIG_PATH
    if argv and argv[0].endswith(".ini"):
        cfg = Path(argv.pop(0))
    if not argv:
        print(__doc__)
        return 2
    ok = send(cfg, argv[0], argv[1] if len(argv) > 1 else "")
    print("已送达" if ok else "发送失败（桌宠没在运行或桥接未开）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
