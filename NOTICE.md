# NOTICE

## Required Notice（PolyForm Noncommercial 1.0.0 要求随副本传递）

```
Required Notice: Copyright (c) 2026 谢君 (Xie Jun) — 星衍放射质控软件 / Report QC App
```

依据 `LICENSE`（PolyForm Noncommercial License 1.0.0）的 "Notices" 章节：
**任何人向他人提供本软件（或其一部分）时，必须同时提供本许可条款或其 URL，
以及上面这行 `Required Notice:`。**

## 商业许可

本软件对**非商业用途**免费（科研、教学、个人学习与试验、非营利/公共机构等，
定义见 `LICENSE`）。**商业用途需另行取得商业许可**（含医院/企业在诊疗或经营活动
中使用），请联系：stardev@xingyan-ai.com。

## 许可变更历史

- 2026-09-30 起：PolyForm Noncommercial License 1.0.0（见 `LICENSE`）
- 2026-09-30 之前的版本：MIT License（见 `LICENSE-MIT`，对已发布版本永久有效）

详见 `LICENSE-HISTORY.md`。

## 第三方开源组件

本软件包含下列开源组件（均按其各自许可使用，未捆绑商业闭源组件）：

| 组件 | 用途 | 许可 |
|---|---|---|
| FastAPI / Starlette | HTTP 服务与路由 | MIT / BSD-3-Clause |
| Uvicorn | ASGI 服务器 | BSD-3-Clause |
| SQLAlchemy | 数据访问 | MIT |
| Pydantic | 数据校验 | MIT |
| pywebview | 桌面壳（系统 WebView） | BSD-3-Clause |
| ONNX Runtime | 离线 OCR 推理 | MIT |
| pypinyin | 拼音推导（同音错字） | MIT |
| pythonnet | 屏幕取词/系统集成（Windows） | MIT |
| ReportLab / python-docx / openpyxl | 报表导出 | BSD / MIT |
| cryptography | Ed25519 授权验签 | Apache-2.0 / BSD-3-Clause |
| PyInstaller | 打包（构建期，不随产品分发） | GPL-2.0 with exception |

> 完整依赖清单见 `requirements.txt`。若需对某一组件做合规审计，
> 请以该组件自身仓库的许可文本为准。
