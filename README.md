# IoT 设备事件识别工程

仓库现在分为旧研究原型和新自动化采集两部分：

```text
HA/
├─ automation/  泛用的 IoT 厂商 App 自动化实验、连续抓包和会话记录平台
├─ legacy/      原始模型代码、历史采集数据、PCAP、HA 导出和模型权重
└─ doc/         实验设计、项目报告和其他文档
```

## 推荐入口

新实验统一从 `automation/` 开始。平台使用说明见 [automation/README.md](automation/README.md)；供模型和自动化代理执行新实验接入时，另见 [automation/AGENTS.md](automation/AGENTS.md)。当前米家台灯是第一个已验证适配器案例，不代表平台只支持米家。

场地受限时，抓包主机可通过独立外网上行和 Wi-Fi 热点为 IoT 设备提供网络。正式采集的接口选择、试抓包和隔离验收见 [热点部署说明](automation/README.md#74-抓包主机提供-iot-热点)；对旧镜像拓扑的数据可比性见 [实验设计](doc/厂商App异网自动化实验设计.md#32-场地受限时抓包主机兼作实验热点)。

Windows 开发模式：

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\automation
uv sync --extra dev
npm install
uv run iot-exp doctor
```

Ubuntu 开发模式（项目本地依赖已安装时）：

```bash
cd /path/to/HA/automation
./iot-exp-local.sh doctor
./iot-exp-local.sh devices
```

Windows 默认使用 `runtime/windows-dev.yaml`；Ubuntu 本地入口使用 `runtime/ubuntu-dev.yaml`。
两者均为不抓包开发配置。Ubuntu 的 USB 权限、持久 udev 规则和真机验证步骤见
[automation/README.md](automation/README.md)。

旧版模型代码位于 `legacy/`，其路径配置和运行方式保持历史状态，不会被新的自动化程序自动读取。不要把新会话产物放入 `legacy/`，也不要让自动化程序直接修改旧 PCAP 或模型权重。

如需复查旧版原型，请先进入 `legacy/` 再运行旧入口：

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\legacy
python main.py
```

旧版模型仍是历史基线，不属于新的跨平台自动化验收流程。
