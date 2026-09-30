# 许可变更历史（License History）

本文件是**事实声明**，用于说明不同版本适用哪份许可，避免"追溯授权"争议。

| 版本范围 | 适用许可 | 说明 |
|---|---|---|
| **≤ `a535f27`（含）**，以及**此前已发布的全部 Release/安装包** | **MIT License** | 见仓库历史 `LICENSE`（现存档为 `LICENSE-MIT`）。自 2026-07-21 起公开且标注 MIT，该授权对这些版本**永久有效**，不因后续变更而收回；依 MIT 已取得的权利继续有效。 |
| **>`a535f27`（自本次变更起）** | **PolyForm Noncommercial License 1.0.0** | 见 `LICENSE`。**非商业用途免费**（科研/教学/个人学习与试验、非营利与公共机构等）；**商业用途需另行取得商业许可**（含医院/企业在诊疗或经营活动中的使用）。随副本分发时必须携带本许可或 [其 URL](https://polyformproject.org/licenses/noncommercial/1.0.0) 以及 `Required Notice:` 行（见 `NOTICE.md`）。 |

## 为什么要写这份文件

1. 权利人**可以**对未来版本采用不同许可（dual licensing / relicensing 是常见做法），
   但**不能**收回已按 MIT 授予的权利。
2. 对外说明"从哪个版本起适用哪份许可"能显著减少与用户、二次开发者之间的误解。
3. 若采用"双许可"（如非商业免费 + 商业付费），本表需列明各轨道的适用条件与联系方式。

## 已实施（2026-09-30，D2 决策 = 选项 A）

- **变更点**：commit `a535f27` 及更早 = MIT；自此之后 = PolyForm Noncommercial 1.0.0。
- **旧文本留档**：`LICENSE-MIT`（供已按 MIT 取得权利者核对来源）。
- **随副本传递**：`NOTICE.md` 提供 `Required Notice:` 行与第三方组件清单；
  CI 打包时会把 `LICENSE` / `NOTICE.md` / `docs/DISCLAIMER.md` 复制进
  `dist/报告质控软件/`，因此**绿色版 zip 与安装包都会带上许可与免责声明**。
- **对外文案已同步**：`README.md`、`docs/DISCLAIMER.md`、`TERMS_OF_SERVICE.md`、
  `docs/index.html`（落地页）均已从"MIT 免费可再分发"改为"非商业免费 / 商业需授权"。

## 仍待律师（不影响本次生效）

- 商业许可（订购协议）的**正式条款文本**：建议在现有 `TERMS_OF_SERVICE.md` 基础上定稿，
  明确"服务范围、更新与支持、医疗机构内部使用许可、禁止向第三方再分发/转售、退款"。
- 是否保留"科研/非营利机构免费"这一档的书面说明（当前由 PolyForm 的
  Noncommercial Organizations 章节覆盖，实务上建议在官网/合同里再复述一次）。
- 落地页与销售材料中"免费"一词的边界（**非商业免费**，须与商业报价区分）。

> 本文件不是法律意见；最终文本请由律师定稿。
