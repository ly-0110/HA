# IoT 厂商 App 自动化实验接入工作流

本文件是供代码模型、自动化代理和维护者执行任务时使用的操作规范。目标是把一个新的 Android 厂商 App + IoT 设备实验，从需求转化为可重复、可审计、可跨平台运行的自动化会话。

除非用户明确改变范围，否则在 `automation/` 内工作时遵守本文件。先阅读 `README.md`，再按本工作流执行。不要读取、迁移或修改 `legacy/` 中的旧 PCAP、数据、模型权重和历史脚本，除非用户明确要求。

## 1. 最终目标

一个实验只有同时满足以下条件，才可称为完成接入：

1. 实验对象、合法事件、前置状态、预期状态和网络边界已明确；
2. 配置能被严格解析，且不包含秘密；
3. 厂商 App 适配器能够稳定导航、读取状态、执行事件和等待回执；
4. 逻辑干运行通过，会话契约有效；
5. 真机最小闭环通过，每种事件至少成功一次；
6. 重复性验收达到实验定义的阈值；
7. 正式模式的依赖、网络隔离、抓包接口、过滤器和磁盘条件全部通过；
8. 会话产物完整、事件 ID 唯一、失败证据未被覆盖；
9. README 中增加该适配器的小节，但不把整个平台描述成单一设备项目。

## 2. 平台事实与不可假设事项

### 2.1 当前已经泛化的部分

- ADB 设备发现、参数读取和设备选择；
- Appium 服务管理与 UiAutomator2 连接；
- 实验配置和运行配置；
- 适配器协议；
- 二态事件的会话编排、日志、质量报告和校验；
- Disabled/Dumpcap 抓包后端；
- 可替换的独立状态观测协议；
- Windows 开发模式和 Ubuntu 正式模式。

### 2.2 当前仍是专用实现的部分

- `EventType` 只有 `turn_on` 和 `turn_off`；
- `DeviceState` 只有 `on`、`off` 和 `unknown`；
- CLI 当前直接创建 `MiHomeDeskLamp1SAdapter`，没有适配器注册表；
- 当前独立状态提供者是 `DisabledHaProvider`；
- 多手机并行是一进程一手机，不是单编排器统一调度。

不得仅通过文档或配置声称已支持尚未实现的事件类型、厂商、并发模型或独立确认来源。

## 3. 开始前收集的信息

尽量从仓库、设备和只读命令自动获取。只有无法可靠推断且会影响实验语义时才询问用户。

必须明确：

| 类别 | 必需信息 |
|---|---|
| App | 厂商、包名、版本、是否已登录、是否允许无重置启动 |
| 手机 | UDID 或选择规则、网络类型、Android 版本、USB 调试状态 |
| IoT 设备 | 稳定设备 ID、显示名、固件、可选 HA entity ID |
| 事件 | 事件名称、合法前置状态、预期状态、重复次数、等待时间 |
| 网络 | 禁止网段、目标设备 IP、手机与实验网隔离方式、USB 共享状态 |
| 抓包 | 平台、接口、BPF 过滤器、权限、输出空间 |
| 验收 | 最小成功率、是否要求独立状态确认、允许的恢复策略 |

不得要求用户提供账号密码、Wi-Fi 密码、OTP、访问令牌或 API 密钥写入仓库配置。

## 4. 阶段化执行流程

严格按阶段推进。后续阶段的通过不得反向掩盖前一阶段失败。

### 阶段 A：仓库与范围审计

1. 阅读根目录 `README.md`、`automation/README.md` 和本文件；
2. 检查工作树状态，保留用户已有修改；
3. 确认任务只涉及 `automation/`，除非用户明确扩大范围；
4. 识别现有实验、适配器、运行配置和测试；
5. 记录当前平台与设备限制，不做超出证据的泛化声明。

通过条件：已知道将新增或修改哪些配置、适配器、注册点、测试和文档。

### 阶段 B：实验建模

为新实验创建 `experiment/<slug>.yaml`。不要直接覆盖其他实验的语义。

至少定义：

```yaml
experiment_id: vendor_device_event_v1
phone:
  phone_id: android_phone_01
  udid: REPLACE_WITH_ADB_UDID
  network: public_wifi
  experiment_network_forbidden: true
app:
  vendor: vendor_name
  package: com.vendor.app
  version: unknown
  selectors: {}
device:
  device_id: stable_device_id
  display_name: Human readable name
  entity_id: null
  firmware: unknown
network:
  forbidden_cidrs: []
  target_device_ip: null
  require_no_usb_tethering: true
sessions:
  count: 1
  repetitions_per_event: 10
  idle_range_seconds: [1, 3]
  cooldown_seconds: 1
  max_attempts: 1
events: []
```

事件建模规则：

- 当前平台只接受 `turn_on: off -> on` 和 `turn_off: on -> off`；
- 如果新设备不能自然表示为二态，不得把多状态事件伪装成开/关；先扩展 `EventType`、`DeviceState`、验证器、编排器和测试；
- 每个事件类型在一个实验中只能出现一次，重复次数由 `sessions.repetitions_per_event` 控制；
- `device_id`、`experiment_id` 和 `phone_id` 应稳定、可机器解析；
- 固件未知时保留 `unknown` 或明确占位，不猜测。

通过条件：配置通过模型校验，事件转移与真实设备语义一致。

### 阶段 C：运行环境建模

开发与正式环境分离：

- Windows 开发配置：`mode: dev`、允许 `capture_mode: disabled`；
- Ubuntu 正式配置：`mode: formal`、必须 `capture_mode: dumpcap`、必须提供抓包接口。

Android SDK 解析顺序：

1. `runtime.android_sdk_root`；
2. `ANDROID_SDK_ROOT`；
3. `ANDROID_HOME`；
4. 解析后的 ADB 真实路径位于 `<sdk>/platform-tools/`。

不要从 `/usr/bin/adb` 机械推导 `/usr`。不能确定 SDK 根目录时停止并要求配置。

多手机并行时为每个进程分配：

- 唯一 UDID；
- 唯一 `phone_id`；
- 唯一 Appium 端口；
- 唯一 UiAutomator2 `systemPort`；
- 唯一会话 ID。

同一个镜像口只允许一个连续抓包所有者。

通过条件：`doctor` 能找到 ADB、SDK、Java、Node、Appium；正式模式还能找到 Dumpcap 和有效接口。

### 阶段 D：实现厂商 App 适配器

适配器必须满足 `VendorAppAdapter`：

```text
launch_and_open_device()
read_state() -> DeviceState
perform_event(event_type)
wait_for_ack(expected_state, timeout_seconds) -> AckEvidence
recover_navigation()
capture_diagnostics(destination, event_id)
close()
```

实现原则：

1. 厂商页面细节只放在适配器和实验选择器中，不泄漏到编排器；
2. `launch_and_open_device` 必须幂等：已在设备页时直接继续，否则从稳定入口导航；
3. `read_state` 不得通过上一次命令推断，必须读取当前页面证据；
4. 动作控件和状态标记应分离；按钮文字可能表示“将要执行的动作”，不是当前状态；
5. `wait_for_ack` 必须在超时内轮询真实状态，并返回观测时间；
6. `recover_navigation` 是失败后的有限恢复，不得无限重试；
7. 失败诊断至少保存页面 XML 和截图；
8. `close` 必须可靠释放 Appium 会话；
9. `no_reset` 可保留登录状态，但不能自动登录或输入秘密。

新增适配器后，还必须修改 CLI 的适配器创建逻辑，或先实现显式适配器注册表。仅创建 Python 文件不会使 CLI 自动使用它。

通过条件：适配器可在假驱动或最小测试中验证选择器映射与状态逻辑，且真机 `inspect-app` 能进入目标页并读取状态。

### 阶段 E：选择器发现

选择器优先级：

1. 稳定资源 ID；
2. 稳定无障碍描述；
3. 稳定可见文字；
4. Android UIAutomator；
5. XPath；
6. 设备专用坐标，仅作为最后降级方案。

流程：

1. 运行 `inspect-app --udid <serial>`；
2. 如果失败，读取 `runs/inspect/inspect_failure.xml` 和 `.png`；
3. 区分页面标记、状态标记、动作控件和弹窗控件；
4. 更新实验 YAML；
5. 重复执行，分别验证所有可达状态；
6. App 升级后重新检查选择器。

不得因为一次截图中的坐标有效就把坐标写入通用编排器。

通过条件：页面导航和状态读取连续成功，且选择器来源可解释。

### 阶段 F：必要测试

测试策略遵循“覆盖新增决策和高风险边界，不为行数而测试”。

必须考虑的测试：

- 新配置字段、枚举或状态转移的验证；
- 新选择器策略映射；
- 纯函数解析，例如 ADB 设备列表、SDK 路径和设备选择；
- 编排器新增分支、事件 ID 唯一性和失败分类；
- 新后端的启动/停止命令构造；
- 干运行会话契约。

通常不需要的测试：

- 对 Pydantic、标准库或 Appium 自身行为的重复测试；
- 只验证常量存在的测试；
- 依赖真实账号、真实云服务或真实网络的单元测试；
- 为每一行简单转发代码创建 mock。

基本命令：

```text
uv run ruff check src tests
uv run pytest
```

通过条件：静态检查通过，必要测试通过，测试不要求秘密或真机。

### 阶段 G：依赖与设备检查

执行：

```text
uv run iot-exp doctor
uv run iot-exp devices
```

设备选择规则：

- 自动化和无人值守任务使用 `--udid`；
- 人工开发可以使用 `--select-device`；
- 只有一台在线手机且配置 UDID 为占位值时才自动选择；
- 多台手机在线而目标不明确时必须停止；
- `unauthorized` 要求用户在手机上确认 RSA；
- 不自动操作登录、安全或系统授权对话框。

`devices` 输出应确认：UDID、状态、型号、Android 版本、SDK level、目标 App 版本和运行端口。

通过条件：目标手机状态为 `device`，目标 App 已安装，参数已显示。

### 阶段 H：逻辑干运行

执行：

```text
uv run iot-exp run --dry-run --repetitions 1 --seed 42 --session-id dry_run_<slug>
uv run iot-exp validate-session runs/sessions/dry_run_<slug>
```

检查：

- 每种事件都有记录；
- `event_id` 唯一；
- 没有开放事件；
- `quality_report.json` 存在；
- 禁用抓包时不伪造 PCAP；
- 日志中没有秘密。

通过条件：校验结果 `ok: true`。

### 阶段 I：网络预检

执行：

```text
uv run iot-exp preflight --udid <serial>
```

确认：

- 所有 ADB shell 命令绑定所选 UDID；
- 手机地址不在 `forbidden_cidrs`；
- 手机不能访问实验设备 IP；
- USB 网络共享关闭；
- 正式抓包接口和磁盘条件可用；
- 输出中的 `selected_phone`、`app` 和 `automation_runtime` 正确。

开发模式可以报告不影响开发的失败，但正式模式任一必要检查失败都必须终止。

通过条件：正式采集要求 `ok: true`。

### 阶段 J：真机最小闭环

先运行：

```text
uv run iot-exp inspect-app --udid <serial>
```

确认当前状态后，使用新会话执行每种事件一次：

```text
uv run iot-exp run --udid <serial> --repetitions 1 --session-id first_real_<slug>_001
uv run iot-exp validate-session runs/sessions/first_real_<slug>_001
```

检查 `actions.jsonl`：

- `state_before` 与事件前置状态一致；
- `expected_state` 正确；
- `state_after` 来自页面回执；
- `phone_id` 和 `phone_udid` 正确；
- `app_version` 和固件字段可追溯；
- 没有重复 `event_id`；
- 没有坐标点击，除非实验明确接受设备专用降级。

失败会话必须保留。修复后创建新的会话 ID，不得覆盖原记录。

通过条件：每种事件至少成功一次，会话校验通过。

### 阶段 K：重复性验收

在不抓包或低风险配置下逐步增加重复次数：

```text
uv run iot-exp run --udid <serial> --repetitions 10
```

默认二态设备建议：

- 每种事件至少 10 次；
- App 成功率不低于 95%；
- 无重复事件 ID；
- 无未关闭事件；
- 无无法解释的 `unknown` 状态；
- 恢复流程没有掩盖持续性选择器失败。

用户定义的验收阈值优先于默认建议。

通过条件：`quality_report.json` 达到阈值，失败样本有诊断证据。

### 阶段 L：正式连续抓包

只有前述阶段全部通过后才启用正式模式。

执行前确认：

- 手机仍在公共网络，不在实验 IoT 网络；
- `target_device_ip` 是真实目标；
- `capture_filter` 不含占位值；
- `capture_interface` 是镜像口或指定接口；
- Dumpcap 权限和磁盘空间充足；
- 会话前后保护时间符合实验设计；
- 同一接口只有一个抓包所有者。

执行：

```text
uv run iot-exp --runtime runtime/ubuntu-lab.yaml doctor
uv run iot-exp --runtime runtime/ubuntu-lab.yaml preflight --udid <serial>
uv run iot-exp --runtime runtime/ubuntu-lab.yaml run --udid <serial> --repetitions <N>
```

通过条件：连续 PCAP、动作日志、时钟信息、网络检查和质量报告来自同一会话。

## 5. 多手机和混杂流量工作流

当前支持一进程一手机：

```text
Process A: --udid SERIAL_A --phone-id phone_a --appium-port 4723 --system-port 8200
Process B: --udid SERIAL_B --phone-id phone_b --appium-port 4725 --system-port 8201
```

执行规则：

1. 先用 `devices` 获取所有在线手机；
2. 为每台手机分配唯一逻辑 ID 和端口；
3. 每个控制进程使用唯一会话 ID；
4. 先分别完成单手机最小闭环；
5. 再并行启动控制进程；
6. 同一镜像口仅保留一个 Dumpcap 进程；
7. 使用 `phone_udid`、事件时间戳和 `clock_sync.json` 与共享 PCAP 对齐；
8. 记录每个控制进程的随机种子和启动时间。

如果需要统一事件顺序、全局随机化或严格避免跨进程时钟偏差，应停止扩展独立进程方案，改为实现单编排器、多适配器会话和单抓包后端。

## 6. 证据与交付清单

每次新增实验或适配器，交付报告至少包括：

- 新增/修改的实验配置；
- 新增/修改的运行配置；
- 适配器和 CLI 注册点；
- 选择器来源与状态语义；
- 实际设备与 App 版本；
- 静态检查和必要测试结果；
- 干运行会话 ID 与校验结果；
- 真机最小闭环会话 ID 与质量结果；
- 重复性验收结果；
- 正式预检状态；
- 尚未填写的固件、IP、接口或观测提供者；
- 已知限制和下一步。

不要只报告“命令成功”。必须引用 `actions.jsonl`、`quality_report.json`、预检报告或诊断文件中的具体证据。

## 7. 必须停止并询问用户的情况

出现以下任一情况时，不得猜测或绕过：

- 需要登录、密码、OTP、CAPTCHA、年龄验证或系统安全授权；
- 无法判断要控制哪台手机或哪个 IoT 设备；
- 事件语义不是二态且用户尚未定义期望模型；
- 手机网络是否与实验网隔离不明确；
- 正式目标 IP、抓包接口或过滤器不明确；
- 操作可能影响非实验设备；
- 页面出现固件升级、恢复出厂、删除设备、共享权限或支付等高影响动作；
- 用户已有修改与任务所需修改发生不可安全合并的冲突；
- 连续三次重复同一阻塞且没有新的安全诊断路径。

询问时说明已经确认的事实、具体阻塞和需要用户做出的最小选择。

## 8. 禁止事项

- 不自动输入账号密码、Wi-Fi 密码、OTP、令牌或密钥；
- 不自动确认 RSA、安全、隐私或系统权限对话框；
- 不绕过登录、验证码、证书或网络安全警告；
- 不为追求成功率改写原始动作记录或 PCAP；
- 不复用失败会话的事件 ID；
- 不让多个正式进程对同一接口各自启动 Dumpcap；
- 不把厂商选择器写进通用编排器；
- 不在缺乏证据时把 `app_ack_only` 标记为 `confirmed`；
- 不把新会话产物写入 `legacy/`；
- 不用坐标替代本可获得的稳定资源 ID、无障碍描述或文字选择器。

## 9. 快速命令清单

```text
# 安装
uv sync --extra dev
npm install

# 质量检查
uv run ruff check src tests
uv run pytest

# 环境与设备
uv run iot-exp doctor
uv run iot-exp devices

# 干运行
uv run iot-exp run --dry-run --repetitions 1 --seed 42 --session-id dry_run_001
uv run iot-exp validate-session runs/sessions/dry_run_001

# 真机只读检查
uv run iot-exp preflight --udid <serial>
uv run iot-exp inspect-app --udid <serial>

# 真机最小闭环
uv run iot-exp run --udid <serial> --repetitions 1 --session-id first_real_001
uv run iot-exp validate-session runs/sessions/first_real_001

# 正式采集
uv run iot-exp --runtime runtime/ubuntu-lab.yaml doctor
uv run iot-exp --runtime runtime/ubuntu-lab.yaml preflight --udid <serial>
uv run iot-exp --runtime runtime/ubuntu-lab.yaml run --udid <serial> --repetitions 20
```

## 10. 文档维护规则

新增适配器后：

1. 保持 README 的平台定位，不把标题改成单一厂商或设备；
2. 在“当前已验证适配器”下增加独立小节；
3. 记录包名、已验证版本、状态语义、选择器策略、验证范围和未完成项；
4. 若平台契约、命令、产物或安全边界变化，同步更新本文件；
5. 文档中只写已实现或明确标为计划的能力。

## 11. 图形控制台约束

- 控制台仅绑定本机回环地址；不要为方便访问而改为监听所有网卡；
- `/api/v1/tasks` 只接受模板标识和允许覆盖的实验参数，不接受任意命令或任意文件路径；
- 图形任务和 CLI 真实运行必须共同持有手机、IoT 设备和抓包接口资源锁；
- 一个图形任务对应一个独立工作进程和一个会话，最多并行四个任务；
- 模拟任务不占用真实设备资源；正式任务缺少目标 IP、接口或过滤器时必须拒绝；
- 强制终止必须限定在任务进程树内，并把任务标记为 `interrupted`，不得标记为完成；
- 修改前端后必须执行 `npm run web:build`，提交构建后的 `web/dist/`，保证双击入口无需开发服务器。
