# QQ 小号 DeepSeek 机器人

把 DeepSeek 接到一个普通 QQ 小号上，让它像朋友一样聊天：私聊、进群、发表情包、发语音、听懂语音、偶尔主动找你，还能用 AI 翻唱唱歌。运行在一台 Windows 电脑上。

框架用的是 [AstrBot](https://github.com/AstrBotDevs/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ)。本仓库提供在它们之上**自己写的插件、本地语音/翻唱服务、启动脚本和配置说明**，不包含第三方程序本身、模型和任何密钥。

## 功能

| 功能 | 说明 | 代码 |
|---|---|---|
| 私聊 / 群聊两套人设 | 私聊和群聊用不同性格；群聊人设带防"调教"规则 | `personas/`（示例） |
| 分段回复、看图、看群聊上下文 | AstrBot 自带，按 `docs/配置说明.md` 打开 | — |
| 表情包按需发送 | 只有对方发表情包、或提到"表情/斗图"时才发 | `astrbot_plugins/meme_gate` |
| 按需语音 | 消息里带"语音"就用语音回复；群里超过 50 字改发文字 | `astrbot_plugins/voice_on_demand` |
| 豆包语音（火山引擎 V3） | 支持新版控制台的 API Key 和 2.0 音色 | `astrbot_plugins/volc_tts` |
| RVC 音色说话 | 先用普通 TTS 念出来，再用本地 RVC 模型换成指定音色；管理员 `/声音` 切换 | `astrbot_plugins/rvc_voice` + `cover/rvc_server.py` |
| 听懂语音 | 本地 SenseVoiceSmall 识别语音，还能听出情绪 | `astrbot_plugins/rvc_voice` + `cover/rvc_server.py` |
| AI 翻唱 | "唱一首XX"：网易云找歌 → 分离人声 → 分主唱/和声 → 去混响 → RVC 变声 → 混音 → 发语音 | `astrbot_plugins/ai_cover` + `cover/cover_job.py` |
| 主动聊天 | 安静一段时间后偶尔主动发消息，有时间段和每日上限 | `astrbot_plugins/proactive_chat` |
| 掉线补回复 | 小号掉线重登后，把掉线期间漏掉的消息补回 | `astrbot_plugins/catch_up` |

除了"RVC 音色说话""听懂语音""AI 翻唱"需要 NVIDIA 显卡，其余功能普通电脑就能跑。

## 整体结构

```
QQ ⇄ NapCat（登录小号，OneBot v11）──反向 WebSocket──▶ AstrBot（机器人主体 + 本仓库的插件）
                                                        ├─ DeepSeek API（聊天、看图）
                                                        ├─ 火山引擎豆包语音（TTS，可选）
                                                        └─ 本地语音服务 cover/rvc_server.py（127.0.0.1:6231，可选）
                                                             ├─ RVC 变声（Applio）
                                                             └─ SenseVoice 语音识别
                                                        cover/cover_job.py（翻唱时临时运行）
```

`cover/` 用一个独立的 Python 3.12 环境（PyTorch CUDA 12.8，支持 RTX 50 系），不影响 AstrBot 自己的环境。本地语音服务由插件按需自动启动，闲置 30 分钟自动退出，平时不占显卡。

## 目录

```
astrbot_plugins/   AstrBot 插件，复制到 AstrBot\data\plugins\
cover/             翻唱和本地语音服务：安装脚本、cover_job.py、rvc_server.py
personas/          人设示例
scripts/           一键启动 / 关闭脚本
docs/配置说明.md    AstrBot 每一项具体怎么配、调试接口
```

## 准备

- Windows 10 / 11
- 一个 QQ **小号**（NapCat 不是官方客户端，有被风控的可能，别用重要的号）
- [DeepSeek API Key](https://platform.deepseek.com)
- Python 3.10+（装 AstrBot 用）和 Git
- 可选：火山引擎（豆包语音）API Key，用来发语音
- 可选：NVIDIA 显卡（8 GB 显存够用）+ [Miniconda](https://docs.conda.io/en/latest/miniconda.html) 或 Anaconda，用来翻唱和本地语音；需要约 20 GB 硬盘

## 部署

下面用 `D:\qqbot` 表示放机器人的文件夹，换成你自己的。最后的目录长这样：

```
D:\qqbot\
├─ AstrBot\                 AstrBot 本体（第 1 步）
├─ NapCat\NapCat.xxxxx.Shell\  NapCat（第 2 步）
├─ cover\                   翻唱和本地语音（第 7 步，可选）
├─ start_bot.bat            一键启动
├─ stop_bot.bat / stop_bot.ps1  一键关闭
```

### 1. 安装 AstrBot

```bat
cd /d D:\qqbot
git clone https://github.com/AstrBotDevs/AstrBot.git
cd AstrBot
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

把本仓库的 `scripts\start_astrbot.bat` 复制到 `D:\qqbot\AstrBot\`，双击运行。看到 WebUI 启动后打开 http://127.0.0.1:6185 ，按提示修改默认密码。

> `start_astrbot.bat` 会把 pyffmpeg 自带的 ffmpeg 加进 PATH（Edge TTS 转码要用）。电脑上已经装了 ffmpeg 的话可以删掉那一行。

### 2. 安装 NapCat 并登录小号

1. 从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 Windows Shell 版（`NapCat.Shell.Windows...zip`），解压到 `D:\qqbot\NapCat\`。
2. 运行里面的 `napcat.bat`，用小号的手机 QQ 扫码登录。
3. 把本仓库的 `scripts\napcat.quick.bat` 复制到 NapCat 的 Shell 文件夹，用记事本把 `YOUR_BOT_QQ` 改成小号 QQ 号。以后用它快速登录，不用每次扫码。
4. 打开 NapCat WebUI（http://127.0.0.1:6099/webui ，Token 在 NapCat 窗口的启动日志里）→ 网络配置 → 新建 **WebSocket 客户端**：
   - URL：`ws://127.0.0.1:6199/ws`
   - Token：自己定一串，记下来
   - 消息格式：array

### 3. 在 AstrBot 里接上 QQ 和 DeepSeek

打开 AstrBot 后台：

1. **平台适配器** → 新增 OneBot v11（aiocqhttp）：
   - ID 必须填 **`qq_xiaohao`**（插件里用的是这个 ID）
   - 反向 WebSocket 地址 `127.0.0.1`，端口 `6199`，Token 和 NapCat 里填的一样
2. **模型提供商** → 新增 OpenAI 兼容：API 地址 `https://api.deepseek.com/v1`，填 DeepSeek API Key，选一个 DeepSeek 模型。
3. 回到 NapCat 窗口，出现连接成功，AstrBot 日志里出现适配器已连接，用你的大号私聊小号就能聊了。

### 4. 人设、群聊和白名单

- **人设**：在「人格设定」里新建，内容参考 `personas/example_private.md` 和 `personas/example_group.md`，名字、性格随便改。
- **群聊单独配置**：在「配置」里新建一个叫"群聊"的配置文件，在配置路由里把 `qq_xiaohao:GroupMessage:` 指过去，选群聊人设。
- **白名单**：强烈建议打开，否则任何人加小号好友、拉它进群都能用掉你的 API 额度。格式：
  - 群：`qq_xiaohao:GroupMessage:群号`
  - 私聊：`qq_xiaohao:FriendMessage:QQ号`
- **管理员**：把你大号的 QQ 号加进管理员 ID，`/声音` 等命令只有管理员能用。

分段回复、群聊上下文、看图等设置的推荐值见 `docs/配置说明.md`。

### 5. 安装本仓库的插件

1. 把 `astrbot_plugins\` 下的文件夹全部复制到 `D:\qqbot\AstrBot\data\plugins\`。
2. 改两个占位符（换成你**大号**的 QQ 号）：
   - `voice_on_demand\main.py` 里的 `VOICE_OWNER_IDS = {"YOUR_OWNER_QQ"}`
   - `proactive_chat\main.py` 里的 `TARGETS = ["qq_xiaohao:FriendMessage:YOUR_OWNER_QQ"]`
3. 想要表情包：在 AstrBot 插件市场安装 **meme_manager**（表情包管理器），`meme_gate` 会控制它什么时候发。
4. 重启 AstrBot，或者在插件页面点重载。

每个插件文件开头都有一段"设置区"注释，可以改触发词、时间段、频率等。

### 6. 语音（可选）

1. 在火山引擎控制台开通豆包语音，拿到 API Key。
2. 运行一次 AstrBot 后，会自动生成 `AstrBot\data\plugin_data\volc_tts\api_key.txt`。用记事本打开，把 Key 粘贴进去保存。新版控制台只填一行 API Key；旧版控制台第一行填 APP ID，第二行填 Access Token。
3. 后台「模型提供商 → 文字转语音」→ 新增，类型选 **火山引擎豆包语音（新版 V3 / API Key）**。ID 建议填 `volc_xiaohe`，音色填一个 2.0 音色，例如 `zh_female_xiaohe_uranus_bigtts`。
4. 在配置文件的「文字转语音」里打开，选这个提供商。概率可以设 0.2，表示大约 20% 的回复用语音。

### 7. 翻唱和本地语音（可选，需要 NVIDIA 显卡）

1. 把本仓库的 `cover\` 复制到 `D:\qqbot\cover\`。
2. 插件默认在和 `AstrBot` 并列的 `cover` 文件夹里找（也就是 `D:\qqbot\cover`）。放在别处的话，设置系统环境变量 `QQBOT_COVER_DIR` 指向它，然后重启 AstrBot。
3. 双击 `cover\install_cover.bat`，安装翻唱环境：conda 建环境、PyTorch CUDA 12.8、Applio、人声分离和基础模型。根据网速大约需要 20 分钟到 1 小时。中断了重新运行就行，已完成的步骤会跳过。
4. 双击 `cover\install_stt.bat`，安装语音识别：FunASR 和 SenseVoiceSmall，大约 5～10 分钟。
5. 放音色模型：`cover\voices\<音色名>\` 里放 RVC v2 的 `.pth`（必须）和 `.index`（可选），再放一个 `voice.json`（可以从 `cover\voices\voice.json.example` 复制）：
   ```json
   {"gender": "female"}
   ```
   - `gender` 填 `female` 或 `male`，翻唱时用它判断要不要升降八度。
   - 可选 `target_hz`：这个音色唱起来舒服的音高。
   - `voices` 里只有一个音色时会自动使用它。
   - **请只使用你有权使用的音色**：自己的声音、得到本人同意的声音，或作者明确允许使用的模型。
6. 后台新增提供商：
   - **文字转语音**，类型 **RVC 音色（底层 TTS + 本地变声）**。ID 例如 `rvc_myvoice`，`base_provider_id` 填上面「6. 语音」里建的 `volc_xiaohe`，`voice` 填音色文件夹名。
   - **语音转文字**，类型 **本地 SenseVoice 语音识别**。然后在配置文件里打开「语音转文字」并选它。
7. 第一次翻唱时会自动下载两个分离模型（各约 900 MB），需要等几分钟。

### 8. 一键启动 / 关闭

把 `scripts\start_bot.bat`、`stop_bot.bat`、`stop_bot.ps1` 复制到 `D:\qqbot\`：

- **启动**：双击 `start_bot.bat`。先开 AstrBot，10 秒后用快速登录打开 NapCat。两个黑色窗口都不要关。
- **关闭**：双击 `stop_bot.bat`。只会结束 AstrBot、NapCat 用的那个 QQ 和本地语音服务，不会动你平时用的 QQ。

## 使用

| 在 QQ 里发 | 效果 |
|---|---|
| 正常聊天 | 私聊每条都回；群里只回 @ 它或以 `/` 开头的消息 |
| 带"语音"两个字 | 这条用语音回复 |
| 发语音 | 自动识别成文字再回复（需要第 7 步） |
| `唱一首成都` / `翻唱《晴天》` / `来一首XX` | 唱副歌附近的 60 秒，发语音；群里要 @ 它 |
| `唱一首完整的XX` | 唱整首，作为文件发送 |
| 歌名后面加 `+3` / `-12` | 手动升降调 |
| `/声音`、`/声音 <名字或提供商ID>` | 查看或切换说话用的声音（管理员） |
| `/reset` | 清空当前对话记忆 |

翻唱只能用网易云能免费播放的歌，VIP 歌曲和纯伴奏版会提示换一首。生成的翻唱请只在朋友之间自娱自乐，不要公开发布。

## 常见问题

- **启动 AstrBot 报 `WinError 10013`（端口 6199 无法绑定）**：端口被 Windows 保留了。用管理员身份运行：
  ```bat
  net stop winnat
  netsh int ipv4 add excludedportrange protocol=tcp startport=6199 numberofports=1
  net start winnat
  ```
- **小号隔一段时间就掉线、要重新扫码**：NapCat 日志里出现"登录已失效"，一般是 QQ 风控。让小号的手机 QQ 保持登录，别在别的电脑上登小号，不要频繁切换 VPN 节点。重新登录后可以用 `catch_up` 插件补回漏掉的消息（接口见 `docs/配置说明.md`）。
- **安装翻唱环境时 C 盘变小了**：是下载缓存。安装脚本会把缓存放在 `cover\.uv_cache` 并在结束后清理；如果还有残留，运行 `cover\clean_c_cache.bat`。
- **50 系显卡报 CUDA 不支持**：需要 CUDA 12.8 版的 PyTorch，`install_cover.py` 装的就是这个版本，不要用旧的 RVC 整合包。
- **翻唱声音怪、咬字不清**：最大的因素是音色模型本身。用唱歌录音训练的模型效果会好很多。

## 致谢 / 第三方

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ)：QQ 协议端
- [Applio](https://github.com/IAHispano/Applio)：RVC 变声
- [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)：人声分离（BS-Roformer、MelBand Roformer karaoke / dereverb 模型）
- [FunASR](https://github.com/modelscope/FunASR) / SenseVoiceSmall：语音识别
- meme_manager：AstrBot 插件市场的表情包管理器

各项目和模型的许可证以它们自己的为准。本项目仅供学习和个人娱乐使用。
