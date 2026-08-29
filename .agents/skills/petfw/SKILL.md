---
name: petfw-dev
description: 开发「团子」本地桌宠项目的专属技能。涉及 petfw 仓库的构建、测试、加表情状态、写扩展、hook 联动或提交推送时使用；内含本项目的硬性门禁与红线。
---

# 团子开发技能

## 项目一句话

PySide6 桌宠：渲染内核只认命令（SetState/Say/Hop），业务全在可插拔 Driver 与
本地扩展里；大模型仅负责对话，其余功能离线可用。

## 必跑命令

```bash
python -m unittest discover -s tests -t .   # 全部测试（无 GUI、无网络）
QT_QPA_PLATFORM=offscreen python run.py --smoke   # 冒烟启动验证
python tools/check.py                       # 发布门禁（测试+版权图+厂商字样扫描）
python prep_assets.py                       # 抠图管线：PNG 静图 + GIF 抽帧拆片
```

## 硬性红线（提交前逐条自检）

1. 角色表情包图片**永不入库**：不得跟踪 assets/raw/、assets/states/ 下任何图片。
2. config.ini、runtime.json 永不入库（含 key/model/token）。
3. 仓库内禁止出现真实 API 地址、密钥、模型名等厂商字样（check.py 会拦）。
4. bridge token 每次启动轮换——外部接入示例一律演示
   `python -m petfw.react <event>`，别写死 token。
5. 提交遵循 TDD：先补测试再实现；commit 前必过 `tools/check.py`。

## 架构速记

```
事件(dict) -> dispatch -> Driver.react -> [SetState|Say|Hop] -> apply 渲染
Driver 两实现：rule(离线兜底)/llm(对话脑,失败降级并提示配置 api)
扩展一律走 bus 事件(growth/weather)，不在 host 里做业务判断
```

音效零素材：`petfw/sound_core.py` 运行期用标准库合成八种短音效（WAV 落系统临时目录），
宿主 `PetWindow.play()` 经 QSoundEffect 播放并全程静默降级，改声音手感只动 sound_core。
左键单击/双击由宿主专属接管（判定纯逻辑在 `petfw/click_flow.py`，280ms 可注入时钟窗口；
点歌决策在 `petfw/song_flow.py`、整首播放薄封装在 `petfw/music_player.py`）：
单击=点歌整首 `[sound] music_file`（默认 assets/local/bgm.mp3）+ dance 循环伴舞到歌完
自动回发呆，歌播着时单击/双击一律忽略，mp3 缺失或后端坏回落「不要戳我！！！！」气泡 +
click.wav + shock 尾部定格 1.2 秒再经转场帧回 idle；双击=点歌开跳（click.wav 原声 +
dance 扭舞一段）；结算画面打开期间点击一律忽略、不触发任何演出（结算开屏会先停点歌 BGM）。
右键菜单「情绪」组末尾的「六拍舞」词条走 `play_six_beat()`：`dance6` 程序剪纸六拍舞常驻循环
（play=loop，once 的谢幕逻辑不适用）+ 抽好的跳舞结算音轨只放一遍，配乐放完舞照跳到用户点别的。
（五态精简，主人拍板 2026-08：hide/alien_suck 等八条已入 manifest 顶层
`_disabled_states` 禁用区——loader 只读 `states`、显式忽略下划线顶层键，
条目搬回即恢复；旧演出代码一律注释保留不物理删除。）

## 常见改动配方

### 加一个新表情状态

1. 放原图 `assets/raw/<名>.png`（或 `<名>.gif`，本地）→ 跑 `prep_assets.py`
2. `assets/manifest.json` 登记：单图用 file/bob_amp/period_ms/tilt_deg；
   GIF 会被自动抽稀成多帧条目（frames + frame_ms）合并进 manifest
3. `petfw/bus.py::STATES` 加名字（否则 SetState 抛错）
4. `petfw/drivers/rule.py` 加台词；`host.py::STATE_ZH` 加中文标签
5. 补一条 rule 驱动相关测试
6. （可选）要动效版：跑 `python tools/local/gifgen.py --state <名> --preview`
   用 `petfw/cutout_anim.py::RECIPES` 程序合成剪纸样片到
   assets/raw/drafts，肉眼验收后再 `--install` 覆盖母带并重跑 prep_assets

**缺图降级规则（核心四态 vs 可选新态）**：
- 核心四态 = idle/cheer/eat/sleep（`bus.CORE_STATES`）：任何一张缺图或损坏，
  `load_states` 直接 `SystemExit` 报错退出——没有它们就撑不起角色形象
  （五态精简后被禁用的核心态如 eat 住在禁用区，不参与加载、不触发此规则）；
- 可选新态（如 laugh/shock/angry/dance）：manifest 登记了但没图时，启动打印
  警告并跳过该状态；托盘菜单不出现该项，事件点到也只是维持当前表情不崩窗；
  用户补图重跑 `prep_assets.py` 即解锁，无需改代码；
- 两边不许漂移：`assets/manifest.json` 登记的名字（活动区+禁用区合计）
  不许超出 `bus.STATES`，且核心态必须登记在册（活动区或禁用区皆算，
  未禁用的核心态必须留活动区，`tests/test_core.py::TestManifest` 强制）；
  可选新态允许先注册进 `bus.STATES`、图片后补到位再跑 prep_assets.py 解锁。

### 禁用/恢复一个表情状态（五态精简配方）

- **禁用**：把该条目从 manifest 的 `states` 整体搬到顶层
  `_disabled_states`（下划线开头）——菜单自动消失、触发自动失效；
  同步把 rule.py 指向它的映射/台词池按「注释保留哲学」注释掉，
  就近改绑到五件套（idle/sleep/dance/shock/cry）。
- **恢复**：把条目搬回 `states`、解开注释即可，数据全程不删。
- loader 只认 `states`：`host.active_states()` 显式忽略一切下划线开头的
  顶层键，`tests/test_five_states.py` 锁死该机制（禁用区断言：在
  `_disabled_states` 里且 `states` 里没有）。

### 逐帧动画与动作点播（animator_core + ActionPlayer）

- **纯逻辑核心在 `petfw/animator_core.py`**：sample_frames 均匀抽稀 ≤6 帧、
  schedule/next_index 双档节拍、validate_rate 变速校验——全部无 Qt 可直测。
- **点播核心在 `petfw/action_player.py::ActionPlayer`**：`start(spec,
  on_finish_state)` 装填、`tick(dt)->帧下标|None` 推进；once 走 v4
  **显式三段拼接时间线**——`segments` 段列表（perform 表演一轮 → hold
  定格 `hold_seconds` 秒停末帧 → transition 转场帧一轮），`tick` 每拍
  `elapsed_seconds += dt` 记秒数、用 if 判断当前段，三段走完返回 None
  谢幕回 `return_to`；loop 永续循环（无三段概念）。表现层只消费它的结论
  （`segment` 属性告诉宿主亮 frames 还是 transition_pics 列表）。
- **宿主秒表保险丝（第二道闸）**：`play_action` 记 `time.monotonic()` 与
  manifest `max_seconds`，`_tick` 每拍过 `host.action_overtime()` 纯函数，
  超时直接谢幕回发呆——独立于 ActionPlayer 内部计时，防任何原因卡死。
- **manifest 四代 schema 并存**：v1 `"file"` 单图照旧；v2 `"frames"`+
  `"frame_ms"` 多帧（GIF 抽稀 `_f{i}`）；v3 动作字段 `play`(once/loop,
  缺省 loop 向后兼容)+`return_to`(缺省 idle)；**v4 显式三段**——
  `frames` 只放表演帧、`transition_frames` 独立转场帧（`_Q` 压扁回弹
  序列）、`hold_seconds` 定格秒数（shock/cry 1.2、dance 0.0）、
  `max_seconds`=表演+定格+转场+1 秒宽限。全帧视频档输出
  `<状态>_F{index:03d}.png`（prep_assets 同名视频优先于 GIF）。
- **安静待机不轮播**：多帧表情平时静立首帧只呼吸浮动（idle bob_amp=2），
  换帧只在 `PetWindow.play_action(name)` 点播时发生——治"定格闪跳"。
- **丝滑档烘焙与乒乓**：`prep_assets.bake_all_smooth()` 把 ≤8 帧的骨折档
  逐对相邻帧 blend 插帧并融回 idle 收招（`<状态>_S{idx:03d}.png`，eat/sleep
  走 140ms 慢速档），manifest 补 `"pingpong": true`——loop 档 ActionPlayer
  往返走帧，once 档照旧播到尾即谢幕；闲置 90 秒且无气泡时宿主经
  `petfw/idle_policy.should_auto_sleep` 自动悄然入睡（不发台词，交互自然唤醒）。
- **SetState 让路**：表演中的切表情请求经 `host.defer_if_playing` 进候补位
  （最后请求赢），谢幕后再应用。
- **菜单单一来源**：本体右键(contextMenuEvent)与托盘共用
  `PetWindow.build_actions_menu(menu, window)`：情绪/整活/系统三段分组，
  词条只列已加载出图的状态，缺图自动隐藏。改菜单只改这一处。
  五态精简后「天气演示」「模拟hook(edit)」经主人拍板暂时下线
  （构建器里整段注释保留，weather 扩展与桥接通路不动）。
- **转场拼接段（once 收招不再硬切）**：主路径是
  `prep_assets.bake_squash_return()` 压扁回弹转场——表演末帧与 idle 平滑压到
  (sy 0.78, sx 1.18)（锚点=底部中心），恰在最大压扁帧换装到 idle 同比例压扁
  帧（形状连续无叠影），再 ease_out_back 式经 1.12 过冲回弹落定；输出
  `<状态>_Q{idx:03d}.png`（33ms→30 帧、41ms→24 帧，仪式约 1 秒）。v4 起
  转场帧独立写进 `transition_frames` 字段（frames 不再追加转场帧），并
  折算 `hold_seconds`/`max_seconds` 写回 manifest；幂等重跑先清 `_T`/`_Q`
  两代旧帧；旧渐变方案 `extend_return_transition()`（12 张
  `_T` 帧）保留供回滚。cheer 单图经 `bake_cheer_party()` 变 45 帧常驻搞笑
  循环（play=loop，挥旗+猛压+粗转+星星爆开连招）。一键重烤：`python prep_assets.py --rebuild`
  （压扁转场 + cheer 派对 + sleep 提速 90ms，确定性幂等）。
- **结算画面联动保留但走新引擎**：结算 opened 改调
  `play_action("dance", play="loop")` 循环扭舞到关窗，closed 直接叫停
  播放器并恢复打开前的表情。
- **母带重烧**（本地工具不入库）：mp4 母带放 `assets/local/gen_videos/`
  跑 `python tools/local/rebuild_frames_from_videos.py` 全帧切片并自动合并
  manifest（frame_ms=int(1000/fps_est) 上限 60、play=once）。

### 结算画面配方（全屏走马灯战报）

1. **core 纯函数与表现层严格分离**：文案行由 `petfw/settlement_core.py::
   build_settlement_lines()` 生成（无 Qt、可注入垃圾值不炸）；Qt 侧
   `settlement_window.py` 只消费字符串，禁止在 GUI 类里做业务计算。
2. **每日定时 = stdlib datetime + 单发 QTimer 自续**：`next_delay_ms()`
   算「现在距最近一个 daily_time 的毫秒差」（已过点排明天），单发 QTimer
   触发后在 timeout 里再算下一次，天然跨天续期；不用 interval 周期定时器。
3. **BGM 静默降级原则**：音频放 `.gitignore` 的 `assets/local/`
   （永不入库、也绝不进 exe datas 列表）；`find_bgm()` 只做存在性探测
   （bgm.mp3 优先、bgm.m4a 兜底）；QMediaPlayer/QAudioOutput 连 import
   一起包 try——缺文件、缺多媒体后端一律无声继续，画面永不陪葬。
   变速倍率 `[settlement] bgm_rate`（默认 2.5，限 0.5~4.0）经
   `validate_rate` 洗过后 setPlaybackRate，单独包 try 不支持就原速播；
   `bgm = false` 可整个关掉 BGM。
4. 表现层失败时宿主要能退回旧气泡战报（见 `host.py::_open_settlement`
   返回 False 的兜底分支）。

### 打包成 exe（私发版）

```bash
python prep_assets.py        # 先有 assets/states/*.png 成品图
pip install pyinstaller      # 仅打包机需要
tools/build_exe.bat          # == python -m PyInstaller tools/petfw.spec
QT_QPA_PLATFORM=offscreen dist/CustomPetFramework.exe --smoke   # 冒烟验证
```

spec 要点（tools/petfw.spec）：

- **paths 解析**：运行期路径统一走 `petfw/paths.py`——frozen(onefile) 下
  只读素材从 `sys._MEIPASS/assets` 读，可写的 config.ini/runtime.json 落在
  exe 同目录（`Path(sys.executable).parent`）；开发态全是仓库根。
- **add-data**：只打包 `assets/manifest.json` 与 `assets/states/*.png`，
  绝不带 `assets/raw/` 原图（版权红线）；入口是薄壳 `tools/petfw_launcher.py`。
- **产物不入库**：build/ 与 dist/ 已 .gitignore，入库的只有 spec 配置本身；
  分发对象须遵守 LICENSE（非商用、保留署名）。

### 加一个新事件源/玩法

1. 在 bus.py 定事件字段 + describe_event 文案
2. 规则脑处理该 type（必须无条件不炸）；LLM 脑免费获得（靠 describe_event）
3. `petfw/extensions/<名>.py` 写纯逻辑核心 + tests 注入 runner 测试
4. 宿主只在托盘/定时器处触发事件，不做业务判断

### 外部程序给宠物发事件

读 `petfw/react.py`（约 40 行）照抄即可；协议详见 docs/API.md 第 5 节。

## 提交节奏约定

里程碑制：每个功能「测试绿 + check.py 全过」即 commit 并 push 到 origin/main；
commit message 用中文描述行为变化而非罗列文件。
