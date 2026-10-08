# 工作台图标来源

界面统一使用`lucide-react 1.52.0`，通过具名导入按需构建。应用图标的主体为Lucide的`Workflow`图形，蓝紫色圆角背景用于区分本应用；没有使用Electron默认图标或Lucide品牌标志。

官方来源：[Lucide](https://lucide.dev/)、[Workflow](https://lucide.dev/icons/workflow)、[许可](https://lucide.dev/license)。完整上游许可保存在`lucide-LICENSE`；运行制品另由build_notices.py复制到resources/licenses/frontend/lucide-react。

`workbench.svg`为矢量母版，`workbench.png`为512px应用图标，`workbench.ico`含16/24/32/48/64/128/256px尺寸。Forge将ICO写入Windows程序及Squirrel安装器；保护安装入口使用同一ICO，Linux Deb使用PNG，BrowserWindow与启动页采用相同图形。

生成命令（automation目录）：`node desktop/scripts/build_app_icon.cjs`。脚本使用锁定安装树中的Lucide/React与Sharp进行格式转换，生成文件直接纳入Git，终端用户无需图形构建工具。
