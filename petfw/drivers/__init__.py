"""驱动工厂：按配置选择大脑，可在运行期热切换。"""
from .base import Driver
from .rule import RuleDriver


def get_driver(mode: str, cp, pet_name: str) -> Driver:
    rule = RuleDriver(pet_name)
    if mode == "llm":
        from .llm import LLMDriver  # 延迟导入，避免没配好也拖累规则模式
        return LLMDriver(cp, fallback=rule)
    return rule
