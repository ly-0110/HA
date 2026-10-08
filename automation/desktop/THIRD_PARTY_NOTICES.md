# 第三方组件与分发边界

本目录记录桌面制品的来源和许可保留方式。实际版本、来源URL及散列见runtime-inputs.lock.json；实际制品中的完整依赖清单见sbom.json。终端软件许可必须随其上游文件保留，不以本说明替换。

| 组件 | 来源 | 许可/随附材料 | 分发方式 |
|---|---|---|---|
| Electron/Chromium | electron/electron固定发行 | Electron LICENSE及LICENSES.chromium.html | 原材料显式复制到resources/licenses/electron，防止安装器过滤顶层通知 |
| CPython与其运行库 | astral-sh/python-build-standalone固定发行 | Python LICENSE.txt及对应发行的第三方许可/构建元数据 | 私有解释器；第三方材料随licenses保留 |
| Python wheel依赖 | uv.lock与PyPI平台wheel | 每个dist-info的METADATA/licenses | 构建阶段预置，记录完整版本 |
| Node | nodejs.org固定发行 | Node LICENSE（含其捆绑库说明） | 私有node二进制；不分发npm/npx安装入口 |
| Temurin JDK17 | adoptium固定发行 | legal/、NOTICE和对应源码获取信息 | 原发行许可目录保留 |
| Appium/UiAutomator2 | npm锁定发行 | Apache-2.0 LICENSE；传递依赖各自许可 | 私有完整运行载荷；扩展索引按平台重定位 |
| Lucide图标 | lucide-react 1.52.0锁定发行 | ISC及包内Feather来源MIT通知，完整LICENSE保留 | 前端按需构建；Workflow用于应用图标，原通知随resources/licenses/frontend/lucide-react保留 |
| Google Android SDK | Google官方或用户已安装SDK | 按具体组件官方许可准备 | 不纳入默认安装包 |
| Npcap/USB驱动 | 官方系统安装 | 适用官方发行/部署条款 | 不捆绑、不静默安装 |
| Linux Dumpcap | 发行版系统包 | 系统包许可及权限指引 | 外部系统组件 |

安装包不包含Google SDK、Npcap、OEM USB驱动或实验数据。用户许可与管理员准备步骤仍由环境向导明确提示，不自动确认系统授权。

JDK源码对应其固定发行版本，保留上游legal材料并记录官方源码发行获取方式。组件许可和适用源码提供条件须根据实际载荷核对；仅保留legal目录或一般项目链接不表示这些条件已全部满足。2026-10-06用户取消分发签名和长期维护专项验收，该取消不被记录为第三方资产许可审核通过。

## 使用范围

随包保留已有LICENSE、NOTICE和第三方材料。项目用于有限实验者；默认包不夹带Google SDK/Npcap/USB驱动或实验数据。具体对外提供方式改变时，依据相应许可证核对适用条件。过程资产核查记录按用户要求清理；原通知和构建来源清单保留。
