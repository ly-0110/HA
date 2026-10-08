# Electron 实验工作台

复用React界面和Python实验核心。Windows交付版为0.1.3，安装包在[releases/windows-0.1.3](releases/windows-0.1.3/README.md)：双击自解压EXE人工安装，无需源码环境；当前没有MSI。

## 使用

安装前安全退出工作台。安装后从桌面快捷方式启动；默认工作区在用户目录IoTExperiments/default，环境设置可选择工作区、登记历史目录和配置外部工具。

Python、Node、Java、Appium/UiAutomator2均为私有运行时，不要求全局uv/npm/Python。SDK/ADB为外部工具，可复用已检测SDK；Wireshark/Dumpcap/Npcap和USB授权按实际需要准备。工具准备不能替代每次正式实验的网络、设备、接口与权限预检。

## 图标与材料

界面使用锁定lucide-react，应用图形使用Workflow，SVG/PNG/多尺寸ICO与来源见[assets](assets/README.md)。内置组件来源/散列固定在runtime-inputs.lock.json，已有上游通知随制品保留，分发边界见THIRD_PARTY_NOTICES.md。

## 开发与构建

开发在主项目目录的codex/electron-workbench分支进行，完成阶段及时提交。源码和web/dist保留版本控制；测试输出、临时构建资源和安装二进制不入Git。原生Ubuntu依赖、权限、安装运行和正式采集仍按OpenSpec任务执行。

从automation目录准备开发环境后执行：

```text
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix desktop
npm run web:build
python desktop/scripts/prepare_windows_build.py
python desktop/scripts/build_runtime.py --uv <构建机uv完整路径>
python desktop/scripts/build_notices.py
python desktop/scripts/collect_python_licenses.py
npm run make --prefix desktop
```

固定版本资产由维护者在构建阶段准备。终端应用启动不在线安装依赖。重建图标使用node desktop/scripts/build_app_icon.cjs，预生成图标已纳入Git。

Ubuntu在自己的Linux文件系统克隆目录继续开发。完整准备、启动、Deb构建与剩余任务见[Ubuntu接续开发](UBUNTU_DEVELOPMENT.md)；脚本复用当前源码，不另复制桌面工程。WSL仅用于预验证。
