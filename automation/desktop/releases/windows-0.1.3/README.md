# Windows 0.1.3 安装包

双击 **IoTExperimentWorkbench-0.1.3-Setup.exe** 安装。它是内嵌完整载荷的自解压EXE，不是需要源码目录的启动脚本；本版本没有MSI。

安装目标为Windows 11 x64，采用用户级安装。安装前在工作台完成安全停止并退出；安装后使用桌面IoTExperimentWorkbench快捷方式。

Python、Node、Java、Appium和UiAutomator2随应用提供；打开工作台、模拟和查看记录无需uv/npm/系统Python。真实手机自动化使用用户已安装的Android SDK/ADB，在环境设置点击“使用此SDK”或选择目录，并完成手机USB调试授权。抓包另需Wireshark/Dumpcap及Npcap权限。

当前安装包未签名。版本、大小、内置运行时版本及SHA-256见release.json与SHA256SUMS.txt；这些为交付清单。工作区和实验数据独立于安装目录。
