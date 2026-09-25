# IoT 厂商 App 自动化实验平台

本目录提供一个跨平台实验框架，用真实 Android 厂商 App 触发 IoT 设备事件，并将 App 操作、状态回执、网络隔离检查、可选连续抓包和可选独立状态观测整理为可验证的实验会话。

平台核心面向复用；当前已有米家台灯的 `turn_on`/`turn_off` 和小爱触屏音箱的 `play_music`/`pause_music` 两组二态事件，以及米家台灯的亮度、色温、六种情景模式和专注模式四类参数化事件（见 [10.2 节](#102-米家台灯1s-增强版亮度色温情景与专注模式参数化事件)）。接入更多状态或事件时仍需扩展事件模型和编排器，不能只增加选择器。

供模型或自动化代理执行完整接入任务时，必须同时阅读 [AGENTS.md](AGENTS.md)。该文件定义了从实验建模到正式采集的可复用工作流、证据要求、停止条件和最少必要测试。

## 1. 平台能力与边界

平台已经提供：

- Windows 开发验证与 Ubuntu 正式采集共用的 Python 编排代码；
- ADB 设备发现、真机参数读取、单设备选择和多设备进程隔离；
- 项目本地 Appium 3 与 UiAutomator2 生命周期管理；
- 厂商 App 适配器接口，包括导航、状态读取、事件执行、回执等待、恢复和诊断；
- 不抓包的开发模式与单次连续 Dumpcap 抓包模式；
- 可替换的独立状态观测接口；
- 纳秒时间戳、唯一事件 ID、追加式 JSONL 日志、质量报告和会话校验；
- 失败页面 XML、截图和 Appium 日志留存。

当前未包含：

- 账号登录、验证码、CAPTCHA 或安全授权自动化；
- 通用多厂商适配器注册表；当前注册了米家台灯和小爱触屏音箱；
- 已启用的会话内 Home Assistant 状态提供者；音箱使用会话后 JSON 导入关联；
- 多手机单编排器和共享事件调度；当前并行方式是一进程一手机；
- P/U 数据集切分、流量特征提取或模型训练。

## 2. 架构

```text
实验 YAML ─┐
           ├─ CLI / ExperimentRunner ── ActionJournal / QualityReport
运行 YAML ─┘              │
                          ├─ VendorAppAdapter ── Appium ── Android 厂商 App
                          ├─ CaptureBackend ──── Disabled 或 Dumpcap
                          └─ HaObservationProvider ── 当前为 Disabled
```

主要扩展点：

| 扩展点 | 路径 | 责任 |
|---|---|---|
| 实验配置 | `experiment/*.yaml` | 手机、App、IoT 设备、网络、事件和选择器 |
| 运行配置 | `runtime/*.yaml` | 平台差异、Appium、ADB、抓包和输出目录 |
| App 适配器 | `src/iot_exp/adapters/` | 页面导航、状态识别、事件执行、回执和恢复 |
| 事件编排 | `src/iot_exp/orchestrator.py` | 前置状态、随机等待、事件 ID、结果分类和会话产物 |
| 抓包后端 | `src/iot_exp/backends/capture.py` | 会话级连续抓包 |
| 独立观测 | `src/iot_exp/backends/ha.py` | App 之外的设备状态证据 |

目录布局：

```text
HA/
├─ automation/
│  ├─ AGENTS.md             # 面向模型的完整复用工作流
│  ├─ experiment/           # 各实验和适配器参数
│  ├─ runtime/              # Windows/Ubuntu 运行环境
│  ├─ src/iot_exp/          # 平台核心与适配器
│  ├─ tests/                # 必要的单元与契约测试
│  └─ runs/                 # 自动生成，不提交 Git
├─ legacy/                  # 旧模型、历史 PCAP、旧数据和权重
└─ doc/                     # 实验设计与项目文档
```

新会话不得写入 `legacy/`，平台也不会自动读取或修改旧 PCAP、模型权重和 HA 导出数据。

## 3. 快速开始

### 3.1 Windows 开发模式

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\automation
uv sync --extra dev
npm install
uv run iot-exp doctor
uv run iot-exp devices
```

Windows 默认使用 `runtime/windows-dev.yaml`，该配置为开发模式且不抓包。

完成一个不连接真机的逻辑会话：

```powershell
uv run iot-exp run --dry-run --repetitions 1 --seed 42 --session-id dry_run_001
uv run iot-exp validate-session runs/sessions/dry_run_001
```

### 3.2 Ubuntu 开发模式

```bash
cd /path/to/HA/automation
uv sync --extra dev
npm install
uv run iot-exp --runtime runtime/ubuntu-dev.yaml doctor
uv run iot-exp --runtime runtime/ubuntu-dev.yaml devices
```

Ubuntu 使用 `runtime/ubuntu-dev.yaml` 进行不抓包真机验证，正式抓包才使用
`runtime/ubuntu-lab.yaml`。

在已经将依赖安装到项目 `.tools/` 目录的主机上，使用下列入口可自动加载 Node、Java、
Android SDK 和 `uv`，同时自动选择 Ubuntu 不抓包配置：

```bash
./iot-exp-local.sh doctor
./iot-exp-local.sh devices
./iot-exp-local.sh preflight --udid <serial>
```

完成一个不连接真机的逻辑会话：

```bash
./iot-exp-local.sh run --dry-run --repetitions 1 --seed 42 --session-id dry_run_001
./iot-exp-local.sh validate-session runs/sessions/dry_run_001
```

### 3.3 图形控制台

完成一次依赖安装和前端构建：

```text
uv sync --extra dev
npm install
npm run web:build
```

Windows 双击 `start-console.cmd`。Ubuntu 直接运行 `./start-console.sh`，或双击
`IoT实验控制台.desktop`；这两个入口会自动加载项目 `.tools/` 中的依赖，不要求 `uv` 位于
全局 `PATH`，也不应使用 `sudo`。启动入口只监听 `127.0.0.1:8765` 并自动打开浏览器。
也可以在已经激活依赖环境的终端执行：

```text
uv run iot-exp-gui
```

控制台提供设备与环境、创建实验、运行中心、历史与结果、环境设置五个页面。实验参数只形成
本次任务的配置快照，不覆盖 `experiment/*.yaml`。模拟运行不连接真机；真机验证不抓包；正式
采集必须提供目标设备 IP、抓包接口和过滤器，并通过正式预检。

控制台默认最多并行运行四个独立任务。同一手机、同一 IoT 设备或同一正式抓包接口会自动
排队；CLI 与控制台通过 `runs/locks/` 共用资源锁。浏览器关闭不会停止任务，重新打开即可继续
查看。任务元数据保存在 `runs/console.sqlite3`，原始实验产物仍保存在 `runs/sessions/<session_id>/`。

从仓库根目录运行时使用：

```text
uv run --project automation iot-exp doctor
```

## 4. 环境要求

### 4.1 Python 与 Node

- Python 3.10–3.13；
- `uv`；
- Node 20.19+；
- npm 10+；
- JDK，并正确设置 `JAVA_HOME`。

程序使用仓库内固定的 Appium 和 UiAutomator2 依赖，不要求全局安装 Appium。不要手工编辑 `uv.lock` 或 `package-lock.json` 中的解析结果。

### 4.2 Android SDK

必须安装 Android SDK Platform Tools。SDK 根目录按以下顺序解析：

1. 运行配置中的 `android_sdk_root`；
2. `ANDROID_SDK_ROOT`；
3. `ANDROID_HOME`；
4. 解析 `adb` 的真实路径；只有位于 `<sdk>/platform-tools/` 时才反推出 `<sdk>`。

Ubuntu 上如果 `/usr/bin/adb` 只是指向 SDK 的符号链接，程序可以自动识别；如果发行版只提供独立 ADB，则必须在运行 YAML 填写完整 SDK 根目录。程序不会把 `/usr` 或 `/` 猜成 SDK。

### 4.3 抓包工具

- Windows：需要 Npcap 和可用的 Dumpcap 权限；
- Ubuntu：需要 Wireshark/Dumpcap，并允许运行用户访问抓包接口；
- 开发配置可以设置 `capture_mode: disabled`；
- `formal` 配置强制要求 `capture_mode: dumpcap` 和明确的抓包接口。

## 5. Android 手机发现与选择

### 5.1 两个平台都需要的手机设置

手机准备：

1. 开启开发者选项和 USB 调试；
2. 使用数据线连接运行平台的主机；
3. 在手机上确认 RSA 调试授权；
4. 保持解锁和充电，关闭目标 App 的电池优化和自动更新；
5. 手机连接公共 Wi-Fi 或蜂窝网络，不连接实验 IoT 网络；
6. 关闭 USB 网络共享。

手机上的 RSA 调试授权与主机的 USB 文件权限是两回事：

- 在手机弹窗中勾选“始终允许此计算机”后，RSA 授权通常不需要每次重新确认；
- 撤销 USB 调试授权、清除调试密钥、更换控制主机或重置手机后，需要重新确认 RSA；
- Windows 通常由设备驱动处理 USB 权限；
- Ubuntu 必须让当前用户拥有对应 USB 设备节点的读写权限，见下节。

### 5.2 Windows USB 准备

安装手机厂商 USB 驱动或 Google USB Driver，然后重新插入手机。设备管理器中不应存在带警告
标记的 ADB 设备。使用以下命令确认状态：

```powershell
uv run iot-exp devices
```

### 5.3 Ubuntu USB 权限

先用 `lsusb` 查找手机的厂商 ID 和产品 ID。例如本项目当前验证的 LG 手机显示为
`1004:631f`。对这台已验证手机，直接运行项目内的一键安装脚本：

```bash
sudo ./install-lg-udev.sh
```

脚本只安装精确匹配 `1004:631f` 的规则，不会放宽其他 LG USB 设备的权限。安装后重新插入
手机；若当前登录会话还没有取得 `plugdev` 组，则注销并重新登录一次。之后 USB 拔插通常不再
需要重新配置 Linux 权限。其他型号不能直接使用该脚本，应按其 `lsusb` ID 创建单独规则。

`setfacl` 只适合临时诊断，例如：

```bash
sudo setfacl -m u:"$USER":rw /dev/bus/usb/001/005
```

这里的总线和设备编号会在重新插拔后变化，因此临时 ACL 会失效；它不会导致手机重新询问 RSA，
但需要对新的设备节点再次授权。长期使用应配置上述精确 udev 规则。

Ubuntu 项目本地环境使用以下命令确认设备状态：

```bash
./iot-exp-local.sh devices
```

输出包括 UDID、ADB 状态、厂商、型号、Android 版本、SDK level、transport ID、目标 App 版本，以及 ADB、SDK、Appium 端口和抓包模式。

设备选择规则：

- `--udid <serial>`：确定性选择，推荐用于脚本和正式实验；
- `--select-device`：交互式选择，仅用于有人值守的终端；
- 未指定时优先使用实验 YAML 的 `phone.udid`；
- YAML 为占位值且只有一台在线手机时自动选择；
- 多台手机在线但没有明确目标时停止，防止误控；
- `unauthorized`、`offline` 和未知 UDID 都会阻止真实运行。

设备选择只覆盖本次运行，不回写实验 YAML。真实 App 版本会从所选手机读取，并覆盖本次会话中的默认版本。

## 6. 通用实验接入流程

完整决策和证据要求见 [AGENTS.md](AGENTS.md)。人工执行时使用以下顺序：

1. 定义实验对象、目标 App、设备状态、合法事件和网络边界；
2. 复制或创建 `experiment/<experiment>.yaml`；
3. 为目标平台创建或选择 `runtime/<runtime>.yaml`；
4. 实现 `VendorAppAdapter`，并在 CLI 的适配器创建位置注册；
5. 只为新增逻辑添加必要单元测试；
6. 运行 `ruff`、`pytest` 和 `doctor`；
7. 完成 `--dry-run` 和会话校验；
8. 连接真机，运行 `devices` 和 `preflight`；
9. 运行 `inspect-app`，根据页面 XML 和截图完善选择器；
10. 执行每种事件一次的最小真机闭环；
11. 执行重复性验收；
12. 最后才启用正式连续抓包和独立状态观测。

不要在选择器尚未稳定、网络隔离未通过或真机最小闭环失败时启动正式采集。

## 7. 开发、验收与正式采集

### 7.1 逻辑干运行

```text
uv run iot-exp run --dry-run --repetitions 1 --seed 42 --session-id dry_run_001
uv run iot-exp validate-session runs/sessions/dry_run_001
```

预期：每种事件生成一条记录，结果为 `app_ack_only`，事件 ID 唯一，会话校验为 `ok: true`。
参数化事件（亮度/色温/情景/专注）的干运行见 [10.2 节](#102-米家台灯1s-增强版亮度色温情景与专注模式参数化事件)，
同样不连接真机，且不要求 HA 日志。

### 7.2 真机无抓包验证

Windows：

```powershell
uv run iot-exp preflight --udid <serial>
uv run iot-exp inspect-app --udid <serial>
uv run iot-exp run --udid <serial> --repetitions 1 --session-id first_real_001
uv run iot-exp validate-session runs/sessions/first_real_001
```

Ubuntu 项目本地环境：

```bash
./iot-exp-local.sh preflight --udid <serial>
./iot-exp-local.sh inspect-app --udid <serial>
./iot-exp-local.sh run --udid <serial> --repetitions 1 --session-id first_real_001
./iot-exp-local.sh validate-session runs/sessions/first_real_001
```

最小闭环通过后再增加重复次数。二态设备的建议首轮验收是每种事件 10 次，App 成功率不低于 95%，无坐标点击，所有 `event_id` 唯一。

### 7.3 Ubuntu 正式采集

编辑正式运行配置：

- `capture_interface`：镜像口；若抓包主机同时提供实验热点，则填写热点的 AP 无线接口；
- `capture_filter`：目标 IoT 设备过滤器；
- `android_sdk_root`：无法自动解析时显式填写；
- `pre_roll_seconds`、`post_roll_seconds`：会话保护时间；
- 实验 YAML 的 `network.target_device_ip` 和 `forbidden_cidrs`。

执行：

```text
uv run iot-exp --runtime runtime/ubuntu-lab.yaml doctor
uv run iot-exp --runtime runtime/ubuntu-lab.yaml preflight --udid <serial>
uv run iot-exp --runtime runtime/ubuntu-lab.yaml run --udid <serial> --repetitions 20
```

正式模式只启动一次连续 Dumpcap。任何正式预检失败都必须阻止采集。

### 7.4 抓包主机提供 IoT 热点

该拓扑的完整实验边界与验收条件见 [实验设计 3.2 节](../doc/厂商App异网自动化实验设计.md#32-场地受限时抓包主机兼作实验热点)。抓包主机需要独立外网上行（建议有线）和支持 AP 模式的无线网卡；IoT 设备连接实验 SSID，控制手机始终连接其他网络。不要让同一无线网卡同时承担上行和热点，除非已确认驱动支持并发且长时间试运行稳定。

Ubuntu 使用 NetworkManager 时，先在系统网络设置中建立加密热点并启用连接共享，再只读核对：

```bash
iw list                         # Supported interface modes 中应有 AP
nmcli device status             # 确认上行与热点分别使用哪个接口
nmcli -f 802-11-wireless.mode,802-11-wireless-security.key-mgmt,ipv4.method connection show <热点连接名>
ip -br addr                     # 记录 AP 接口的实际网段和设备地址
dumpcap -D                      # 确认 AP 接口可被抓包程序访问
```

热点应使用 WPA2/WPA3、`ipv4.method shared`，让设备得到 DHCP 地址并经主机 NAT 上网；NetworkManager 可能自动分配 `10.42.x.0/24`，以现场输出为准。不要把热点密码写入仓库配置、命令历史或会话日志。若系统创建的是开放/WEP 热点，或设备无法稳定联网，先修正网卡与热点配置。

正式运行前更新两个现有 YAML 文件，使用现场值，不另造配置字段：

| 文件 | 设置 |
|---|---|
| `runtime/ubuntu-lab.yaml` | `capture_interface` 填 **AP 无线接口**；`capture_filter` 先经试抓包验证，再填 `host <设备热点侧IP>` 等目标过滤器。不要填外网上行接口。 |
| `experiment/mi_desk_lamp_1s.yaml` | `network.target_device_ip` 填设备在热点上的稳定 IP；`network.forbidden_cidrs` 包含热点网段；`phone.network` 保持真实的 `public_wifi` 或蜂窝网络类型。 |

先在 AP 接口进行短时无过滤试抓包，确认设备上线、DNS 和 App 动作期间的设备发出与收到的包，再检查正式过滤器是否仍覆盖目标流量。`host <IP>` 不覆盖全部 ARP、广播发现和无线链路帧；如研究这些流量，需调整抓包范围并重新验证。普通数据包抓取不使用 monitor mode，它可能使网卡退出正常热点工作模式。外网上行接口上的 NAT 后数据不能代替设备侧 PCAP。

现有 `preflight` 在网络隔离方面只检查手机地址是否落在禁止网段，并以设备 IP 的 `ping` 作为可达性辅助判断；`ping` 不通并不能单独证明隔离。当前 `require_no_usb_tethering` 配置也尚未对应自动检查，预检不会验证热点的 DHCP/NAT、AP 抓包覆盖率或厂商 App 是否走局域网控制。现场仍需核对 USB 网络共享关闭、手机未连接实验 SSID、设备确实通过热点访问云端、App 操作后 PCAP 有双向流量，再开始正式会话。热点重启、设备 IP 改变或上行切换后重新执行这些检查并新建会话。

## 8. 多手机与混杂流量

当前并行模型是一进程一手机。每个进程必须使用唯一的 Appium 端口和 UiAutomator2 `systemPort`：

```text
uv run iot-exp run --udid SERIAL_A --phone-id phone_a --appium-port 4723 --system-port 8200 --repetitions 10
uv run iot-exp run --udid SERIAL_B --phone-id phone_b --appium-port 4725 --system-port 8201 --repetitions 10
```

每条动作记录同时包含 `phone_id` 和 `phone_udid`。多个控制进程可以共同制造背景和混杂流量，但同一镜像口或热点 AP 接口的连续抓包应只由一个采集进程负责，避免重复 PCAP、接口竞争和不一致的边界。

如果实验需要统一随机化多台手机的事件、共享一个会话 ID 和一份 PCAP，应扩展为单编排器、多适配器会话、单 `CaptureBackend`，而不是让多个正式进程分别启动 Dumpcap。

## 9. 会话产物与结果

```text
runs/sessions/<session_id>/
├─ session.yaml
├─ actions.jsonl
├─ run_journal.jsonl
├─ traffic.pcapng
├─ appium.log
├─ ha_events.json
├─ clock_sync.json
├─ network_isolation_check.json
├─ quality_report.json
└─ screenshots/
```

结果类型：

- `app_ack_only`：App 页面确认成功，没有独立观测；
- `confirmed`：App 与独立观测都确认预期状态；
- `ha_only`：只有独立观测确认；
- `timeout`：回执在超时内未达到预期；
- `unexpected_state`：没有满足前置状态的合法事件；
- `automation_error`：页面、选择器、Appium 或恢复流程异常；
- `failed`：保留给明确失败分类。

失败会话和原始证据必须保留。不要手工覆盖 `actions.jsonl`、`run_journal.jsonl` 或 PCAP；重试时创建新会话和新事件 ID。

## 10. 当前已验证适配器：米家台灯1S 增强版

当前默认实验为 [mi_desk_lamp_1s.yaml](experiment/mi_desk_lamp_1s.yaml)，适配器为 `MiHomeDeskLamp1SAdapter`。

已验证环境：

- 米家包名：`com.xiaomi.smarthome`；
- 已验证 App 版本：`11.8.703`；
- 设备页标题：`米家台灯1S 增强版`；
- 状态语义：页面显示“关灯”表示当前为开启，显示“开灯”表示当前为关闭；
- `turn_on` 和 `turn_off` 使用可见文字选择器，不使用坐标；
- Windows 无抓包最小闭环已经完成，关灯和开灯均得到 `app_ack_only`。

米家案例命令：

```text
uv run iot-exp devices
uv run iot-exp preflight --udid <serial>
uv run iot-exp inspect-app --udid <serial>
uv run iot-exp run --udid <serial> --repetitions 1
```

首次接入或 App 升级后应重新运行 `inspect-app`。失败诊断会写入：

```text
runs/inspect/inspect_failure.xml
runs/inspect/inspect_failure.png
```

选择器优先级：资源 ID、无障碍描述、可见文字、Android UIAutomator、XPath。坐标只能作为最后的设备专用降级方案，不能进入通用编排层。

该案例仍需填写真实 `device.firmware`。由于 `HaObservationProvider` 当前禁用，成功记录应为 `app_ack_only`，不能错误标记为 `confirmed`。

## 10.1 小爱触屏音箱 LX04：音乐播放与暂停

使用 [实验配置](experiment/xiaomi_touchscreen_speaker_music.yaml) 和
[正式运行配置](runtime/ubuntu-speaker-lab.yaml)。已识别设备型号 `xiaomi.wifispeaker.lx04`、
固件 `2.42.112`、HA 实体 `media_player.xiaomi_cn_636575596_lx04`、热点侧 IP
`10.42.0.196`。控制手机是 LG V405，米家版本 `11.8.703`。

米家设备页的播放按钮没有可读取的文字或无障碍标签。专用适配器根据实际截图中按钮的
三角形或双竖线读取当前状态，每次点击前重新检查前置状态，点击后等待页面回执。
如果图标无法可靠识别，状态为 `unknown`，不得按上一次点击推断。
曲目自然结束造成的状态变化不能算作点击确认。
本音箱试采集连续记录播放和暂停各 20 次尝试；页面未收到回执时保留失败记录和数据包，
不因试采集中的成功率中途停止会话。
操作前随机等待 3–6 秒、操作后等待 2 秒；页面 10 秒无回执即记录失败，
同一逻辑事件最多尝试 3 次。每次尝试都有独立事件 ID，因此原始日志可能超过 40 条，
但计划的逻辑事件仍是播放和暂停各 20 次。

在 `automation/` 下执行干运行与无抓包验收：

```bash
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml run --dry-run --repetitions 1 --session-id speaker_dry_001
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml inspect-app --udid LMV405UAd6421e56
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml run --udid LMV405UAd6421e56 --repetitions 1 --session-id speaker_one_001
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml run --udid LMV405UAd6421e56 --repetitions 10 --session-id speaker_ten_001
```

正式采集前，确认手机未连接 `10.42.0.0/24` 热点、USB 网络共享关闭、设备仍为
`10.42.0.196`、`wlp2s0` 能抓到该设备的双向流量、Dumpcap 权限可用，并实测 HA 电脑与
采集机的时钟偏差。可将 [时钟探针](clock_probe.py) 复制到 HA 电脑；采集机运行
`python3 clock_probe.py serve --host 10.150.254.162`，HA 电脑运行
`python3 clock_probe.py client --host 10.150.254.162`。记录输出的偏差和不确定度，
只有偏差连同不确定度小于 500 毫秒时，才允许自动候选金标准关联。

正式模式需要当前进程拥有 `wireshark` 组权限；加入组后重新登录，或使用 `sg wireshark`
启动命令。先运行 `doctor` 和 `preflight`，再用同一会话连续抓取每类 20 次：

```bash
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml --runtime runtime/ubuntu-speaker-lab.yaml doctor
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml --runtime runtime/ubuntu-speaker-lab.yaml preflight --udid LMV405UAd6421e56
./iot-exp-local.sh --experiment experiment/xiaomi_touchscreen_speaker_music.yaml --runtime runtime/ubuntu-speaker-lab.yaml run --udid LMV405UAd6421e56 --repetitions 20 --session-id speaker_formal_001
```

会话结束后在采集机取得精确 UTC 查询范围：

```bash
./iot-exp-local.sh ha-window runs/sessions/speaker_formal_001
```

在 HA 电脑上用独立的数据处理仓库导出对应 `entity_id`、`start`、`end` 的历史记录；
令牌只从该电脑的环境变量 `HA_TOKEN` 读取，不得贴入仓库或发给他人。将导出的 JSON
复制回采集机，再执行：

```bash
./iot-exp-local.sh review-pcap runs/sessions/speaker_formal_001
./iot-exp-local.sh reconcile-ha runs/sessions/speaker_formal_001 speaker_ha_history.json --clock-offset-ms <实测偏差> --clock-uncertainty-ms <测量不确定度>
```

关联结果写入会话中的 `ha_reconciliation.json`，保留原始动作日志和 PCAP。
报告中的每条事件都需要人工核对 App、HA 与对应 PCAP；缺失、重复或时间范围外的
HA 状态均保持待复核，不能自动当作确认事件。该流程直接导出 JSON，无需手工 CSV。

如果 HA 电脑接入采集机热点，可使用以下一次性配对后的自动协作方式。先等正在运行的
正式会话结束，再让 HA 电脑连接该热点，以免中途改变网络条件。采集机地址为
`10.42.0.1`；服务只绑定这个热点地址。采集机启动：

```bash
./.tools/uv/uv run --project . python ha_bridge_server.py runs/sessions/speaker_formal_001
```

首次启动会生成权限为 `0600` 的 `runs/ha_bridge.key`。把该密钥文件以本地方式
复制到 HA 电脑并只允许该电脑用户读取。在 HA 电脑中撤销旧长期访问令牌、
生成新令牌并只在该电脑设置环境变量 `HA_TOKEN`；两个密钥都不要写入仓库或聊天记录。
可把 [HA 端协作脚本](ha_companion.py) 和配对密钥打包后复制到 HA 电脑。
在 HA 电脑运行：

```bash
python3 ha_companion.py --bridge-url http://10.42.0.1:8767 --ha-url http://localhost:8123 --key-file ha_bridge.key
```

脚本会等待采集机发布已完成会话的实体 ID 和 UTC 范围，测量两机时差，通过 HA 官方历史
接口导出 JSON，在 HA 电脑留一份私有副本，并向采集机上传历史 JSON 与时钟测量值。
采集机验证会话和时钟范围后生成 `ha_history.json`、`ha_clock_probe.json`、
`pcap_review.json` 与 `ha_reconciliation.json`。配对请求使用消息签名，HA 令牌
不会发给采集机。上传后每条关联仍为待人工复核；自动报告只列候选事件和逐事件双向包数。

## 10.2 米家台灯1S 增强版：亮度、色温、情景与专注模式（参数化事件）

使用 [mi_desk_lamp_1s_advanced.yaml](experiment/mi_desk_lamp_1s_advanced.yaml) 在已验证的开灯/关灯之外
定义四类带目标的事件：`set_brightness`（亮度数值）、`set_color_temperature`（色温数值）、
`select_scene`（六种情景之一）和 `set_focus_mode`（专注开关布尔目标）。这些事件的执行、回执
判定、证据和校验与二态事件不同：每次尝试都必须在动作前后各读取一次目标维度的页面状态，
只有"动作后读值命中目标且动作前未达目标"才记为成功。

### 参数化事件 YAML 字段

实验级 `parameters:` 块声明各维度的合法边界；对应事件出现时该声明必须存在，否则配置被拒绝：

```yaml
parameters:
  brightness: {range: [1, 100], unit: "%", tolerance: 2}          # 范围、单位、允许误差
  color_temperature: {range: [2600, 5100], unit: "K", tolerance: 100}
  scene:
    scenes: [电脑模式, 温馨模式, 休闲模式, 办公模式, 阅读模式, 娱乐模式]
    presets:  # 真机逐一选择后的亮度/色温回读；六组必须覆盖 scenes 且在误差内互不重叠
      电脑模式: {brightness: 50, color_temperature: 2700}
      温馨模式: {brightness: 60, color_temperature: 3500}
      休闲模式: {brightness: 50, color_temperature: 4000}
      办公模式: {brightness: 100, color_temperature: 4500}
      阅读模式: {brightness: 100, color_temperature: 5000}
      娱乐模式: {brightness: 80, color_temperature: 3000}
events:
  - {event_type: set_brightness, target: 30}          # target 必须是整数且落在 range 内
  - {event_type: set_color_temperature, target: 3500} # 同上，单位与 range 来自 parameters
  - {event_type: select_scene, target: 阅读模式}       # target 必须是 scenes 候选之一
  - {event_type: set_focus_mode, target: true}        # target 必须是布尔值
```

- 事件唯一性按 `(event_type, target)` 判定：六个不同情景目标可以共存，重复声明同一目标被拒绝；
  二态事件（无目标）仍按 `event_type` 判定，旧台灯和音箱 YAML 原样可用。
- 亮度、色温、情景事件的 `required_state` 默认为 `on`（台灯须已开启），可显式声明 `on`/`off`；
  `set_focus_mode` 不接受电源前置条件，其前置判据是开关自身状态。
- 数值目标越界、情景目标不在候选集、目标类型不匹配或缺少 `parameters` 声明都会在运行前被拒绝，
  不会向设备发送动作。
- 情景 ID 与设备页按钮文字一一对应（`scene_<ID>_button` 选择器）。本机 App 不暴露选中属性，
  运行时改用设备页的亮度与色温文字回读匹配 `presets`，无需识别截图；两者有一项不可读或
  同时匹配多个预设时返回未知。可读但不匹配任何预设时返回空值。

### 运行语义：目标已达到不算状态变化

1. 调度器为每个 `(event_type, target)` 身份单独计数并随机选择；选择时读取目标维度，
   目标已达到或状态不可读的事件本轮不参选，并在 `run_journal.jsonl` 记录
   `target_already_reached` / `parameter_read_failed`。
2. 随机等待结束后、动作前再次复核电源前置条件与目标维度：期间目标被达成会记录
   `precommand_target_reached` 并改选其他事件；期间状态变化记录 `precommand_state_changed`。
3. 动作后轮询回读（超时由 `sessions.parameter_ack_timeout_seconds` 控制，默认 15 秒）：
   读值命中目标且动作前未达目标 → `app_ack_only`；读值可解析但未命中 → `timeout`；
   页面不可解析 → `failed`（`parameter_unreadable`）。滑块不可读时拒绝盲滑（不发送动作）。
4. 若剩余事件全部无法合法执行（例如目标已被达成且无其他事件可改变状态），运行器记录
   `no_legal_transition` 并以未完成会话结束；不无限等待，也不把重复操作凑成成功数。
5. 同一配置含滑块和情景事件时，先完成可执行的亮度与色温目标，再随机选择情景；情景预设
   可能接管滑块。每个情景身份至少需要两个可区分的情景目标才能往返。

### 日志字段、证据级别与会话校验

参数化事件的 `actions.jsonl` 记录在既有字段之外新增：

| 字段 | 含义 |
|---|---|
| `dimension` | 目标维度（brightness / color_temperature / scene / focus_mode） |
| `target_value` | 声明的目标值（数值、情景 ID 或布尔值） |
| `unit` / `tolerance` | 数值维度的单位与允许误差 |
| `observed_before` / `observed_after` | 动作前后页面观察：`value`、`known`、`unit`、`readback_values`、`observed_at_unix_ns`、`source`、`evidence_files` |

- 每次尝试在命令前后各保存与同一 `event_id` 关联的页面 XML 和截图（截图仅作事后审计，
  情景模式的运行判定不读取截图）
  （`screenshots/<event_id>_before.*`、`<event_id>_after.*`）；失败时的诊断保存为
  `<event_id>.*`，不会覆盖前后证据。重试使用新的 `attempt` 和唯一 `event_id`。
- 没有独立设备观测时，成功一律为 `app_ack_only`（App 页面回执，`source=vendor_app`），
  不会写 `confirmed`，也不要求 HA 日志即可验证本阶段功能行为。
- `validate-session` 对参数化记录追加检查：目标必须属于会话计划且在声明范围内；成功记录
  必须有可读的前后观察、动作前未达目标、动作后命中目标、时间戳有序、引用的证据文件存在；
  对情景事件还核对前后亮度/色温回读与预设以及 XML 文字证据；
  任何被篡改的记录（删除后读值、改成 `confirmed`、伪造目标）都会被拒绝。质量报告按事件
  身份给出 `planned_by_identity`、`completed_by_identity` 和 `results_by_identity`，六个情景
  不会被同名事件相互覆盖。

干运行与校验：

```powershell
uv run iot-exp --experiment experiment/mi_desk_lamp_1s_advanced.yaml run --dry-run --repetitions 1 --seed 42 --session-id advanced_dry_001
uv run iot-exp validate-session runs/sessions/advanced_dry_001
```

### 已核实与未核实的真机字段

以下字段来自 2026-09-24 真机（LG LM-V405，1440px 宽，米家 11.8.703）只读页面检查与最小真机
闭环（会话 `advanced_real_001`/`advanced_real_002`/`advanced_real_003`，原始页面证据
保留在本地 `runs/sessions/`，不纳入功能开发代码提交）：

- 设备页标题与开关语义："米家台灯1S 增强版"；显示"关灯"= 当前开启，"开灯"= 当前关闭；
- 亮度、色温以文字回显（如 `60%`、`5078K`），单位为 `%` 与 `K`；
- 亮度滑条实测范围 `[1, 100]`、色温滑条实测范围约 `[2600, 5100]K`（超出该范围的声明目标
  无法达到，YAML 已按实测回填）；
- 滑块轨道像素校准值 `track_start_px: 142` / `track_end_px: 1179`（由前后读值证据拟合），
  换机或 App 升级后必须重新校准；
- 六个情景按钮文字与定位：电脑模式、温馨模式、休闲模式、办公模式、阅读模式、娱乐模式
  （`我的模式` 区域，可点击 ViewGroup 含图标与文字；区域滚出屏幕时由
  `scrollIntoView` 自动滚入）；
- 专注模式开关位于"更多设置"二级设置页，`checked` 属性可读；开/关闭环在真机三次会话中
  均得到 `app_ack_only`；
- 亮度 30 与色温 4600/3200 的"滑动-回读"闭环在真机得到 `app_ack_only`（允许误差内命中）。

真机核实的限制（未完成项，按规格如实报告）：

- **情景控件不提供当前选中属性**：2026-09-25 真机逐一选择六种情景后，六组控件 XML 的
  `selected` / `checked` 均为 `false`，但亮度与色温分别变为上表六组互不混淆的预设值。
  运行时在命令前后读取这两个数值，并按声明误差匹配唯一预设；动作后匹配目标且动作前
  未匹配目标才记 `app_ack_only`。此方法基于本次工作流程无手动操作的约束；若 App 版本、
  设备固件或预设发生变化，须重新校准。它是 App 回执，不等于独立设备状态确认。
  真机最小闭环 `scene_preset_real_20260925_02` 已验证电脑模式（50%/2700K）→阅读模式
  （100%/5000K）→温馨模式（60%/3500K）；两次结果均为 `app_ack_only`，`validate-session`
  报告 `ok: true`，运行判定没有读取截图。
- **情景模式会接管亮度与色温**：点击任一情景后，滑块拖动不再改变回读值；混合会话现先执行
  亮度/色温，再执行情景，若剩余目标无法合法执行则记 `incomplete`。
- 左屏幕边缘起滑会触发系统返回手势：滑块轨道校准值已避开边缘区域，请勿在未校准的设备上
  直接使用本模板。

## 11. 常见故障

| 现象 | 处理顺序 |
|---|---|
| `adb: not found` | 安装 Platform Tools；配置 `adb_executable` 或 PATH |
| SDK 根目录无法确定 | 设置 `android_sdk_root`、`ANDROID_SDK_ROOT` 或 `ANDROID_HOME` |
| `unauthorized` | 解锁手机并确认 RSA；不要自动操作授权对话框 |
| `offline` | 重启 ADB、换数据线或 USB 口、保持亮屏 |
| 多台设备报错 | 使用 `--udid` 或有人值守时使用 `--select-device` |
| Appium 端口占用 | 为进程分配唯一 `--appium-port` 并检查残留进程 |
| UiAutomator2 端口冲突 | 为每台手机分配唯一 `--system-port` |
| 找不到页面或控件 | 运行 `inspect-app`，查看 XML/截图，更新适配器选择器 |
| 状态一直 `unknown` | 分离“状态标记”和“动作控件”，验证开/关两种页面 |
| formal preflight 失败 | 查看 `runs/preflight_latest.json`，修复前不得采集 |
| Dumpcap 启动失败 | 核对接口、权限、过滤器和磁盘空间 |
| 操作成功但不是 `confirmed` | 独立观测未启用时 `app_ack_only` 是正确结果 |
| 参数化事件一直 `timeout` | 页面读值未命中目标；核对滑块范围声明与回显文字，查看前后证据 |
| 情景事件全部 `failed` | 核对亮度、色温读值及六组 `presets` 是否仍与当前 App 一致；查看前后 XML |

## 11.1 参数化事件故障排查

| 现象 | 处理顺序 |
|---|---|
| `parameter_unreadable` | 页面结构变化导致读值失败；运行 `inspect-app` 更新读值选择器 |
| `parameter_target_timeout` | 动作已执行但目标未命中：检查 range/步进声明与真实滑块是否一致 |
| `no_legal_transition` 提前结束 | 剩余目标均已达成或不可读；核对计划目标组合（见 10.2 运行语义） |
| `parameter_unconfigured` | 事件存在但 `parameters` 未声明；补齐声明后再运行 |

## 12. 安全与数据要求

- 不把账号密码、Wi-Fi 密码、OTP、令牌或密钥写入 YAML、日志和截图说明；
- 不自动处理登录、验证码、年龄验证、RSA 授权或系统安全提示；
- 手机必须与实验 IoT 网络隔离，并关闭 USB 网络共享；
- 正式采集前必须确认目标 IP、抓包接口和过滤器；
- 保留失败证据，禁止为了“通过”而改写原始事件结果；
- 新适配器不得直接修改旧 PCAP、模型权重或 `legacy/` 数据。

实验及产物整理完成后、提交报告、数据集说明、PR 或其他实验内容前，按 [AGENTS.md §6.1](AGENTS.md) 一次性向用户确认时钟对齐是否可接受，以及 `manual_review` 是否可确认通过。两项均得到明确肯定答复后，直接将本次交付确认记为通过；原始会话/对账文件中的 `manual_review` 状态保持原样，用户确认另记于交付或派生清单。若任一项未获肯定答复，不得标记通过或提交相关内容。

## 13. 实验数据处理

会话采集、抓包质量检查和 HA 状态对账由本平台负责。PCAP/HA 数据清洗、P/U 数据集构造、特征提取和模型训练脚本已迁移至独立仓库；请在那里执行离线处理和训练流程。
