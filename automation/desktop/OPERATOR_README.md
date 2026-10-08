# IoT 实验工作台

连接 Android 手机，通过厂商 App 操作 IoT 设备，记录实验动作、页面回执和网络流量。

## 功能

- 米家台灯：开关、亮度、色温、情景模式和专注模式。
- 小爱触屏音箱：音乐播放与暂停。
- 编辑事件目标、执行次数和等待时间，组成实验批次。
- 模拟练习、真机验证和正式抓包三种运行方式。
- 实时查看进度与日志，查阅历史实验、截图、页面 XML 和报告。
- 通过本机应用打开 PCAP 和其他实验文件。

## 下载与安装

从 [0.1.5 发布页面](https://github.com/ly-0110/HA/releases/tag/v0.1.5) 的 **Assets** 下载对应安装包。

| 系统 | 安装包 |
|---|---|
| Windows 11 x64 | `IoTExperimentWorkbench-0.1.5-Setup.exe` |
| Ubuntu 24.04 x64 桌面版 | `iot-experiment-workbench_0.1.5_amd64.deb` |

Windows：双击 EXE，按安装提示完成安装，然后打开桌面上的 **IoTExperimentWorkbench** 快捷方式（IoT 实验工作台）。

Ubuntu：在安装包所在目录打开终端，执行：

```bash
sudo apt install ./iot-experiment-workbench_0.1.5_amd64.deb
```

安装后从应用菜单打开 **IoT 实验工作台**。升级时先在工作台结束当前实验并关闭窗口，再安装新版本。

Python、Node、Java、Appium 和 UiAutomator2 已包含在安装包内。打开应用、模拟实验和查看记录可直接使用。

## 第一次使用前的准备

### Android 手机与 SDK

1. 在电脑上准备 Android SDK。已有 SDK 可以直接复用；新安装可使用 [Android 官方命令行工具](https://developer.android.com/tools)。
2. SDK 组件使用以下清单：Platform Tools（ADB）、Build Tools 36.1.0、Android Platform 36，以及 Emulator。已验证的 Emulator 版本为 Windows 36.2.12、Ubuntu 37.2.12；只需准备工具，实机实验不需要创建虚拟设备或下载系统镜像。
3. 在工作台的 **环境设置** 中选择 SDK 根目录；该目录包含 `platform-tools/adb` 或 `adb.exe`。
4. 手机开启 USB 调试，用数据线连接电脑，在手机上确认调试授权。
5. 在手机上打开米家，登录账号并添加实验设备。保持手机解锁。
6. 在 **设备概览** 中刷新，选择显示为已连接的手机。

Windows 根据手机型号安装 USB 驱动。Ubuntu 为手机配置 udev/USB 访问权限；连接后用环境设置和设备概览确认手机在线。

### 正式抓包

Windows 安装 [Wireshark](https://www.wireshark.org/download.html) 和安装向导中的 Npcap。Ubuntu 安装抓包工具并为当前用户启用权限：

```bash
sudo apt install wireshark-common tcpdump
sudo dpkg-reconfigure wireshark-common
sudo usermod -aG wireshark "$USER"
```

配置时选择允许普通用户抓包，重新登录后生效。在工作台 **环境设置** 中选择 Dumpcap 程序。

将 IoT 设备接入实验网络，手机连接另一网络，并关闭手机 USB 网络共享。抓包电脑使用能看到 IoT 设备流量的网卡；电脑提供实验热点时，选择热点网卡。

在 **新建实验 → 正式采集** 中填写：

- 目标设备 IP：IoT 设备在实验网络上的地址。
- 抓包接口：本机用于采集设备流量的网卡。
- 抓包过滤器：通常为 `host <目标设备IP>`。

台灯模板预填 `10.42.0.250`、`wlp2s0` 和 `host 10.42.0.250`，按现场地址及网卡名称修改。

## 开始一次实验

1. 打开 **新建实验**，选择设备模板。
2. 选择运行方式：先用 **模拟运行** 熟悉流程；**真机验证** 操作手机；**正式采集** 同时记录网络流量。
3. 选择手机，编辑事件目标与每个目标的执行次数。等待时间、随机种子和抓包保护时间在 **高级配置** 中调整。
4. 点击 **检查配置**，按页面提示补齐准备；点击 **启动实验**。多个计划可先 **加入批次**，再统一启动。
5. 在 **运行中心** 查看进度和日志，使用停止按钮结束任务，等待清理完成。
6. 在 **实验记录** 查看结果、预览证据和打开 PCAP。

切换页面会保留本次编辑的表单、事件目标和待启动批次。

## 文件保存位置

默认工作区是用户目录下的 `IoTExperiments/default`。环境设置可以切换工作区或登记已有实验目录。

每次实验保存到工作区的 `runs/sessions/<会话编号>/`，主要文件包括：

| 文件 | 内容 |
|---|---|
| `session.yaml` | 本次实验配置 |
| `actions.jsonl` | 动作时间、目标和 App 回读结果 |
| `traffic.pcapng` | 正式采集的连续网络流量 |
| `quality_report.json` | 完成数量和结果统计 |
| `clock_sync.json` | 实验主机时间记录 |
| `network_isolation_check.json` | 本次网络预检结果 |
| `screenshots/` | 页面截图和 XML |

备份实验时复制整个会话目录。应用升级后继续使用原工作区。
