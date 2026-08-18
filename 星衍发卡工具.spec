# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gen_activation_gui.py'],
    pathex=['src'],
    binaries=[],
    # 2026-08-18 M7 修复：禁止把 keys/private_key.pem 打入分发物——
    # 此前发卡工具打包内含私钥，拿到工具者即可为任意机器生成有效激活码（授权体系失守）。
    # 发卡工具只带公钥（验签/展示用）；私钥仅存 keys/ 本地，签名须在受控环境进行。
    datas=[('keys/public_key.pem', 'keys'), ('src/license_utils.py', 'src')],
    hiddenimports=['license_utils', 'cryptography'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='星衍发卡工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='星衍发卡工具',
)
app = BUNDLE(
    coll,
    name='星衍发卡工具.app',
    icon=None,
    bundle_identifier=None,
)
