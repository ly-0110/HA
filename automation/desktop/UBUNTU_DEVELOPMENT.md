# Ubuntu 接续开发

目标为 Ubuntu 24.04 x64 原生桌面。Windows交付版为0.1.3，Ubuntu修复版为0.1.4；Ubuntu原生Deb安装、普通用户核心运行及现有LG手机USB权限已验证，完整SDK/driver doctor以及Electron正式开关最小闭环已通过。交付见[Ubuntu安装包](releases/ubuntu-0.1.4/README.md)。WSL结果只用于预验证。开发沿用 `codex/electron-workbench`，无需创建第二份源码或独立worktree。

## 1. 获取工作分支

在另一台 Linux 主机的本地 Linux 文件系统中执行：

```bash
git clone --branch codex/electron-workbench --single-branch https://github.com/ly-0110/HA.git
cd HA
git status --short
git log -1 --oneline
```

已有该仓库时，先保存自己的修改，再 `git fetch origin` 并切换到该工作分支；之后用 `git pull --ff-only` 更新。默认分支 `develop` 未合并本次桌面改造，直接克隆默认分支不会得到当前实现。

Git 已保存 Python 核心、React 源码及 `web/dist`、Electron 主进程/预加载、图标、实验/运行模板、构建脚本、Python/npm/Appium/发行资产锁文件和 OpenSpec 规划。私有运行时、node_modules、构建缓存、安装二进制和临时实验输出不入 Git，需要在目标 OS 重建。本机 Windows 安装 EXE 不会通过克隆获得。

## 2. 准备构建工具及私有运行时

先在 Ubuntu 桌面准备 Git、系统 Python、zstd 和 Deb 构建工具：

```bash
sudo apt-get update
sudo apt-get install git python3 zstd fakeroot dpkg
```

系统 Python 仅用于引导构建；应用的 Python、Node、Java、Appium/UiAutomator2由锁文件指定并由脚本下载/校验。无需先安装全局 uv、Node、Java 或 Appium。构建阶段需要能够访问 GitHub 发行资产、nodejs.org、PyPI 和 npm registry；终端应用启动不在线安装这些依赖。

以下命令从仓库根目录执行，并在同一个 Bash 会话中继续：

```bash
repo_root="$PWD"
build_cache="$HOME/.cache/iot-exp-build"
python3 automation/desktop/scripts/prepare_linux_build.py --output "$build_cache"

build_node_bins=( "$build_cache"/node/node-*/bin )
build_uv_files=( "$build_cache"/uv/*/uv )
build_uv="${build_uv_files[0]}"
export PATH="${build_node_bins[0]}:$PATH"
export UV_CACHE_DIR="$build_cache/uv-cache"
export npm_config_cache="$build_cache/npm-cache"

npm ci --ignore-scripts --no-audit --no-fund --prefix automation
python3 automation/desktop/scripts/prepare_linux_desktop.py --root "$build_cache"
python3 automation/desktop/scripts/collect_python_licenses.py   --resources "$build_cache/resources" --cache "$build_cache/assets"
python3 automation/desktop/scripts/build_notices.py   --resources "$build_cache/resources"   --electron-dist "$repo_root/automation/desktop/node_modules/electron/dist"
```

准备脚本直接使用当前 `automation/desktop` 源码，把其 `build-resources` 链接到缓存中的 `resources`，不会复制桌面工程，也不会自动执行图形测试。`--output` 与 `--root` 必须对应同一个缓存目录；已有实体 `build-resources` 或指向其他目录的链接时，脚本会提示先检查，避免覆盖现有资源。

首次构建使用 Git 已保存的 `web/dist`。如继续修改前端或 Python，运行下面的刷新命令，再启动或打包：

```bash
npm run web:build --prefix automation
"$build_uv" run --no-project --python 3.13 --with PyYAML==6.0.3   python automation/desktop/scripts/build_runtime.py   --uv "$build_uv" --output "$build_cache/resources"   --cache "$build_cache/assets" --backend-only
python3 automation/desktop/scripts/build_notices.py --resources "$build_cache/resources"
```

`--backend-only` 用于刷新当前应用 wheel/前端；修改发行资产或 Appium 锁文件时，重新执行完整的 `prepare_linux_build.py`，再收集通知和生成 SBOM。提交前端变更时同时提交新的 `web/dist`。每个阶段完成后及时在当前工作分支提交，推送到同一远端分支。

## 3. 启动桌面并构建 Deb

在登录的普通用户桌面会话启动：

```bash
npm start --prefix automation/desktop
```

无图形会话的 SSH 主机可以准备和构建，实际 GUI 验证须在该主机的桌面会话进行。若 Electron 报缺失共享库，可先 `ldd automation/desktop/node_modules/electron/dist/electron` 查看具体缺项，再按目标主机安装对应 Ubuntu 包。Ubuntu 24.04 使用的 ALSA 库包名为 `libasound2t64`，见 [Ubuntu 软件包说明](https://packages.ubuntu.com/en/noble/libs/libasound2t64)。不要通过关闭 Electron 沙箱或把整个应用以 root 运行来掩盖启动问题。

先安全退出应用，再执行：

```bash
npm run make --prefix automation/desktop -- --platform=linux --arch=x64 --targets=@electron-forge/maker-deb
find automation/desktop/out/make -type f -name '*.deb'
```

用上述输出中的实际 Deb 路径进行人工安装，例如 `sudo apt install ./automation/desktop/out/make/deb/x64/<实际文件名>.deb`，然后从桌面菜单启动。Deb 生成不等于原生安装验收已完成。图标和预安装脚本均随源码保存；Linux 入口的可执行权限与 LF 换行也由 Git 保存。不要提交缓存、out、node_modules 或新产生的实验会话。

## 4. 外部设备工具与验收结果

SDK/ADB、系统 USB 权限、Wireshark/Dumpcap仍需目标主机准备。在工作台环境设置中选择实际 SDK 根目录（必须含 `platform-tools/adb`）与 Dumpcap 路径。不能复制 Windows SDK、Java 或 Python 二进制给 Linux 使用。外部 SDK 准备与组件边界见 [运行环境与发行依赖](../../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/runtime-dependencies.md)。

USB 权限和手机检查沿用 [平台 README](../README.md)，其中 `install-lg-udev.sh` 仅用于所注明的 LG 型号，不可把另一台手机当作同一个设备。正式抓包须在原生目标主机确认接口、权限、网络隔离与双向目标流量；已有 App 回执不作为独立状态确认。

[OpenSpec tasks](../../openspec/changes/archive/2026-10-08-refactor-workbench-to-electron/tasks.md) 已完成37/37项；Windows保留原验收范围，Ubuntu本机完成剩余三项：

| 任务 | 原生 Ubuntu 验证结果 |
|---|---|
| 1.3 | Platform Tools37.0.1、Command-line Tools22.0、Build Tools36.1.0、Platform36 revision2、Emulator37.2.12固定清单；私有driver doctor无必需缺项 |
| 7.2 | 精确1004:631f udev、LG Android10/API29与米家11.8.703、普通用户Dumpcap权限、私有工具与正式预检通过 |
| 9.3 | 手机10.208.124.237与台灯热点10.42.0.0/24隔离，wlp2s0目标短抓包双向流量通过；安装版真实Electron界面提交正式任务，开关2/2、27包PCAP、动作/时钟/质量/会话校验及安全退出通过 |

本机SDK的版本、归档和复现边界见运行依赖固定清单。正式会话保留于 `automation/runs/ubuntu-electron-final-20261008/workspace/runs/sessions/session_20261008T144944Z_987eaef0`，不纳入安装包或Git。确认级别为App回执，人工内容复核保持待办；同主机时间记录不代表跨机HA对时或NTP专项验收。

不恢复用户已取消的边界演练，不以 WSL 预验证补勾原生任务。当前接续说明不包含过程测试输出或验收报告。正式实验数据仍按现有会话契约保存，遵守 [AGENTS.md](../AGENTS.md) 的真机操作与实验内容提交要求。
