# 医学影像报告质控系统 vX.Y.Z

> 发布说明模板：每次发版复制本模板，替换 X.Y.Z 与更新内容后，粘贴到 GitHub Release 描述。

## 更新内容
- （填写本次新增 / 修复的功能，例如：新增 R10 模板合规校验、监听命中即弹窗提醒）

## 下载
- **Windows**：`report_qc_setup_vX.Y.Z.exe`
- **macOS**：`ReportQcApp_vX.Y.Z.app`（Mac 未公证，见下方打开说明）

> 下载地址：https://github.com/你的用户名/report_qc_app/releases/tag/vX.Y.Z

## 安装与打开

### Windows
1. 双击 `report_qc_setup_vX.Y.Z.exe` 安装。
2. 首次运行若被 SmartScreen 拦截，点击「更多信息 → 仍运行」即可（软件免费开源、无签名，属正常提示）。

### macOS（未公证，需手动放行）
下载 `.app` 后若提示「无法验证开发者，无法打开」：
- **方式一**：右键点击 App → 「打开」→ 在弹窗中确认打开；
- **方式二**：终端执行 `xattr -cr /Applications/ReportQcApp.app` 后正常打开。

### 数据说明
- 软件**完全离线运行**，所有报告与样本仅存本机，不上传任何网络。
- 样本库位于：`%APPDATA%/MedicalReportQC/`（Windows）或用户目录（macOS）。

## 免责声明
本软件为科研 / 教学辅助工具，**非医疗器械**，不替代执业医师诊断。使用风险由使用者自行承担。基于 MIT License 发布。

## 反馈
误报、建议、问题请提交 GitHub Issues。
