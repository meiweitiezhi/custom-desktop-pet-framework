"""团子 —— 本地桌宠。

架构（harness 思想）：
  事件(点击/提醒/hook) -> Driver(规则 或 LLM) -> 命令(set_state/say/hop) -> Qt 宿主渲染
渲染内核只认 bus.py 里定义的命令，任何"大脑"都通过 drivers/base.py 的接口接入。
"""

__version__ = "0.1.0"
