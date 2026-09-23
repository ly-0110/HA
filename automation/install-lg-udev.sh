#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo ./install-lg-udev.sh" >&2
  exit 2
fi

vendor_id="1004"
product_id="631f"
rule_path="/etc/udev/rules.d/51-android-lg.rules"
rule="SUBSYSTEM==\"usb\", ATTR{idVendor}==\"${vendor_id}\", ATTR{idProduct}==\"${product_id}\", MODE=\"0660\", GROUP=\"plugdev\", TAG+=\"uaccess\""

if command -v lsusb >/dev/null && ! lsusb -d "${vendor_id}:${product_id}" >/dev/null; then
  echo "警告：当前未检测到 LG ${vendor_id}:${product_id}，仍将安装对应规则。" >&2
fi

install -d -o root -g root -m 0755 /etc/udev/rules.d
printf '%s\n' '# LG Android phone used by the IoT experiment runner.' "$rule" >"$rule_path"
chmod 0644 "$rule_path"
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match="idVendor=${vendor_id}" --attr-match="idProduct=${product_id}"

echo "已安装 $rule_path"
echo "请重新插入手机；若当前登录会话尚未取得 plugdev 组，请注销并重新登录一次。"
