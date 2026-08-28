"""驱动接口：所有"大脑"实现 react(event) -> [命令]。"""


class Driver:
    name = "base"

    def __init__(self, pet_name: str = "团子"):
        self.pet_name = pet_name

    def react(self, event: dict) -> list:
        """根据事件返回一组命令（bus.SetState / bus.Say / bus.Hop）。

        必须纯同步、不许抛异常——宿主可能在任意线程调用。
        """
        raise NotImplementedError
