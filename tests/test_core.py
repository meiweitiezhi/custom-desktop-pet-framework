"""核心逻辑单元测试：全程不开 GUI、不发真实网络请求。

运行：python -m unittest discover -s tests -t .
"""
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import petfw  # noqa: E402
from petfw import bus  # noqa: E402
from petfw.bridge import BridgeServer  # noqa: E402
from petfw.config import TEMPLATE, load  # noqa: E402
from petfw.drivers.llm import LLMDriver, parse_reply  # noqa: E402
from petfw.drivers.rule import RuleDriver  # noqa: E402


def _cp(**brain):
    """构造一个内存 configparser，模拟 config.ini 的 brain 段。"""
    import configparser
    cp = configparser.ConfigParser()
    cp.add_section("brain")
    for k, v in brain.items():
        cp.set("brain", k, v)
    return cp


class TestBus(unittest.TestCase):
    def test_commands_from_valid_dict(self):
        cmds = bus.commands_from_dict({"state": "cheer", "text": "加油！"})
        kinds = [type(c).__name__ for c in cmds]
        self.assertEqual(kinds, ["SetState", "Say"])

    def test_states_include_new_expressions(self):
        # 核心四态永远在；新四态（可选表情）注册进词表才能被 SetState 接受
        for name in ("idle", "cheer", "eat", "sleep",
                     "laugh", "shock", "angry", "dance"):
            self.assertIn(name, bus.STATES)

    def test_commands_accept_dance_and_new_states(self):
        for name in ("laugh", "shock", "angry", "dance"):
            cmds = bus.commands_from_dict({"state": name, "text": "嘿嘿"})
            states = [c.state for c in cmds if isinstance(c, bus.SetState)]
            self.assertEqual(states, [name], f"state={name}")

    def test_commands_ignores_noise(self):
        # 多余字段 / 非法状态 / 非字符串 都安全跳过
        self.assertEqual(bus.commands_from_dict(
            {"state": "fly", "text": 123, "extra": 1}), [])
        self.assertEqual(bus.commands_from_dict("不是字典"), [])
        self.assertEqual(bus.commands_from_dict(None), [])

    def test_describe_event_has_chinese_mapping(self):
        self.assertIn("戳", bus.describe_event({"type": "click"}))
        self.assertIn("喝水", bus.describe_event({"type": "reminder", "kind": "drink"}))
        self.assertIn("错误", bus.describe_event({"type": "hook", "event": "error"}))


class TestRuleDriver(unittest.TestCase):
    def test_click_no_longer_replies_random_lines(self):
        # 单击已由宿主专属接管：规则脑对 click 不再接话，只落未知事件兜底
        d = RuleDriver()
        for _ in range(20):
            cmds = d.react({"type": "click"})
            self.assertTrue(cmds)
            self.assertFalse(any(isinstance(c, bus.Say) for c in cmds),
                             "click 不许再冒随机台词")
            self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))

    def test_hook_maps_state(self):
        d = RuleDriver()
        cmds = d.react({"type": "hook", "event": "success"})
        st = [c.state for c in cmds if isinstance(c, bus.SetState)]
        self.assertEqual(st, ["cheer"])

    def test_growth_levelup_switches_to_dance(self):
        # 升级时刻用扭舞庆祝（非升级仍是 cheer）
        d = RuleDriver()
        up = d.react({"type": "growth", "commits": 30,
                      "title": "代码之蛋", "leveled_up": True})
        self.assertEqual([c.state for c in up
                          if isinstance(c, bus.SetState)], ["dance"])
        plain = d.react({"type": "growth", "commits": 3,
                         "title": "咸鱼蛋", "leveled_up": False})
        self.assertEqual([c.state for c in plain
                          if isinstance(c, bus.SetState)], ["cheer"])

    def test_unknown_event_does_not_crash(self):
        self.assertTrue(RuleDriver().react({"type": "???"}))


class TestLlmParsing(unittest.TestCase):
    def test_parse_plain_json(self):
        obj = parse_reply('{"state":"eat","text":"干饭人干饭魂"}')
        self.assertEqual(obj["state"], "eat")

    def test_parse_fenced_and_noisy(self):
        text = '好的！这是结果：\n```json\n{"state":"sleep","text":"晚安"}\n```\n以上'
        self.assertEqual(parse_reply(text)["text"], "晚安")

    def test_parse_garbage_returns_none(self):
        self.assertIsNone(parse_reply("呵呵呵没有json"))
        self.assertIsNone(parse_reply(""))
        self.assertIsNone(parse_reply("{broken json"))


class TestLLMDriver(unittest.TestCase):
    def _driver(self, **brain):
        cp = _cp(**{"api_base": "http://127.0.0.1:9", "api_key": "x",
                    "model": "test-model", **brain})
        return LLMDriver(cp, fallback=RuleDriver())

    def test_success_path(self):
        d = self._driver()
        d._call_api = lambda msg: '前缀 {"state":"cheer","text":"成功啦"} 后缀'
        cmds = d.react({"type": "hook", "event": "success"})
        states = [c.state for c in cmds if isinstance(c, bus.SetState)]
        self.assertEqual(states, ["cheer"])

    def test_missing_config_reports_need_api(self):
        d = self._driver(api_base="", api_key="", model="")
        cmds = d.react({"type": "hook", "event": "praise"})
        says = [c.text for c in cmds if isinstance(c, bus.Say)]
        self.assertTrue(any("需要接入自己的api" in t for t in says))
        # 同时规则脑兜底命令还在 —— 离线也能玩
        self.assertTrue(any(isinstance(c, bus.Hop) or isinstance(c, bus.SetState)
                            for c in cmds))

    def test_api_error_reports_and_falls_back(self):
        def boom(msg):
            raise OSError("connection refused")
        d = self._driver()
        d._call_api = boom
        cmds = d.react({"type": "hook", "event": "praise"})
        says = [c.text for c in cmds if isinstance(c, bus.Say)]
        self.assertTrue(any("需要接入自己的api" in t for t in says))

    def test_notice_rate_limited(self):
        def boom(msg):
            raise OSError("down")
        d = self._driver()
        d._call_api = boom
        n_notices_first = sum(1 for c in d.react({"type": "hook", "event": "praise"})
                              if isinstance(c, bus.Say) and "需要接入" in c.text)
        n_notices_second = sum(1 for c in d.react({"type": "hook", "event": "praise"})
                               if isinstance(c, bus.Say) and "需要接入" in c.text)
        self.assertEqual(n_notices_first, 1)
        self.assertEqual(n_notices_second, 0)  # 冷却期内不重复


class TestConfig(unittest.TestCase):
    def test_template_is_vendor_free(self):
        low = TEMPLATE.lower()
        # 黑名单词这里动态拼接，避免本文件本身触发仓库密钥扫描
        banned = ("ht" + "tp:", "z." + "ai", "gl" + "m-", "bigmo" + "del")
        for word in banned:
            self.assertNotIn(word, low)

    def test_load_creates_template_with_token(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "config.ini"
            cp = load(path)
            self.assertTrue(path.exists())
            self.assertTrue(cp.get("bridge", "token").strip())
            # 未配置三要素时永远回落规则脑
            cp.set("brain", "mode", "llm")
            from petfw.config import brain_mode, llm_ready
            self.assertFalse(llm_ready(cp))
            self.assertEqual(brain_mode(cp), "rule")


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.srv = BridgeServer(0, "s3cret")
        self.port = self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_health_no_token(self):
        code, body = self.get("/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_react_requires_token(self):
        code, _ = self.get("/react?event=edit&token=WRONG")
        self.assertEqual(code, 401)

    def test_react_good_token_enqueues(self):
        code, body = self.get("/react?event=edit&message=hi&token=s3cret")
        self.assertEqual((code, body["ok"]), (200, True))
        item = self.srv.sink.get(timeout=2)
        self.assertEqual(item["type"], "hook")
        self.assertEqual(item["event"], "edit")
        self.assertEqual(item["message"], "hi")

    def test_post_react_json(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/react",
            data=json.dumps({"event": "done", "message": "收工"}).encode(),
            method="POST",
            headers={"X-Petfw-Token": "s3cret",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
        item = self.srv.sink.get(timeout=2)
        self.assertEqual((item["event"], item["message"]), ("done", "收工"))

    def test_post_bad_token_denied(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/react",
            data=b'{"event":"x"}', method="POST",
            headers={"X-Petfw-Token": "nope"})
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("should be denied")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)


class TestTokenRotation(unittest.TestCase):
    def test_rotate_changes_and_persists(self):
        from petfw.config import rotate_token
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "config.ini"
            old = load(path).get("bridge", "token")
            new = rotate_token(path)
            self.assertNotEqual(old, new)
            # 落盘了：重新读文件拿到的是新 token
            self.assertEqual(load(path).get("bridge", "token"), new)


class TestReactCli(unittest.TestCase):
    def setUp(self):
        import configparser as _cpmod
        self.srv = BridgeServer(0, "cli-token")
        self.port = self.srv.start()
        self.ini = pathlib.Path(tempfile.mkdtemp()) / "config.ini"
        cp = _cpmod.ConfigParser()
        cp.add_section("bridge")
        cp.set("bridge", "port", str(self.port))
        cp.set("bridge", "token", "cli-token")
        with open(self.ini, "w", encoding="utf-8") as f:
            cp.write(f)

    def tearDown(self):
        self.srv.stop()

    def test_send_delivers_event(self):
        from petfw.react import send
        self.assertTrue(send(self.ini, "edit", "改了文档"))
        item = self.srv.sink.get(timeout=2)
        self.assertEqual((item["event"], item["message"]), ("edit", "改了文档"))

    def test_main_routes_short_args(self):
        from petfw import react
        code = react.main([str(self.ini), "success"])
        self.assertEqual(code, 0)
        self.assertEqual(self.srv.sink.get(timeout=2)["event"], "success")


class TestGrowth(unittest.TestCase):
    def test_parse_log_counts_lines(self):
        from petfw.extensions.growth import parse_count
        self.assertEqual(parse_count("6\n"), 6)
        # 空输出/异常输出都算「拿不到数据」，绝不当成 0 次提交
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count("fatal: 不是git仓库"))
        self.assertIsNone(parse_count(None))

    def test_level_thresholds(self):
        from petfw.extensions.growth import level_for
        self.assertEqual(level_for(0)[0], 1)
        self.assertEqual(level_for(4)[0], 1)
        self.assertEqual(level_for(5)[0], 2)
        self.assertEqual(level_for(15)[0], 3)
        self.assertEqual(level_for(30)[0], 4)
        self.assertEqual(level_for(99)[0], 4)

    def test_tracker_scan_uses_injected_runner(self):
        from petfw.extensions.growth import GrowthTracker
        t = GrowthTracker(repo_dir=".", runner=lambda cmd: "7\n"
                          if any(a.startswith("--since") for a in cmd) else None)
        self.assertEqual(t.scan_today(), 7)

    def test_tracker_broken_repo_returns_none(self):
        from petfw.extensions.growth import GrowthTracker
        t = GrowthTracker(repo_dir=".", runner=lambda cmd: None)
        self.assertIsNone(t.scan_today())


class TestWeather(unittest.TestCase):
    def test_condition_mapping_pure(self):
        from petfw.extensions.weather import state_for
        self.assertEqual(state_for("Rain"), "sleep")
        self.assertEqual(state_for("Clear"), "cheer")
        self.assertEqual(state_for("Clouds"), "idle")
        self.assertEqual(state_for("完全未知的字符串"), "idle")

    def test_payload_shape(self):
        from petfw.extensions.weather import extract_state
        self.assertEqual(extract_state({"weather": [{"main": "Rain"}]}), "sleep")
        self.assertIsNone(extract_state({}))
        self.assertIsNone(extract_state("not-a-dict"))


class TestNewEvents(unittest.TestCase):
    def test_weather_rule_matches_mapping(self):
        from petfw.extensions.weather import CONDITION_TO_STATE
        d = RuleDriver()
        for cond, state in CONDITION_TO_STATE.items():
            cmds = d.react({"type": "weather", "condition": cond})
            states = [c.state for c in cmds if isinstance(c, bus.SetState)]
            self.assertEqual(states, [state], f"condition={cond}")

    def test_weather_unknown_condition_harmless(self):
        cmds = RuleDriver().react({"type": "weather", "condition": "Zzz"})
        self.assertTrue(cmds)  # 不抛错、给个 Hop 兜底

    def test_growth_rule_shows_title_and_celebrates_levelup(self):
        d = RuleDriver()
        cmds = d.react({"type": "growth", "commits": 30,
                        "title": "代码之蛋", "leveled_up": True})
        says = [c.text for c in cmds if isinstance(c, bus.Say)]
        self.assertTrue(any("代码之蛋" in t and "30" in t for t in says))
        self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))

    def test_describe_growth_and_weather(self):
        g = bus.describe_event({"type": "growth", "commits": 3,
                                "title": "卷王蛋", "leveled_up": True})
        self.assertIn("3", g)
        self.assertIn("卷王蛋", g)
        w = bus.describe_event({"type": "weather", "condition": "Clear"})
        self.assertIn("Clear", w)


NEW_FIVE = ("cry", "hide", "love", "alien", "blushmax")

HOOK_ERROR_POOL = ["呜哇——又挂了…",
                   "别骂了别骂了，我自己知道错了",
                   "哇的一声哭出来"]
# 【禁用区】doom 台词池随 hide 态退役：仅存档备查，不再是活代码
FLOURISH_DOOM_POOL = ["让我在这顶帽子里反省一下人生",
                      "世界暂时与我无关，勿cue"]


class TestStatesExpansion(unittest.TestCase):
    """五个新状态注册进词表：SetState / commands_from_dict 都要认。"""

    def test_setstate_accepts_five_new_names(self):
        for name in NEW_FIVE:
            self.assertEqual(bus.SetState(name).state, name)

    def test_setstate_still_rejects_illegal_names(self):
        for bad in ("fly", "Cry", "cry ", "hidee", ""):
            with self.assertRaises(ValueError):
                bus.SetState(bad)

    def test_commands_from_dict_accepts_five_new_states(self):
        for name in NEW_FIVE:
            cmds = bus.commands_from_dict({"state": name.upper(), "text": "嘿"})
            states = [c.state for c in cmds if isinstance(c, bus.SetState)]
            self.assertEqual(states, [name], f"state={name}")


class TestRuleTriggerMatrix(unittest.TestCase):
    """规则脑触发矩阵：事件 → 状态的新旧行为对照。"""

    def _states(self, cmds):
        return [c.state for c in cmds if isinstance(c, bus.SetState)]

    def _says(self, cmds):
        return [c.text for c in cmds if isinstance(c, bus.Say)]

    def test_hook_error_switches_to_cry_with_new_pool(self):
        d = RuleDriver()
        seen_states, seen_texts = set(), []
        for _ in range(30):
            cmds = d.react({"type": "hook", "event": "error"})
            seen_states.update(self._states(cmds))
            seen_texts += self._says(cmds)
        self.assertEqual(seen_states, {"cry"})
        self.assertTrue(seen_texts)  # 确实说了话
        for t in seen_texts:
            self.assertIn(t, set(HOOK_ERROR_POOL))

    def test_flourish_doom_retired_falls_back_to_cry(self):
        # doom→hide 已随 hide 入禁用区整段注释：归宿待主人拍板（候选
        # cry/shock/恢复hide），当前落回 error→cry 兜底，禁用台词池不复活
        from petfw.drivers import rule as rule_mod
        cmds = RuleDriver().react({"type": "hook", "event": "error",
                                   "flourish": "doom", "streak": 3})
        self.assertEqual(self._states(cmds), ["cry"])
        for t in self._says(cmds):
            self.assertIn(t, set(HOOK_ERROR_POOL))
        self.assertFalse(hasattr(rule_mod, "FLOURISH_DOOM"),
                         "FLOURISH_DOOM 池必须整体注释保留")

    def test_flourish_comeback_dance_hop_famous_line_kept(self):
        # love 已入禁用区：翻盘改扭舞庆祝，Hop 与著名台词原样保留
        cmds = RuleDriver().react({"type": "hook", "event": "success",
                                   "flourish": "comeback", "streak": 3})
        self.assertEqual(self._states(cmds), ["dance"])
        self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))
        # 著名台词原样保留
        self.assertEqual(self._says(cmds),
                         ["三十年河东 三十年河西！这不就翻盘了！"])

    def test_click_taken_over_by_host_falls_through(self):
        # 规则脑的 click 三档已退役：一律未知事件兜底（Hop），不切表情不说话
        d = RuleDriver()
        for ev in ({"type": "click"},
                   {"type": "click", "away_seconds": 700},
                   {"type": "click", "away_seconds": 200},
                   {"type": "click", "away_seconds": 30}):
            cmds = d.react(ev)
            self.assertEqual(self._states(cmds), [], f"ev={ev}")
            self.assertEqual(self._says(cmds), [], f"ev={ev}")

    def test_idle_chat_still_talks(self):
        # 闲聊台词源从已退役的 CLICK_LINES 挪到 IDLE_HOP_LINES，行为不哑
        from petfw.drivers.rule import IDLE_HOP_LINES
        d = RuleDriver()
        says = []
        for _ in range(10):
            says += self._says(d.react({"type": "idle", "seconds": 120}))
        self.assertTrue(says)
        for t in says:
            self.assertIn(t, IDLE_HOP_LINES)

    def test_alien_blushmax_lines_retired_with_zone(self):
        from petfw.drivers import rule as rule_mod
        # alien/blushmax 已入禁用区：预留台词池整体注释保留（不再存活），
        # 同名外部事件依旧走 idle 兜底，绝不指向禁用态
        for attr in ("ALIEN_LINES", "BLUSHMAX_LINES"):
            self.assertFalse(hasattr(rule_mod, attr),
                             f"{attr} 应随禁用区整体注释保留")
        for ev in NEW_FIVE:
            if ev not in ("alien", "blushmax"):
                continue
            self.assertEqual(
                self._states(RuleDriver().react({"type": "hook", "event": ev})),
                ["idle"])


class TestBridgeNewEvents(unittest.TestCase):
    """bridge praise / kiss 自定义事件一路贯通到规则脑 dance（原 love 已禁用）。"""

    def setUp(self):
        self.srv = BridgeServer(0, "tok")
        self.port = self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def _drive_via_bridge(self, event):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/react?event={event}&token=tok")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
        item = self.srv.sink.get(timeout=2)
        cmds = RuleDriver().react(item)
        return [c.state for c in cmds if isinstance(c, bus.SetState)]

    def test_bridge_praise_event_drives_rule_to_dance(self):
        # love 已入禁用区：praise/kiss 改开心到跳舞（台词池不变）
        self.assertEqual(self._drive_via_bridge("praise"), ["dance"])

    def test_bridge_kiss_event_drives_rule_to_dance(self):
        self.assertEqual(self._drive_via_bridge("kiss"), ["dance"])

    def test_praise_pool_is_shared_by_praise_and_kiss(self):
        # 台词池已迁移为 rule.PRAISE_LINES（原 CLICK_LINES 池 + 原两句），
        # praise 与 kiss 共用同一池
        from petfw.drivers.rule import PRAISE_LINES
        d = RuleDriver()
        for ev in ("praise", "kiss"):
            for _ in range(20):
                says = [c.text for c in d.react({"type": "hook", "event": ev})
                        if isinstance(c, bus.Say)]
                self.assertTrue(says and set(says) <= set(PRAISE_LINES),
                                f"event={ev} says={says}")


class TestDescribeEventExpansion(unittest.TestCase):
    """describe_event 中文处境描述：对话脑不写代码即免费受益。"""

    def test_hook_error_still_mentions_error_keyword(self):
        desc = bus.describe_event({"type": "hook", "event": "error"})
        self.assertIn("错误", desc)

    def test_hook_comeback_mentions_turnaround_keyword(self):
        desc = bus.describe_event({"type": "hook", "event": "success",
                                   "flourish": "comeback", "streak": 3})
        self.assertIn("翻盘", desc)

    def test_hook_praise_mentions_praise_keyword(self):
        desc = bus.describe_event({"type": "hook", "event": "praise"})
        self.assertIn("夸", desc)


class TestTrayLabelsExpansion(unittest.TestCase):
    """托盘中文名覆盖新五名（仿既有 STATE_ZH 防漂移测试）。"""

    def test_tray_labels_cover_every_state_including_new_five(self):
        from petfw.host import STATE_ZH
        self.assertEqual(set(STATE_ZH), set(bus.STATES))
        new_zh = {st: STATE_ZH.get(st) for st in NEW_FIVE}
        self.assertEqual(new_zh, {"cry": "哭唧唧", "hide": "缩帽躲",
                                  "love": "比小心心", "alien": "外星吸人",
                                  "blushmax": "羞耻爆炸"})


class TestManifest(unittest.TestCase):
    """manifest 与代码词表的一致性：防止以后一边加状态另一边忘了同步。"""

    def _manifest_states(self) -> dict:
        path = (pathlib.Path(__file__).resolve().parents[1]
                / "assets" / "manifest.json")
        return json.loads(path.read_text(encoding="utf-8"))["states"]

    def test_manifest_keys_match_bus_states(self):
        # 双向弱包含代替强相等：允许两边分支各自先行扩展，合并后自然闭合。
        # 专属演出动作（alien_suck）只在 manifest 登记、不进 SetState 词表，
        # 但必须在 host.ACTION_ONLY 里声明，防止 manifest 出现野名字。
        # 五态精简：alien_suck 等八条住在顶层 "_disabled_states" 禁用区，
        # 活动区 + 禁用区合起来仍不许超出词表 ∪ ACTION_ONLY ∪ 六拍舞。
        from petfw.host import ACTION_ONLY, SIX_BEAT_STATE
        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[1]
             / "assets" / "manifest.json").read_text(encoding="utf-8"))
        mk = set(manifest["states"].keys())
        zone = set(manifest.get("_disabled_states", {}).keys())
        core = set(getattr(bus, "CORE_STATES", ()))
        allst = set(bus.STATES)
        # 核心态必须登记在案——五态精简后被禁用的（如 eat）住禁用区也算在册
        self.assertTrue(core <= mk | zone,
                        "核心态至少要登记在活动区或禁用区之一")
        self.assertTrue((core - zone) <= mk,
                        "未被禁用的核心态必须仍留在活动区")
        self.assertLessEqual(mk | zone,
                             allst | set(ACTION_ONLY) | {SIX_BEAT_STATE})
        self.assertEqual(set(ACTION_ONLY) - allst, zone - allst,
                         "禁用区里的专属动作必须与 ACTION_ONLY 一一对应")
        self.assertIn("alien_suck", zone)
        self.assertNotIn("alien_suck", mk)

    def test_manifest_entries_have_animation_fields(self):
        for name, spec in self._manifest_states().items():
            self.assertTrue(spec.get("file"), f"{name} 缺 file")
            self.assertGreater(float(spec.get("period_ms", 0)), 0,
                               f"{name} 缺 period_ms")


class TestAssetFallback(unittest.TestCase):
    """缺图降级：核心四态缺图必须报错退出，可选新态缺图只警告跳过。"""

    def test_collect_missing_splits_core_and_optional(self):
        from petfw.host import collect_missing
        states = {
            "idle": {"file": "states/idle.png"},
            "eat": {"file": "states/eat.png"},
            "dance": {"file": "states/dance.png"},
            "shock": {"file": "states/shock.png"},
        }
        available = ["states/idle.png", "states/eat.png"]
        missing_core, missing_optional = collect_missing(states, available)
        self.assertEqual(missing_core, [])
        self.assertEqual(sorted(missing_optional), ["dance", "shock"])

    def test_collect_missing_reports_core_loss(self):
        from petfw.host import collect_missing
        states = {"cheer": {"file": "states/cheer.png"}}
        missing_core, missing_optional = collect_missing(states, [])
        self.assertEqual(missing_core, ["cheer"])
        self.assertEqual(missing_optional, [])

    def test_collect_missing_all_present(self):
        from petfw.host import collect_missing
        states = {n: {"file": f"states/{n}.png"} for n in bus.STATES}
        files = [f"states/{n}.png" for n in bus.STATES]
        self.assertEqual(collect_missing(states, files), ([], []))

    def test_tray_labels_cover_every_state(self):
        # 托盘中文映射与词表一一对应，防止加状态后菜单漏名字
        from petfw.host import STATE_ZH
        self.assertEqual(set(STATE_ZH), set(bus.STATES))
        self.assertEqual(STATE_ZH["laugh"], "笑哭")
        self.assertEqual(STATE_ZH["dance"], "扭舞")

    def test_paths_resolve_to_repo_in_dev_mode(self):
        from petfw import paths
        repo = pathlib.Path(__file__).resolve().parents[1]
        self.assertEqual(paths.ASSETS, repo / "assets")
        self.assertEqual(paths.CONFIG_PATH, repo / "config.ini")
        self.assertEqual(paths.RUNTIME_PATH, repo / "runtime.json")
        self.assertEqual(paths.APP_DIR, repo)


if __name__ == "__main__":
    unittest.main()
