# 待决策清单（需要人/外部机构决定，不是工程任务）

> 本文汇总本轮审计与三份专项文档（合规差距、临床验证、交付硬化、定价打包）中
> **只能由创始人/律师/临床/院内信息科拍板**的事项。工程侧能做的已尽量前置，
> 但这些决定不定，后续工作会返工。

| ID | 待决事项 | 为什么必须现在定 | 需要谁 | 关联文档 |
|---|---|---|---|---|
| ~~D1~~ | ~~产品监管定性~~ → **✅ 已定（2026-09-30）：实验性质** | 已写入 README 与 DISCLAIMER：实验性软件、非医疗器械、不得用于临床诊断；**边界条件**：一旦进真实临床工作流（纳入质控台账/参与签发），须重新评估定性并完成注册/测评。副作用：与 D3 收费需在合同中明确"卖的是服务与更新，非医疗质量担保" | 创始人已决 | [DISCLAIMER.md](DISCLAIMER.md)、[COMPLIANCE_GAP_ANALYSIS.md](COMPLIANCE_GAP_ANALYSIS.md) §2 |
| ~~D2~~ | ~~许可模式~~ → **✅ 已定（2026-09-30）：选项 A — 改专有/源可见许可** | 已实施：`LICENSE` 改为 **PolyForm Noncommercial 1.0.0**（非商业免费、商业需授权）；旧 MIT 文本存档为 `LICENSE-MIT`；`LICENSE-HISTORY.md` 记录分界点（≤`a535f27` 为 MIT，之后为 PolyForm）；`NOTICE.md` 提供 `Required Notice:`；README/DISCLAIMER/ToS/落地页口径已统一；CI 会把许可与免责复制进发布物。**仍需律师**：商业许可（订购协议）正式条款定稿 | 创始人已决 | [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md)、[LICENSE](../LICENSE)、[LICENSE-HISTORY.md](../LICENSE-HISTORY.md) |
| **D3** | **定价结构**：现文档写 ¥59/年（单机口径），远低于医院采购的正常区间 | 价格锚定不可逆；且要覆盖实施/售后成本 | 创始人 | [PRICING_AND_PACKAGING.md](PRICING_AND_PACKAGING.md) §2、§4 |
| **D4** | **等保义务与等级**：是否测评、测几级、由谁整改 | 义务主体通常是医疗机构；厂商需提供可测评的产品与材料，**不要对外承诺"我们做了等保三级"** | 院内信息科 + 测评机构 | COMPLIANCE_GAP_ANALYSIS §4 |
| **D5** | **备份加密策略**：内置备份为明文 SQLite（含完整报告正文） | 命中等保"数据保密性"与 PIPL；`deploy/backup.sh` 已支持 age/gpg/7z，需决定是否强制 | 院内信息科 + 创始人 | [DELIVERY_HARDENING.md](DELIVERY_HARDENING.md) §5 |
| **D6** | **云端 LLM 是否允许**：默认本地，但配置后报告正文会外发 | 属数据出境/第三方处理范畴；若允许须写进隐私政策与院内审批 | 创始人 + 院内合规 | PRIVACY_POLICY、COMPLIANCE_GAP_ANALYSIS §5 |
| **D7** | **语义评测集由谁标注、多少例** | LLM 层的价值目前**无证据**；不标注就无法回答"要不要保留 LLM" | 放射科医师 + 统计 | [EVAL_SET_GUIDE.md](EVAL_SET_GUIDE.md)、[CLINICAL_VALIDATION_PLAN.md](CLINICAL_VALIDATION_PLAN.md) |
| **D8** | **是否提供无头服务模式**（影响 Windows 服务化方式） | 打包 exe 目前是桌面壳；做服务需要单独的启动入口 | 创始人 | DELIVERY_HARDENING 附录 A |
| **D9** | **PostgreSQL 切换阈值**：单库 SQLite 能撑多少并发/多大规模 | 多科室并发写入时 SQLite 会成为瓶颈；切换需要数据迁移与运维支持 | 创始人 + 院内信息科 | DELIVERY_HARDENING §10 |
| **D10** | **`server/main.py` 拆分是否按计划执行** | 89 个端点、2567 行；不定则并行开发继续互相踩 | 创始人（排期） | [ROUTES_SPLIT_PLAN.md](ROUTES_SPLIT_PLAN.md) |
| **D11** | **医院/公立机构为何仍需付费？** → **✅ 路线已定（2026-09-30）：路线 A —— 维持 PolyForm，靠"服务 + 正式授权"收费**（不改为自订许可） | 背景：PolyForm 的「Noncommercial Organizations」把 *public safety or health organization* 与 *government institution* 列为免费档（**不论经费来源**）→ 公立医院可能据此主张免费使用，故**不能**主张"医院商用即侵权"。已按路线 A 写成拟稿：订阅 = 书面商业授权（排除口径争议）+ 正式版本更新与安全修复 + 支持/实施/验收材料 + 可入账可审计凭证。**仍须律师确认**：① 该写法是否足够；② 是否需在 `NOTICE.md`/协议中声明"诊疗活动中的使用视为商业用途"及其在 PolyForm 下的效力。**未采用路线 B**（自订许可/保留所有权利：能硬性排除医院免费档，但失去标准文本，且需重写 LICENSE 与全部对外文案） | 创始人已定路线 A；措辞待律师 | [COMMERCIAL_LICENSE_TERMS.md](COMMERCIAL_LICENSE_TERMS.md) §0 Q1、[LICENSE_OPTIONS.md](LICENSE_OPTIONS.md) |

## 已完成的决策（留档）

- **D1 = 实验性质**（2026-09-30）：实验性软件、非医疗器械、不得用于临床诊断；
  若进真实临床工作流须重新评估监管定性。
- **D2 = 选项 A（PolyForm Noncommercial 1.0.0）**（2026-09-30）：
  非商业免费、商业需授权，与激活码/订阅制自洽；旧版本 MIT 永久有效（已留档）。
- **D11 = 路线 A（维持 PolyForm，靠"服务 + 正式授权"收费）**（2026-09-30）：
  不改为自订许可；拟稿见 `COMMERCIAL_LICENSE_TERMS.md`，措辞待律师。

## 工程侧已就绪、只等决定的支撑

- **合规**：合规差距表（含等保逐控制点现状、PHI 分级与红线、待问询清单）。
- **临床**：可执行的研究方案（样本量公式、双人盲法 + 仲裁、统计方法、图表清单）。
- **交付**：nginx/WinSW/systemd/备份脚本模板 + 上线检查清单 + 回滚预案。
- **商务**：4 档打包 + 价格带 + 报价单模板 + 红线话术。
- **工程**：语义评测集工具链（模板/校验/打分）、迁移框架与升级演练、覆盖率与静默异常门禁、
  统一自检入口 `scripts/check_all.sh`。
