"""
server/license_web.py
星衍放射质控 · SPA/Web 版授权管理（无 tkinter 依赖）

与 src/license_utils.py 保持同一套语义：
- 免责声明（disclaimer_accepted）
- 免费试用期 TRIAL_DAYS = 90 天，本地持久化 first_run
- 激活码：Ed25519 非对称，开发者私钥签名机器硬件标识，客户端仅用内嵌公钥验签
  （私钥留本地发卡用，公钥随前端发布，无私钥无法伪造激活码）

license 状态存于 appdata 目录下的 license.json（与 web_settings.json 同目录）。
机器码 / 验签逻辑与 Tkinter 版一致，确保同一台机器两版激活码互通。
"""
import os
import json
import hashlib
import base64
import datetime
import platform
import subprocess
import socket

TRIAL_DAYS = 90

# 与 src/license_utils.py 内嵌公钥、keys/public_key.pem 完全一致
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEACsb0q9A7E3oRfw/DNkMf1UKoxKWzeK6xP2ZaNLbpnto=
-----END PUBLIC KEY-----
"""

DISCLAIMER_TEXT = """用户协议与免责声明

欢迎使用「星衍放射质控系统」（以下简称"本软件"）。

一、使用许可
1. 本软件免费试用期为 90 天，试用期后需输入有效的激活码方可继续使用。
2. 您仅可在获得授权的情况下使用本软件。

二、免责声明
1. 本软件提供的报告质控结果仅供参考，不构成最终诊断依据。
2. 所有质控结果均需由具备资质的放射科医师进行审核和确认。
3. 开发者不对因使用本软件产生的任何直接或间接损失承担责任。
4. 本软件不替代医疗专业人员的临床判断和决策。

三、数据安全
1. 本软件质控引擎在本地运行，不向任何第三方上传患者数据。
2. 样本库数据存储在本地，请自行做好数据备份。

四、知识产权
本软件的知识产权归开发者所有。未经授权，禁止反向工程、修改或分发。

————————————————
继续使用即表示您已阅读、理解并同意上述条款。如不同意，请退出本软件。"""


# ---------------- license.json I/O ----------------

def _license_path(appdata_dir: str) -> str:
    return os.path.join(appdata_dir, "license.json")


def _read_license(appdata_dir: str) -> dict:
    try:
        with open(_license_path(appdata_dir), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_license(appdata_dir: str, data: dict) -> None:
    os.makedirs(appdata_dir, exist_ok=True)
    with open(_license_path(appdata_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ---------------- 硬件标识（激活码绑定对象） ----------------

def _stable_hw_id() -> str:
    """稳定的硬件标识：重装系统/换网卡尽量不变。
    Windows=MachineGuid / macOS=IOPlatformUUID / Linux=/etc/machine-id /
    兜底=hostname。与 src/license_utils.py 完全一致。"""
    sysname = platform.system()
    try:
        if sysname == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                return winreg.QueryValueEx(k, "MachineGuid")[0].strip()
        elif sysname == "Darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]).decode()
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[3].strip()
        else:
            with open("/etc/machine-id") as f:
                return f.read().strip()
    except Exception:
        pass
    return socket.gethostname().strip() or "UNKNOWN"


def machine_id() -> str:
    """展示用短机器码（发卡 / 客服核对用）。"""
    return hashlib.md5(_stable_hw_id().encode("utf-8")).hexdigest()[:12]


# ---------------- 试用期 ----------------

def check_trial(appdata_dir: str):
    """返回 (state, days_left)。
    state: 'activated' / 'trial' / 'expired'。"""
    lic = _read_license(appdata_dir)
    if lic.get("activated"):
        return ("activated", 0)
    first_run = lic.get("first_run")
    if not first_run:
        lic["first_run"] = datetime.date.today().isoformat()
        _write_license(appdata_dir, lic)
        return ("trial", TRIAL_DAYS)
    try:
        first = datetime.date.fromisoformat(first_run)
    except Exception:
        first = datetime.date.today()
    used = (datetime.date.today() - first).days
    if used >= TRIAL_DAYS:
        return ("expired", 0)
    return ("trial", TRIAL_DAYS - used)


# ---------------- 激活码（Ed25519） ----------------

def validate_activation_code(code: str) -> bool:
    if not code:
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        public_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)
    except Exception:
        return False
    raw = code.strip().upper().replace("-", "").replace(" ", "")
    if len(raw) % 8 != 0:
        raw += "=" * (8 - len(raw) % 8)
    try:
        sig = base64.b32decode(raw)
    except Exception:
        return False
    try:
        # 激活码绑定对象 = 展示用「机器识别码」(machine_id)，与设置页/激活框复制给用户的
        # 文本一致；开发者按用户提供的机器码发卡，避免「复制短码却签全量硬件标识」导致验签失败。
        public_key.verify(sig, machine_id().encode("utf-8"))
        return True
    except Exception:
        return False


def activate(appdata_dir: str, code: str) -> bool:
    if validate_activation_code(code):
        lic = _read_license(appdata_dir)
        lic["activated"] = True
        lic["activation_code"] = code
        _write_license(appdata_dir, lic)
        return True
    return False


# ---------------- 聚合状态 ----------------

def license_status(appdata_dir: str, account_count: int) -> dict:
    lic = _read_license(appdata_dir)
    state, days = check_trial(appdata_dir)
    return {
        "disclaimer_accepted": bool(lic.get("disclaimer_accepted")),
        "activated": bool(lic.get("activated")),
        "trial_state": state,            # activated / trial / expired
        "trial_days_left": days,
        "trial_days_total": TRIAL_DAYS,
        "machine_id": machine_id(),
        "account_count": account_count,
    }


def accept_disclaimer(appdata_dir: str) -> None:
    lic = _read_license(appdata_dir)
    lic["disclaimer_accepted"] = True
    _write_license(appdata_dir, lic)


def disclaimer_text() -> str:
    return DISCLAIMER_TEXT
