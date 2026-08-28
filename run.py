"""团子入口：python run.py [--smoke]"""
import sys

if __name__ == "__main__":
    from petfw.host import main
    raise SystemExit(main())
