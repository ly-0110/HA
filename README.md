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

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\automation
uv sync --extra dev
npm install
uv run iot-exp doctor
```

旧版模型代码位于 `legacy/`，其路径配置和运行方式保持历史状态，不会被新的自动化程序自动读取。不要把新会话产物放入 `legacy/`，也不要让自动化程序直接修改旧 PCAP 或模型权重。

如需复查旧版原型，请先进入 `legacy/` 再运行旧入口：

```powershell
Set-Location C:\Users\Administrator\Desktop\HA\legacy
python main.py
```

旧版模型仍是历史基线，不属于新的跨平台自动化验收流程。
