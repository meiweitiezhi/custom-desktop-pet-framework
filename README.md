# 自定义桌宠框架

数据驱动、可插拔大脑、离线可玩的本地桌宠：一只圆滚滚的白色小宠物
（默认昵称「团子」）挂在桌面右上角浮动卖萌，会干饭、会打气、会犯困，
被戳了会说话。**大模型只用来对话，其它一切功能 100% 本地**——没网、没配 key
也能完整地玩。

## 功能

- **表情动画**：核心四态（发呆 / 举花球打气 / 举叉子干饭 / 睡觉）+ 可选新四态
  （笑哭 / 惊讶 / 生气 / 扭舞），各自不同的浮动节奏；可选态缺图时自动降级跳过，
  补图即解锁
- **双大脑可热切换**（托盘菜单）：
  - 规则脑：查表+随机台词，离线兜底，永不断线
  - 对话脑：调你自己的 OpenAI 兼容网关，宠物自己决定说什么+什么表情；
    失败时报「需要接入自己的api」并自动降级规则脑（90 秒限频防刷屏）
- **健康提醒**：定时喝水/伸懒腰弹气泡
- **hook 联动**：本机 HTTP 桥 + 一行命令客户端，让 coding agent 实时投喂事件
- **Git 成长系统**：扫仓库当日提交换称号——咸鱼蛋 → 勤快蛋 → 卷王蛋 → 代码之蛋；
  收工 hook 或每日定时弹**全屏结算画面**，游戏风走马灯回放今日战报
  （有本地 BGM 就循环播放，缺音频/缺多媒体后端自动静默）
- **别走别走梗**：离开 3 分钟回来被笑哭嘲讽，离开 10 分钟以上被兴师问罪
  「别走别走！我班呢！」
- **编译兴衰军师**：连败 3 次触发「俱往矣……」哀叹，连败后翻盘自动庆祝
  「三十年河东 三十年河西！」
- **过审小剧场**：对话脑每句台词约六分之一概率盖上「（本句已过审，
  审核笑了 N 分钟)」的恶搞章
- **天气心情灯**：晴→打气、雨雪→睡觉（本地演示菜单；联网抓取由你自己的
  本地脚本完成，见下方红线）

## 快速开始

```bash
pip install -r requirements.txt

# 1) 放入你的角色图（见「素材红线」，仓库不含任何图片）
#    按 assets/raw/<状态名>.png 命名：
#    核心态 idle.png / cheer.png / eat.png / sleep.png（缺了起不来）
#    可选态 laugh.png / shock.png / angry.png / dance.png（缺了只跳过不报错）
python prep_assets.py        # 自动抠透明底并裁剪

# 2) 启动
python run.py                # 首次运行自动生成 config.ini（含随机鉴权 token）
```

想接入大模型对话：打开自动生成的 `config.ini`，把 `[brain]` 的
`api_base / api_key / model` 三项填上即可（具体填什么看你自己本地的接入笔记，
本项目仓库与文档刻意不出现任何厂商信息）。不填则一直使用规则脑。

## 让写代码时桌宠有反应

token 每次启动都会轮换，所以不要在 hook 里写死它，统一用自带客户端：

```bash
python -m petfw.react start     # 开始干活 → 打气
python -m petfw.react edit      # 改代码   → 干饭（让我看看改了啥）
python -m petfw.react success   # 成功    → 打气庆祝
python -m petfw.react error     # 出错    → 躺平睡觉
```

把上面的命令挂进你的 coding agent hooks（如 PostToolUse/Stop）即可。
也可以直接 POST `/react`（协议见 [docs/API.md](docs/API.md)）。

## 接口文档

完整协议、事件词表、Driver 插件规范、manifest 格式：
[docs/API.md](docs/API.md)。agent 协作开发请看
[.agents/skills/petfw/SKILL.md](.agents/skills/petfw/SKILL.md)。

## 目录分工

| 目录 | 职责 | 改动要求 |
|---|---|---|
| `petfw/host.py` | Qt 渲染内核（只认命令，不做决策） | 过全部测试 |
| `petfw/bus.py` | 事件/命令数据协议 | 变更需同步文档 |
| `petfw/drivers/` | 大脑：规则驱动 / 大模型驱动 | 插件式，勿在内核特判 |
| `petfw/extensions/` | 独立玩法（git成长/天气） | 只经 bus 事件影响宠物 |
| `assets/` | manifest + 图片（图片不入库） | — |
| `tests/` | 无 GUI 核心测试 | 新逻辑必须带测试 |
| `tools/` | 门禁与脚本 | — |

## 素材与隐私红线（重要）

1. **角色表情包图片永不入库**（第三方版权形象）：`assets/raw/` 与
   `assets/states/` 都在 `.gitignore` 里。clone 之后需要按上面步骤自备图片；
   想分享给朋友建议用打包好的 exe + 私发素材。
2. **config.ini / runtime.json 永不入库**：api_key、模型名、桥接 token 全在本地。
3. **仓库内零厂商痕迹**：代码、注释、文档都不含具体 API 地址/密钥/模型名，
   `tools/check.py` 会机械化扫描强制执行。
4. **桥接只监听 127.0.0.1** 且带 token 鉴权。

## 发布前门禁

```bash
python tools/check.py    # 单元测试全绿 + 版权图扫描 + 厂商字样扫描
```

## License

本项目采用自定义版权保护许可：可以克隆、学习、fork 到自己仓库私有修改自用；
再分发（含打包二进制）须完整保留版权声明与署名且不得商用。详见
[LICENSE](LICENSE)。

## Roadmap

已完成 ✔：双大脑热切换 / hook 联动 / Git 成长称号 / 全屏结算画面（每日战报 +
BGM）/ 别走别走回归彩蛋 / 编译兴衰军师 / 过审小剧场

下一站：番茄钟专注模式 → 声控跳舞（麦克风音量驱动）→ 天气联网接入（可选插件）

### 打包：出一份含素材的私发版

仓库不含图片，想分享给朋友请自己打一份带素材的 exe 私发：

```bash
# 0) 备好素材成品（assets/states/*.png；raw 原图不会被装进 exe）
python prep_assets.py
pip install pyinstaller      # 仅打包机需要

# 1) 一键构建
tools/build_exe.bat          # 等价于 python -m PyInstaller tools/petfw.spec

# 2) 验证
QT_QPA_PLATFORM=offscreen dist/CustomPetFramework.exe --smoke
```

产物是单文件 `dist/CustomPetFramework.exe`，内含 manifest 与 states 成品图；首次运行会在
exe 同目录生成 `config.ini` 与 `runtime.json`。注意：**构建产物与你的原图都不
要外传到公开渠道**，私发对象须遵守 [LICENSE](LICENSE)（非商用、保留署名）。
