# IoT 厂商 App 自动化实验平台

本目录提供一个跨平台实验框架，用真实 Android 厂商 App 触发 IoT 设备事件，并将 App 操作、状态回执、网络隔离检查、可选连续抓包和可选独立状态观测整理为可验证的实验会话。

平台核心面向复用；`米家台灯1S 增强版` 是当前第一个已经完成真机验证的适配器案例，而不是平台本身的唯一目标。当前公共事件模型是二态 `turn_on`/`turn_off`，适用于灯、插座、开关等设备。接入多状态设备或其他事件类型时，需要扩展事件模型和编排器，不能只增加选择器。

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
- 通用多厂商适配器注册表；当前 CLI 默认创建米家台灯适配器；
- 二态开关以外的通用事件模型；
- 已启用的 Home Assistant 状态提供者；
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

进入目录：

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\automation
```

Ubuntu：

```bash
cd /path/to/HA/automation
```

安装依赖：

```text
uv sync --extra dev
npm install
```

执行基础检查：

```text
uv run iot-exp doctor
uv run iot-exp devices
```

完成一个不连接真机的逻辑会话：

```text
uv run iot-exp run --dry-run --repetitions 1 --seed 42 --session-id dry_run_001
uv run iot-exp validate-session runs/sessions/dry_run_001
```

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

手机准备：

1. 开启开发者选项和 USB 调试；
2. 使用数据线连接运行平台的主机；
3. 在手机上确认 RSA 调试授权；
4. 保持解锁和充电，关闭目标 App 的电池优化和自动更新；
5. 手机连接公共 Wi-Fi 或蜂窝网络，不连接实验 IoT 网络；
6. 关闭 USB 网络共享。

列出设备和参数：

```text
uv run iot-exp devices
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

### 7.2 真机无抓包验证

```text
uv run iot-exp preflight --udid <serial>
uv run iot-exp inspect-app --udid <serial>
uv run iot-exp run --udid <serial> --repetitions 1 --session-id first_real_001
uv run iot-exp validate-session runs/sessions/first_real_001
```

最小闭环通过后再增加重复次数。二态设备的建议首轮验收是每种事件 10 次，App 成功率不低于 95%，无坐标点击，所有 `event_id` 唯一。

### 7.3 Ubuntu 正式采集

编辑正式运行配置：

- `capture_interface`：镜像口或抓包接口；
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

## 8. 多手机与混杂流量

当前并行模型是一进程一手机。每个进程必须使用唯一的 Appium 端口和 UiAutomator2 `systemPort`：

```text
uv run iot-exp run --udid SERIAL_A --phone-id phone_a --appium-port 4723 --system-port 8200 --repetitions 10
uv run iot-exp run --udid SERIAL_B --phone-id phone_b --appium-port 4725 --system-port 8201 --repetitions 10
```

每条动作记录同时包含 `phone_id` 和 `phone_udid`。多个控制进程可以共同制造背景和混杂流量，但同一镜像口的连续抓包应只由一个采集进程负责，避免重复 PCAP、接口竞争和不一致的边界。

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

## 12. 安全与数据要求

- 不把账号密码、Wi-Fi 密码、OTP、令牌或密钥写入 YAML、日志和截图说明；
- 不自动处理登录、验证码、年龄验证、RSA 授权或系统安全提示；
- 手机必须与实验 IoT 网络隔离，并关闭 USB 网络共享；
- 正式采集前必须确认目标 IP、抓包接口和过滤器；
- 保留失败证据，禁止为了“通过”而改写原始事件结果；
- 新适配器不得直接修改旧 PCAP、模型权重或 `legacy/` 数据。
