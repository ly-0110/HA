# Ubuntu 0.1.4 安装包

修复切换页面后新建实验表单、事件目标和待启动批次丢失，以及 Ubuntu/Wayland 窗口图标关联。台灯模板按已有热点实验提供默认目标 `10.42.0.250`、接口 `wlp2s0` 和过滤器 `host 10.42.0.250`，现场网络改变时应调整，正式启动仍执行当前预检。

先从窗口正常退出旧版，再安装：

```bash
sudo apt install ./iot-experiment-workbench_0.1.4_amd64.deb
```

从应用菜单打开“**IoT 实验工作台**”，或运行 `iot-experiment-workbench`。应用保留内置 Python/Node/Java/Appium，无需手动设置 PATH。默认工作区为 `~/IoTExperiments/default`；已有工作区配置不会被安装覆盖，本机尚未配置的台灯抓包字段已备份后补齐。

已通过前端检查/测试/构建、控制台回归及 13 项真实 Electron GUI 检查，包括跨四个页面保留草稿/批次、事件编辑、模拟、正式预检阻止不合格任务和安全退出。原生 Ubuntu SDK/driver doctor 与LG手机/台灯正式开关最小闭环随后已实测通过：2/2 App回执、27包PCAP、双向目标流量和同会话校验正常。未把这些App回执提升为独立HA确认。

版本、大小、运行时版本和 SHA-256 见 `release.json` 与 `SHA256SUMS.txt`。Deb 留在本机，二进制不入 Git，交付目录不保留过程测试报告。
