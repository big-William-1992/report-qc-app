#!/usr/bin/env python3
"""开发者工具：用私钥为指定机器生成激活码（Ed25519 离线方案）。

用法：
  python gen_activation_code.py              # 生成本机激活码
  python gen_activation_code.py "<机器码>"   # 为客户机器码生成
      （机器码 = 客户从激活对话框复制的「机器识别码（发卡用）」文本）

说明：
  - 需 cryptography 依赖（venv/bin/python 运行）。
  - 私钥 keys/private_key.pem 切勿提交到仓库 / 分发。
"""
import sys
import os
import base64

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import license_utils as L
from cryptography.hazmat.primitives import serialization

KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys", "private_key.pem")


def main():
    if not os.path.isfile(KEY_PATH):
        print("错误：找不到私钥 keys/private_key.pem（请勿将私钥提交到仓库）")
        sys.exit(1)
    with open(KEY_PATH, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)

    if len(sys.argv) > 1:
        hw_id = sys.argv[1].strip()
        print(f"为目标机器码生成激活码：{hw_id}")
    else:
        hw_id = L._stable_hw_id()
        print(f"为本机生成激活码（机器码：{hw_id}）")

    sig = priv.sign(hw_id.encode("utf-8"))
    b32 = base64.b32encode(sig).decode().rstrip("=")
    code = "-".join(b32[i:i + 5] for i in range(0, len(b32), 5))
    print("\n===== 激活码（发给用户） =====")
    print(code)
    print("===============================")


if __name__ == "__main__":
    main()
