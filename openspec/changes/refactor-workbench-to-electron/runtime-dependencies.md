# 运行环境与发行依赖

本文是 [design.md](design.md) 的依赖细化。2026-10-04已生成Windows Squirrel和WSL Ubuntu Deb内部测试制品；“安装后可用”仍须以目标系统实际验收为准。2026-10-06用户取消干净系统断网安装、升级/卸载深度演练、分发签名/维护和多入口并行专项验收；这些不是当前交付前置，也未计为通过。私有依赖、不在线安装的核心运行契约与组件许可边界继续保留；原生Ubuntu正式采集尚未通过。当前交付见automation/desktop/releases/windows-0.1.3，剩余任务见tasks.md。

2026-10-07用户进一步排除不必要检查：1.4以来源/版本/上游声明/既有通知、实际SBOM与默认包边界的基础审查验收，该范围已完成。全面对外分发条件证明、逐库源码缓存与重链接专项移出本轮；现有材料缺项仍如实保留。SDK采用已验证固定准备清单，不穷尽最小集合。安装资源不写入的契约保持，以正常安装运行和载荷散列核查验收，不追加Windows ACL专项。Windows已有实际证据，余下为原生Ubuntu部分。

## 1. 依赖分层与部署责任

| 组件 | 首版选择/版本来源 | 分发位置 | 由谁准备 | 未就绪时影响 |
|---|---|---|---|---|
| Electron 与 React 静态资源 | Electron 稳定版精确锁定；现有 React/Vite 构建 | 安装包 | 构建流水线 | 桌面入口不可用 |
| Python | CPython 3.13 可搬迁发行；固定资产/散列 | 私有 runtime/python | 构建流水线 | 后台/模拟不可用 |
| Python 依赖 | uv.lock 平台解析，含 iot_exp wheel/FastAPI/uvicorn/Pydantic/PyYAML/Pillow/Appium client | 私有 site-packages | 构建流水线 | 相应核心能力不可用；不得运行时 pip 修复 |
| Node | 24 LTS 的固定官方发行资产 | 私有 runtime/node | 构建流水线 | 真机 Appium 不可用；历史/模拟保持可用 |
| Appium | 当前已锁定 3.7.0 起点，独立 runtime package 精确锁定 | 私有 runtime/appium | 构建流水线 | 真机不启动 |
| UiAutomator2 | 当前已锁定 6.9.3 起点，包含完整 driver/APK/生产依赖 | 私有 runtime/appium | 构建流水线 | 真机不启动；最低 Android 8/API26 |
| Java | Temurin JDK17 LTS 固定发行 | 私有 runtime/java | 构建流水线，保留许可/legal内容 | Java 工具相关真机能力不启动 |
| Android SDK/ADB | 复用有效本机 SDK，或许可确认后的官方固定包 | 用户 SDK 或 workspace 管理 tools/sdk | 实验者/管理员通过准备向导 | 真机不可用；模拟/查看可用 |
| Dumpcap/Wireshark | Windows 官方安装；Ubuntu 发行版包 | 系统安装 | 实验者/管理员 | 正式抓包不可用；无抓包真机验证可用 |
| Npcap | Windows 官方安装，具体部署遵循发行许可 | 系统驱动 | 实验者/管理员 | Windows 正式抓包不可用 |
| USB 驱动/udev/组权限 | 实际手机与发行版要求 | 系统 | 实验者/管理员 | 对应手机不可控制 |
| 手机 App 与网络 | 已登录厂商 App、USB 授权、异网隔离 | 手机/实验网络 | 实验者 | 真实实验被预检阻止 |
| uv/npm/Vite/TypeScript/Forge | 仅开发与构建，按各锁文件固定 | 构建环境 | 维护者 | 不作为终端用户启动前提 |

Node 与 Appium 版本必须满足 Appium 3 的官方要求；npm 是构建工具，运行时使用固定 Node 与 Appium 入口。最低 Android 约束来自 driver，不等于已经验证任意手机或 App 版本。[Appium requirements](https://appium.io/docs/en/3.2/quickstart/requirements/)、[UiAutomator2 requirements](https://github.com/appium/appium-uiautomator2-driver)

## 2. 可分发运行时布局

```text
resources/
  web/                     # 已构建 React 资源
  templates/               # 版本化实验/运行模板
  runtime-manifest.json
  licenses/                # 第三方许可与 notices
  runtime/<os>-x64/
    python/                # 可搬迁解释器、完整 wheel 依赖
    node/                  # 独立 node.exe 或 bin/node
    java/                  # JDK17 与 legal/
    appium/                # 独立 package + lock + 生产依赖/APK
```

上述路径在 ASAR 外且只读。应用可写 Appium 状态、下载/准备暂存、日志与缓存属于用户状态/工作区。不分发带开发机绝对路径的 venv、editable wheel 链接、Appium extensions.yaml 或测试缓存。

首版固定采用 `appStateRoot/appium/<bundleId>/` 作为版本化可写 APPIUM_HOME。从随包的已校验driver、完整传递依赖、APK和扩展元数据模板初始化并重定位索引；该版本的driver payload保持固定，缓存/索引允许在指定子目录写入。启动时只校验和重定位，不执行npm或driver install，也不同时依赖npm项目自动发现。继承的全局APPIUM_HOME被明确替换为该私有路径。首次真实session仍可能安装手机侧APK，这是设备准备而非PC下载依赖。[Appium extension management](https://appium.io/docs/en/3.2/guides/managing-exts/)

## 3. SDK 固定准备清单与许可

- `platform-tools` 必需，提供 ADB；SDK 根必须能明确定位该目录，不能从任意独立 adb 路径推导 `/usr` 等根。
- 为已锁定 driver 准备所需的固定 Build Tools，验证 APK 签名/检查工具；所需 Platform 包与 API 等级由实际 driver doctor/工具调用验收后固定在 SDK 清单。
- Command-line Tools 只用于准备/管理 SDK，不作为打开工作台和模拟实验的必需运行依赖。
- Android Studio、Emulator、system images、NDK 和 CMake 不进入首版实机默认安装集合。
- 向导先检查现有 SDK 的路径、组件和版本；需要补包时展示官方来源、大小、许可证步骤和目标目录，由用户确认后准备，禁止自动 `yes | sdkmanager --licenses`。
- 未取得具体发行资产再分发许可前，默认安装包不内置 Google SDK 二进制。以后改变这一策略须逐组件审核，不能以 AOSP 源码许可证推定 Google 二进制发行可再分发。

此处采用官方安装/复用策略，不向用户承诺“所有 SDK 都可免费随包复制”。[Android SDK terms](https://developer.android.com/studio/terms)、[sdkmanager](https://developer.android.com/tools/sdkmanager)

固定清单按实际安装版本、driver doctor和目标手机结果记录，不逐一删除包或穷尽版本证明最小集合。Windows已记录Platform Tools36.0.0、Build Tools36.1.0，现有Platforms34/36与doctor检查使用的Emulator36.2.12；这是已验证准备记录，不宣称每个包都是实机必需。原生Ubuntu对应清单及权限待实测，按任务1.3/7.2推进。

## 4. 抓包及系统权限

Windows 由向导检测官方 Wireshark/Dumpcap 和 Npcap；安装驱动由管理员完成。默认不捆绑或静默安装 Npcap，实验室批量/OEM部署需依据相应许可准备。[Npcap license](https://github.com/nmap/npcap/blob/master/LICENSE)

Ubuntu 采用发行版 wireshark-common/Dumpcap 与必要权限配置。向导提供安装、wireshark 组和重新登录的指引，不自动扩大系统权限，不对整个 Electron/后台提权。`dumpcap -D` 只证明接口枚举，正式就绪还需要授权用户在实际接口进行短时试抓包并验证双向目标流量。[Wireshark capture privileges](https://wiki.wireshark.org/CaptureSetup/CapturePrivileges)

USB RSA、厂商 App 登录、Windows OEM USB 驱动、Linux udev 和网络隔离为独立人工准备步骤。已完成准备的机器无需每次联网安装软件；厂商云端事件仍需要实际业务网络。

## 5. 运行时解析规则

打包模式只从已校验 manifest 的私有可执行路径和用户明确配置的 SDK/抓包工具路径解析依赖，不静默回退到 PATH 上任意同名程序。源码 CLI 保留现有显式参数与环境变量解析，输出最终实际使用的路径、版本和来源。

为每个子进程单独生成环境：一致设置JAVA_HOME、ANDROID_HOME、ANDROID_SDK_ROOT、上述版本化APPIUM_HOME、私有Node路径与缓存目录；不修改全局PATH、系统Java或用户默认Python。启动Appium使用 `[nodeExecutable, appiumEntry, ...args]`，参数使用数组，不经过shell。索引只允许引用声明的私有driver目录，重定位后验证schema、bundle版本和路径边界；固定payload逐文件散列与可写缓存分别处理。

环境状态至少区分“就绪、缺失、不兼容、权限不足、校验失败”，对应 Core、Device、Capture 三类能力。正式启动仍再次验证手机、网络隔离、实际接口/过滤器、权限和空间；依赖向导显示绿色不能替代本次实验预检。

## 6. 清单与构建

`runtime-manifest.json` 为每个 OS/架构记录：schema、bundle ID、app/backend 协议版本、Python/Node/JDK/Appium/driver 精确版本、相对路径、来源 URL、资产 SHA-256、依赖锁摘要、最低系统条件、许可证位置、构建来源和验收报告引用。SDK 外部清单记录实际组件版本与来源，不保存账号、密钥、USB凭证或控制令牌。

构建顺序：校验官方/批准来源资产 → 按锁文件准备目标平台 wheel 和 Appium runtime → 准备预置 APK/扩展 → 去除测试/缓存/绝对路径 → 生成 SBOM/许可文件 → 校验散列 → 打包 → 在本机核对私有运行时、无全局工具依赖和实际能力。用户取消另建干净系统断网专项验收；本机PATH隔离和WSL结果分别记录，不推定干净系统已验收。JDK保留发行legal/NOTICE，精确对应源码已独立缓存和校验；本轮不把全面对外分发条件证明作为功能完成前置。[Temurin FAQ](https://adoptium.net/docs/faq)

安装后只校验和启动，不执行 uv sync、pip install、npm install 或 driver install。缺失/损坏的内置组件提示修复安装包；外部工具通过向导准备。离线镜像由管理员事先从合法来源准备并核验，不把未经授权的 SDK/Npcap 合并成默认公开运行包。

应用/后台/运行时版本仍由清单与握手校验；数据库事务迁移前备份，原PCAP、动作日志和报告不重写。已有安装保护和默认数据保留实现保留，升级/卸载全入口和回退演练不再属于活动验收。外部替换或移除安装包造成的任务中断按生命周期恢复契约处理，不承诺应用阻止系统管理员操作。来源、原许可声明/notices及实际SBOM保持；对外提供方式改变时再按适用许可证核对对应条件，不把当前基础审查记为全面对外分发审查。

## 7. 当前能力验收矩阵

| 目标系统 | 私有核心运行/模拟 | 无抓包真机 | 正式抓包 | 能力声明 |
|---|---|---|---|---|
| Windows 11 x64 原生桌面 | 本机验证私有依赖、无全局PATH依赖、不在线安装；安装目录普通用户检查 | 独立授权手机验证 | Npcap/Dumpcap/接口另验收 | 仅声明已通过的能力；不宣称干净系统断网专项验收通过 |
| Ubuntu 24.04 LTS x64 原生桌面 | 原生普通用户安装/运行待验收；WSL预验证独立标注 | USB/SDK/JDK/driver独立验证 | 权限、隔离及双向试抓包通过 | 仅声明已通过的能力；不以WSL推定原生通过 |
| Windows 10、Ubuntu 22.04、ARM/macOS/WSL | 另建后续矩阵 | 不从上述结果推定 | 不从上述结果推定 | 首版未验收支持 |

保留中文/空格路径、安装资源与数据分离及不写安装资源、任意 cwd、子进程版本来源、无全局 PATH 污染、原文件打开无副本、安全退出、受控进程故障恢复和基础资产来源/通知检查。共享锁与外部不可控保持已有功能契约和局部证据，不要求另外进行多入口并行完整演练。干净系统断网安装、升级/卸载深度演练、发布签名、升级回滚及操作系统强制关机专项演练均记录为用户取消；至少60秒后显式强制终止、创建身份核对和不自动重跑保持原要求，原生Ubuntu真实采集仍为活动验收。真实实验报告必须区分模拟、App 页面回执、独立观测和正式捕获，不以模拟通过证明硬件就绪。
