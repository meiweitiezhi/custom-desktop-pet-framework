"""天气心情灯：把天气大类映射成桌宠状态。

红线设计：
- 本模块是纯映射库，零网络零依赖；
- 联网抓取由使用者自己的本地脚本完成（拿到标准 /weather 结构后调用
  extract_state 或直接给桌宠发 weather 事件），所以本仓库不出现任何
  厂商地址与密钥；
- 托盘里的晴/雨菜单是无网络的本地演示。
"""

CONDITION_TO_STATE = {
    "Clear": "cheer",
    "Clouds": "idle",
    "Rain": "sleep",
    "Drizzle": "sleep",
    "Thunderstorm": "sleep",
    "Snow": "sleep",
    "Mist": "idle",
    "Fog": "idle",
    "Haze": "idle",
}


def state_for(condition: str) -> str:
    """任意输入都安全回落 idle。"""
    if not isinstance(condition, str):
        return "idle"
    return CONDITION_TO_STATE.get(condition, "idle")


def extract_state(payload):
    """从标准 /weather JSON 响应里提取应切换的状态；解析不了返回 None。"""
    if not isinstance(payload, dict):
        return None
    weather = payload.get("weather")
    if isinstance(weather, list) and weather and isinstance(weather[0], dict):
        main = weather[0].get("main")
        if isinstance(main, str):
            return state_for(main)
    return None
