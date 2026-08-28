# 团子 接口文档

> 红线：本文档不包含任何真实 API 地址、密钥、模型名。厂商相关信息只保存在本地
> 未入库的文件中（见 `.gitignore` 的 `docs_local/` 与 `*.local.md` 规则）。

## 0. 架构一图流

```
事件源                          大脑(Driver)                渲染内核(host)
─────────                      ─────────────               ─────────────
鼠标点击 ┐                  ┌─ RuleDriver(本地规则)    ┌→ SetState 切表情
健康提醒 ├→ dispatch(ev) → │                          ├→ Say      冒泡
闲聊时机 │  (纯 dict)      └─ LLMDriver(大模型对话)     └→ Hop      蹦跶
hook桥接 ┘                     失败自动降级↑
```

约定三条：

1. **大模型只用来对话**：模型只产出「台词文本 + 表情选择」，动画、提醒调度、
   hook 摄入、托盘等一切功能 100% 本地实现。离线 / 没有 key 时桌宠完整可玩。
2. **说话失败要明示**：LLM 驱动失败时报「需要接入自己的api」（90 秒限频），
   同时规则脑无缝接管，绝不哑掉、绝不崩窗。
3. **内核只认命令**：`petfw/host.py` 不做业务决策，只执行 `bus.py` 定义的命令；
   任何新玩法 = 写一个新 Driver 或新事件源，内核不改。

## 1. 配置文件 `config.ini`（本地，不入库）

首次运行自动生成。全部字段：

| 段 | 键 | 说明 |
|---|---|---|
| pet | name | 宠物名字，显示在气泡/托盘 |
| pet | display_size | 显示尺寸上限(px)，默认 128 |
| brain | mode | `rule` / `llm`；三要素不全时自动回落 `rule` |
| brain | api_base | OpenAI 兼容网关地址(**填什么看 docs_local/**) |
| brain | api_key | 密钥，只存本地 |
| brain | model | 模型名，只存本地 |
| bridge | enabled | 是否开本地桥接(hook 联动入口) |
| bridge | port | 默认 8321，占用时本次启动自动禁用并提示 |
| bridge | token | **每次启动轮换**；外部调用统一走 `python -m petfw.react` |
| reminders | enabled / interval_minutes | 健康提醒开关与间隔 |
| settlement | enabled | 全屏结算画面总开关（hook done / 每日定时共用） |
| settlement | daily_time | 每日自动播报时刻 HH:MM，默认 18:00；已过点自动排明天 |
| settlement | bgm | 结算画面 BGM 总开关，默认 true |
| settlement | bgm_rate | BGM 变速倍率，默认 2.5；合法区间 0.5~4.0，越界/非法自动回落 1.0 原速 |
| sound | enabled / volume | 互动音效开关与音量(0~1)：运行期程序合成 WAV 到系统临时目录，零素材文件，无声环境自动静默降级 |

路径说明：开发态 `config.ini` / `runtime.json` 固定在仓库根；用 PyInstaller
打包的 exe（frozen 模式）下二者生成在 **exe 同目录**（`Path(sys.executable).parent`），
只读素材则从解包临时目录读取——解析统一收敛在 `petfw/paths.py`。

## 2. 事件协议（进程内）

事件是普通 dict，经 `PetWindow.dispatch(ev)` 进入大脑：

| type | 附加字段 | 触发者 |
|---|---|---|
| click | away_seconds=距上次交互秒数 | 左键单击宠物（非拖拽）；带上它可获得「别走别走」回归彩蛋 |
| reminder | kind=`drink`/`stretch` | 健康提醒定时器 |
| idle | seconds=安静秒数 | 无操作 ≥100s 后的随机闲聊 |
| hook | event, message?, flourish?, streak? | 外部程序经 HTTP 桥送入；宿主在 `_on_hook` 统一合并 BuildStreak 的连败/翻盘判定（doom=连败3起、comeback=连败≥2后取胜） |
| growth | commits, level, title, leveled_up | Git 成长扫描（托盘「今日战报」、hook `done` 或每日定时触发，正常路径改为弹全屏结算画面） |
| weather | condition | 天气心情灯（本地演示菜单或你自己的抓取脚本） |

`describe_event()` 会把附加字段翻译成给大模型的处境描述：click 带
`away_seconds` 时变成「小主人回来了，离开了大约 X 分钟/几秒」；hook 带
`flourish` 时先交代连败/翻盘再接原事件——对话脑不写一行代码即免费受益。

## 3. 命令协议（进程内）

Driver 返回命令列表，宿主逐条执行：

```python
SetState(state)   # state ∈ bus.STATES（见下表），非法值直接抛错
Say(text, seconds=6)  # 冒泡台词，超宽自动折行，最长截断 80 字符
Hop(times=1)      # 0.7 秒弹跳加成
```

STATES 状态词表（前四名为核心态缺图即退出，其余为可选态缺图自动跳过，
可选态允许词表先注册、图片后补到位）：

| state | 中文名 | 使用场景 |
|---|---|---|
| idle | 发呆 | 核心态。默认待机、安静陪伴、无话可说时 |
| cheer | 打气 | 核心态。开工、成功、收工等庆祝时刻 |
| eat | 干饭 | 核心态。喝水提醒、"改代码像干饭"的场景 |
| sleep | 犯困 | 核心态。伸懒腰提醒、出错躺平、雨雪天气 |
| laugh | 笑哭 | 可选新态。点击逗趣、轻松玩笑的场合 |
| shock | 惊讶 | 可选新态。突发事件、出乎意料的通知（几乎静止微颤） |
| angry | 生气 | 可选新态。连续报错、"炸毛"式吐槽 |
| dance | 扭舞 | 可选新态。Git 称号升级等高光庆祝时刻 |
| cry | 哭唧唧 | 可选新态。hook error 报错时当场哭一场 |
| hide | 缩帽躲 | 可选新态。编译连败 3 起（doom）躲进帽子反省人生 |
| love | 比小心心 | 可选新态。翻盘成功、外部 praise/kiss 被夸被亲 |
| alien | 外星吸人 | 可选新态。暂无自动触发，托盘可手选，台词池预留待未来钩子 |
| blushmax | 羞耻爆炸 | 可选新态。暂无自动触发，托盘可手选，台词池预留待未来钩子 |

静态立绘之外，`petfw/cutout_anim.py::RECIPES` 内置八套剪纸动画配方（laugh/cry/shock/eat/sleep/idle/cheer/angry 占位），本地可用 `python tools/local/gifgen.py --state <名> --preview` 合成样片到 assets/raw/drafts（本地目录，不入库），验收后 `--install` 覆盖母带并重跑 `prep_assets.py`。

## 4. Driver 插件接口

```python
from petfw.drivers.base import Driver

class MyDriver(Driver):
    name = "my"
    def react(self, event: dict) -> list:
        # 必须纯同步、不许抛异常（内部自行 try/except）
        return [bus.SetState("cheer"), bus.Say("你好呀")]
```

在 `petfw/drivers/__init__.py::get_driver` 注册后即可被托盘「大脑」菜单切换。
LLMDriver 已内置：回复解析容错(```json 围栏/废话包裹均可)、失败降级规则脑、
失败提示限频。测试参照 `tests/test_core.py::TestLLMDriver`（stub `_call_api`）。

## 5. 本地 HTTP 桥（外部接入唯一入口）

- 只监听 `127.0.0.1:<bridge.port>`，token 鉴权（header 或 query 均可）
- CORS 不开放
- **token 每次启动轮换**——推荐统一使用自带客户端 `python -m petfw.react`
  （约 40 行源码在 `petfw/react.py`，它每次现读 ini 里的端口与 token）：

```bash
python -m petfw.react edit            # 最简
python -m petfw.react done 收工啦     # 带备注
```

需要 HTTP 直连时的协议如下：

### GET /health
无需 token。返回 `{"ok":true,"pet":"petfw"}`，用于探活。

### GET /react
```
GET /react?event=<event>&message=<可选备注>&token=<config.ini里的token>
```
curl 示例（占位符请替换成你自己的端口/token）：
```bash
curl "http://127.0.0.1:8321/react?event=success&token=<token>"
```

### POST /react
```bash
curl -X POST "http://127.0.0.1:8321/react" \
  -H "Content-Type: application/json" \
  -H "X-Petfw-Token: <token>" \
  -d '{"event":"edit","message":"改了接口文档"}'
```

错误码：401 token 错误；400 JSON 解析失败；404 路径不存在。
`event`/`message` 截断上限 40/200 字符；未知 event 由大脑自主兜底，不会报错。

## 6. ZCode / coding-agent hook 接入

推荐姿势：把仓库克隆路径设为环境变量 `PETFW_HOME`，hook 命令一行搞定，
且永远不用关心 token 轮换：

```bat
cmd /c "cd /d %PETFW_HOME% && python -m petfw.react edit"
```

在客户端 settings 里的挂载示例（PostToolUse 改代码时触发）：

```json
{
  "hooks": {
    "PostToolUse": [{
      "command": "cmd /c \"cd /d %PETFW_HOME% && python -m petfw.react edit\""
    }]
  }
}
```

推荐 event 词表（RuleDriver 内置了对应表情）：

| event | 表情反应 |
|---|---|
| start | cheer「开工！我给你举花球！」 |
| edit | eat「让我看看改了啥好吃的」 |
| test | idle「测试跑着呢，我盯着呢」 |
| success | cheer「搞定！夸我夸我！」 |
| error | cry「呜哇——又挂了…」（连败 3 起 → hide「让我在这顶帽子里反省一下人生」） |
| praise / kiss | love「嘿嘿…被夸得好开心嘛」（外部程序可发的自定义事件，两者同池台词） |
| done | 触发全屏结算画面：扫当日提交数弹走马灯战报（有本地 BGM 则按 `bgm_rate` 变速循环播放）；结算期间本体切扭舞持续蹦跶，关窗自动恢复打开前的表情 |

连败/翻盘由宿主 `PetWindow._on_hook` 里的 BuildStreak 判定后以
`flourish`/`streak` 字段并入事件；连续 error 计数穿插 edit/test 等其它
信号不清账，success 终结连败（≥2）时报 comeback，翻盘时刻切 love 并附
带一段 Hop 蹦跶。

## 7. 角色/素材清单格式 `assets/manifest.json`

数据驱动，加状态无需改渲染代码。每个状态支持两种 schema（可并存迁移）：

**单图模式 v1**（照旧兼容）：

```json
{
  "pet": "my-pet",
  "states": {
    "idle": { "file": "states/idle.png", "bob_amp": 3, "period_ms": 2600, "tilt_deg": 0 }
  }
}
```

**多帧模式 v2**（`prep_assets.py` 检测到 `assets/raw/<状态名>.gif` 时自动生成）：

```json
"dance": {
  "file": "states/dance.png",
  "frames": ["states/dance_f0.png", "states/dance_f1.png", "states/dance_f2.png"],
  "frame_ms": 120,
  "bob_amp": 9, "period_ms": 450, "tilt_deg": 10
}
```

**动作字段 v3**（可选键，向后兼容；全帧视频切片 / 点播系统使用）：

| 字段 | 含义 |
|---|---|
| file | 相对 assets/ 的路径（单图模式必填；多帧模式可保留作静图兜底） |
| frames | 多帧模式的帧序列，相对 assets/ 的 PNG 路径列表 |
| frame_ms | 帧时长的基准毫秒数（取自 GIF duration 中位数或 int(1000/fps_est)，上限 60ms） |
| play | `once` 完整播放一轮就谢幕 / `loop` 循环；缺省 `loop`（老条目零改动兼容） |
| return_to | 谢幕后的建议去向，缺省 `idle`（宿主实际优先回到表演前来路） |
| bob_amp | 上下浮动幅度 px |
| period_ms | 浮动周期，越小越欢快 |
| tilt_deg | 摆动最大倾角 |

帧序列有两种命名：GIF 抽稀档输出 `<状态>_f{i}.png`（旧 6 帧）；源视频
全帧档输出 `<状态>_F{index:03d}.png`（大写 F，上限 240 帧）。同名多源时
**全帧视频优先**——`assets/raw/<状态名>.mp4` 存在就走
`extract_full_frames()`，解不出帧自动回落 GIF 抽稀。全帧切片内置相邻去重
（平均逐像素差 <2.0 视为静止跳过）与 fps_cap 抽稀，母带重建见本地脚本
`tools/local/rebuild_frames_from_videos.py`（不入库）。

多帧的播放节奏由 **ActionPlayer**（`petfw/action_player.py`）统一裁决：
- **安静待机不轮播**：多帧表情平时静立首帧只做呼吸浮动，杜绝旧的 80ms
  "定格闪跳"；换帧只发生在明确点播时；
- **点播完整播放**：`PetWindow.play_action(name)` 按 frame_ms 全帧率逐帧
  推进，`play=once` 播完一轮谢幕自动回表演前来路，`loop` 永续循环；
- celebrate 档（hop 生效期或结算画面开着）仍会让呼吸/摆动提速加幅，
  但表演期的换帧节奏恒为 frame_ms，不再分档变速。

注意：新增状态名需同步加入 `petfw/bus.py::STATES` 才能被 SetState 接受。
manifest 与词表的防漂移单测只约束两条：核心四态必须已登记、登记的名字
不许超出 `bus.STATES`——可选新态允许先注册名字、图片后补到位。核心四态
（idle/cheer/eat/sleep）缺图启动直接退出；可选新态缺图只警告跳过。多帧条目按
整组 frames 全部到位才算不缺。
`prep_assets.py` 把 `assets/raw/*.png` 泛洪抠图后生成 `assets/states/*.png`
（raw 目录只留本地不入库）；检测到 `*.gif` 则走抽稀管线——均匀采样 ≤6 帧
（含首尾帧）、逐帧同容差抠图、所有帧裁到透明区联合包围盒防抖动，输出
`<状态>_f{i}.png` 并把 frames/frame_ms json 读改写合并进 manifest。

## 8. 动作菜单（本体右键 = 托盘，共用一份清单）

**右键点击桌宠本体**弹出中文动作点播菜单，托盘图标菜单同样由它填充：
两者都走 `PetWindow.build_actions_menu(menu, window)` 这一个构建器，
一处维护永不漂移。三段分组，词条只列已加载出图的状态：

| 分组 | 词条（中文名直呼其字） | 行为 |
|---|---|---|
| 情绪 | 发呆 打气 干饭 睡觉 笑哭 惊讶 [生气] 扭舞 | `play_action(name)`：完整播放一次演完回家（生气缺素材时整个词条隐藏） |
| 整活 | 哭唧唧 缩帽躲 比小心心 外星吸人 羞耻爆炸 | 同上 |
| 系统 | 今日战报 / 天气演示(晴·多云·雨·雪子菜单) / 模拟hook(edit) / 健康提醒(开关) / 退出 | 复用宿主既有槽方法 |

表演期间收到的 SetState 不会打断演出：请求进候补位排队（后来的覆盖
先来的），谢幕后再应用（`host.defer_if_playing` 纯函数裁决）。

## 9. 测试与验证

```bash
python -m unittest discover -s tests -t .   # 核心测试，无 GUI 无网络
QT_QPA_PLATFORM=offscreen python run.py --smoke   # 冒烟启动（offscreen 可选）
python prep_assets.py                         # 重新处理素材（PNG 静图 + 视频全帧/GIF 抽帧）
python tools/check.py                         # 发布门禁（测试+版权图+厂商字样）
```
