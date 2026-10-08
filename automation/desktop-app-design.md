# Electron 桌面重构方案入口

2026-10-03：已确定采用 Electron 重构工作台。完整方案建立为 OpenSpec 变更
`refactor-workbench-to-electron`，本文件作为阅读索引。当前交付为规划文档，桌面代码和安装包尚未实施。

## 阅读顺序

1. [变更提案](../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/proposal.md)：问题、范围、新增能力和兼容边界。
2. [架构设计](../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/design.md)：核心复用、路径、认证、后台生命周期、互斥与迁移。
3. [运行环境依赖](../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/runtime-dependencies.md)：内置组件、SDK/驱动准备、版本清单、许可与离线验收。
4. [实施与验收清单](../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/tasks.md)：按依赖顺序推进，每项有完成验证。

## 已确定的技术方向

Electron 承载现有 React 工作台，Python 保留实验核心。应用分发私有 CPython、独立 Node、Appium/UiAutomator2 和 JDK；SDK及抓包驱动优先复用本机或从官方准备，不要求实验者安装全局 uv/Python/Node/npm。

安装资源与可写工作区分离，GUI/CLI共享独立于输出根的设备锁；正常退出等待采集清理，崩溃不自动重跑，原始PCAP与报告不改写。本机打开直接使用原文件路径。

首版发布目标为 Windows11x64 与 Ubuntu24.04LTSx64 原生桌面。当前浏览器入口及启动器继续可用；平台和硬件能力只有通过对应安装包及真实环境验收后才能声明支持。
