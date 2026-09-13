# 星衍放射质控软件 · 测试策略

> MetaGPT SOP · Phase 4 — QA Engineer
> 版本：v4.3.6 · 更新日期：2026-09-13
> 状态：现行策略

---

## 1. 测试概览

### 1.1 当前状态

| 指标 | 数值 |
|------|------|
| 收集测试 | 399 |
| 通过 | 389 |
| 跳过 | 10 |
| 测试目录 | `tests/` |
| 测试框架 | pytest |
| 覆盖率 | 核心引擎 + 服务端 API |
| CI 门禁 | pytest 全量 + ruff 致命规则 |

### 1.2 测试金字塔

```
                    ┌──────────────┐
                    │   E2E 测试    │  2 (Playwright)
                    │  Playwright  │  浏览器级验证
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │       集成测试            │  ~100 (pytest + FastAPI TestClient)
              │   API 端点 / 数据库       │  鉴权 / 权限 / 数据隔离
              └────────────┬────────────┘
                           │
    ┌──────────────────────┴──────────────────────┐
    │                单元测试                        │  ~290 (pytest)
    │    引擎规则 / 工具函数 / 模块逻辑               │  NER / 规则 / 词表 / 评分
    └─────────────────────────────────────────────┘
```

---

## 2. 测试分层

### 2.1 单元测试（src/）

**范围**：引擎核心逻辑、词表、工具函数

**目录**：
```
tests/
├── test_engine_zh_nlp.py          # 中文 NLP 测试
├── test_zh_ner.py                 # NER 实体识别
├── test_zh_radiology_synonyms.py  # 放射同义词
├── test_context_logic_rules.py    # 上下文逻辑规则（R12/R14/R15/R17）
├── test_radiology_errors.py       # 放射科错误检出
├── test_typo_term_improved.py     # 错别字检测
├── test_r19_homophone.py          # R19 同音错别字
├── test_r19_sensitivity.py        # R19 灵敏度
├── test_r22_lesion_size.py        # R22 测量术语
├── test_r6_site_full_abdomen.py   # R6 登记部位
├── test_badcase_store.py          # 错误案例存储
├── test_knowledge_resources.py    # 知识库资源
├── test_dataset_pipeline.py       # 数据集管线
└── ...
```

**测试约定**：
```python
class TestRuleX:
    """规则 X 的测试。"""
    
    def test_basic(self):
        """基础场景。"""
        engine = RuleEngine()
        findings = engine.run("报告文本")
        assert any(f.rule_id.startswith("RX") for f in findings)
    
    def test_false_positive(self):
        """误报场景——不应报错。"""
        engine = RuleEngine()
        findings = engine.run("正常报告文本")
        assert not any(f.rule_id.startswith("RX") for f in findings)
    
    def test_edge_case(self):
        """边界场景。"""
        ...
```

### 2.2 集成测试（server/）

**范围**：API 端点、数据库操作、鉴权、权限隔离

**目录**：
```
tests/
├── test_api_guard.py              # API 守卫（队列去重/归属隔离/RIS 配置）
├── test_auth_security.py          # 认证安全（密码策略/登录锁定）
├── test_accounts.py               # 账号管理
├── test_sample_user.py            # 样本用户隔离
├── test_samplelib_migration.py    # 样本库迁移
├── test_export_import.py          # 导出导入
├── test_license.py                # 授权验证
├── test_push_structured.py        # 结构化推送
├── test_llm_client.py             # LLM 客户端
├── test_llm_fusion.py             # LLM 融合
├── test_ocr_fix.py                # OCR 修复
├── test_ocr_meta_improve.py       # OCR 元信息
├── test_ocr_optimize.py           # OCR 优化
├── test_windows_update.py         # Windows 更新
├── test_desktop_singleton.py      # 桌面单实例
└── conftest.py                    # 测试配置（DB 隔离）
```

**测试约定**：
```python
# conftest.py 提供 DB 隔离
@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    """每个测试使用独立临时数据库。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("QC_DB_OVERRIDE", str(db_path))
    yield
    db_path.unlink(missing_ok=True)

class TestAuthGuard:
    """API 守卫测试。"""
    
    def test_queue_dedup(self, client):
        """队列入队去重。"""
        ...
    
    def test_user_data_isolation(self, client):
        """用户数据隔离——doctor 仅见自己数据。"""
        ...
```

### 2.3 E2E 测试

**范围**：浏览器级验证

**框架**：Playwright

**目录**：
```
tests/e2e/
├── test_app.py                    # 应用级 E2E
└── ...
```

**运行**：
```bash
npx playwright test
```

---

## 3. CI 门禁

### 3.1 CI 流程

```
GitHub Actions (build-windows.yml)
  │
  ├── unit-tests (ubuntu-latest)
  │   ├── pytest 全量（399 测试）
  │   └── 阻断条件：任何测试失败
  │
  ├── build (windows-latest)
  │   ├── needs: unit-tests
  │   ├── PyInstaller 打包
  │   └── 阻断条件：打包失败
  │
  └── artifacts
      ├── exe 可执行文件
      └── Inno Setup 安装包
```

### 3.2 阻断规则

| 检查 | 工具 | 阻断条件 |
|------|------|---------|
| 单元测试 | pytest | 任何测试失败 |
| 代码检查 | ruff | 致命规则（E9/F63/F7/F821/F822/F811/F401/F702/B018） |
| 依赖安全 | pip-audit | 已知 CVE 漏洞 |
| 构建 | PyInstaller | 打包失败 |
| 启动探测 | launch-test | exe 启动后 health 端点不可达 |

### 3.3 本地验证

```bash
# 完整验证（提交前必须通过）
python -m pytest -q                              # 单元测试
ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018 src/ server/  # 代码检查
python -c "from src.engine import RuleEngine; e = RuleEngine(); print('Engine OK')"  # 引擎自检
```

---

## 4. 测试覆盖范围

### 4.1 引擎规则覆盖

| 规则 | 测试文件 | 覆盖场景 |
|------|---------|---------|
| R1 | test_engine_zh_nlp.py | 性别矛盾（前列腺/子宫） |
| R2 | test_context_logic_rules.py | 左右混淆（跨段/同段） |
| R3 | test_radiology_errors.py | 评分标准缺失（BI-RADS/PI-RADS） |
| R4 | test_radiology_errors.py | 计量单位错误（HU/血压） |
| R5 | test_context_logic_rules.py | 描述-结论矛盾 |
| R6 | test_r6_site_full_abdomen.py | 登记部位不符 |
| R8 | test_typo_term_improved.py | 同音错别字 |
| R12 | test_context_logic_rules.py | 句子前后文 |
| R14 | test_context_logic_rules.py | 前后文（跨段） |
| R15 | test_context_logic_rules.py | 上下文（段内） |
| R17 | test_context_logic_rules.py | 逐部位精确比对 |
| R18 | test_radiology_errors.py | 检查完整性 |
| R19 | test_r19_homophone.py | 形近字/读音候选 |
| R22 | test_r22_lesion_size.py | 术语与测量一致性 |
| R23 | test_engine_zh_nlp.py | 繁体字检测 |

### 4.2 API 端点覆盖

| 端点 | 测试文件 | 覆盖场景 |
|------|---------|---------|
| /auth/* | test_auth_security.py | 注册/登录/锁定/改密 |
| /qc/check | test_api_guard.py | 质控请求 + 限流 |
| /samples/* | test_sample_user.py | 增删改查 + 用户隔离 |
| /queue/* | test_api_guard.py | 入队/出队/去重/归属 |
| /license/* | test_license.py | 激活/过期/伪造拦截 |
| /ocr/* | test_ocr_fix.py | OCR 上传/识别/回填 |
| /ris/* | test_api_guard.py | 配置/拉取/连接测试 |
| /admin/* | test_api_guard.py | 审计日志/错误报告 |

---

## 5. 测试数据

### 5.1 报告样本

**来源**：
- `tests/` 目录内内联样本（单元测试）
- `data/mcscset/` 公开数据集（回归评测）
- `data/processed/` 处理后样本

**约定**：
- 单元测试使用内联短样本（快、可复现）
- 回归评测使用公开数据集（大规模、统计）
- 不提交含真实患者数据的样本

### 5.2 测试夹具

```python
# conftest.py 提供的夹具
@pytest.fixture
def client():
    """FastAPI TestClient（独立 DB）。"""
    ...

@pytest.fixture
def admin_token():
    """管理员 Bearer token。"""
    ...

@pytest.fixture
def doctor_token():
    """医生 Bearer token。"""
    ...

@pytest.fixture
def sample_report():
    """示例报告文本。"""
    return "患者男性，52岁。检查所见：右肺上叶见结节..."
```

---

## 6. 性能测试

### 6.1 基准指标

| 场景 | 目标 | 实测 |
|------|------|------|
| 单份报告质控 | < 3 秒 | ~2ms（引擎） |
| 批量 50 份 | < 10 秒 | ~9ms |
| 引擎构造 | < 1 秒 | ~0ms（进程内单例） |
| OCR 单区域 | < 5 秒 | 变化检测跳过 |

### 6.2 性能测试脚本

```bash
# 引擎性能
python benchmarks/engine_bench.py

# OCR 性能
python tools/ocr_bench.py
```

---

## 7. 安全测试

### 7.1 安全测试清单

| 场景 | 验证方法 | 测试文件 |
|------|---------|---------|
| SQL 注入 | RIS 查询只读校验 | test_api_guard.py |
| XSS | HTML 转义 | test_auth_security.py |
| 路径穿越 | 导出前缀白名单 | test_api_guard.py |
| 授权绕过 | 机器指纹绑定 | test_license.py |
| 暴力破解 | 登录锁定 | test_auth_security.py |
| 弱密码 | 黑名单 + 复杂度 | test_auth_security.py |
| 数据隔离 | doctor 仅见自己 | test_sample_user.py |
| 审计覆盖 | 21 个敏感端点 | test_api_guard.py |

### 7.2 安全检查

```bash
# 依赖 CVE 扫描
pip-audit -r requirements.txt

# 硬编码密钥检查
grep -rn "password.*=.*'" --include="*.py" src/ server/ | grep -v test
grep -rn "secret.*=.*'" --include="*.py" src/ server/ | grep -v test

# 患者数据泄露检查
grep -rn "姓名.*[张王李赵]" --include="*.py" src/ server/
```

---

## 8. 回归测试

### 8.1 回归数据集

```
data/mcscset/          # 公开数据集（MCSCSet）
data/processed/        # 处理后样本
benchmarks/            # 基准测试结果
```

### 8.2 回归运行

```bash
# 全量回归
python -m pytest tests/ -q

# 仅引擎回归
python -m pytest tests/test_engine_zh_nlp.py tests/test_context_logic_rules.py tests/test_radiology_errors.py -q

# 仅 API 回归
python -m pytest tests/test_api_guard.py tests/test_auth_security.py tests/test_license.py -q
```

### 8.3 回归判定标准

| 指标 | 通过标准 |
|------|---------|
| 测试通过率 | 100%（0 失败） |
| 引擎性能 | 不低于基线的 90% |
| 误报率 | 不高于基线 |
| 漏报率 | 不高于基线 |

---

## 9. 测试维护

### 9.1 新增功能测试要求

- [ ] 新增 API 端点：至少 3 个测试（成功/失败/边界）
- [ ] 新增规则：至少 5 个测试（基础/误报/漏报/边界/极端）
- [ ] 新增前端功能：至少 1 个 Playwright E2E 测试
- [ ] 新增环境变量：至少 1 个测试覆盖配置变更

### 9.2 测试维护原则

- **先写测试再修 Bug**（TDD for bugs）
- **测试失败 = 功能回归**，必须修复
- **跳过测试（skip）需注明原因和恢复条件**
- **测试数据不使用真实患者信息**
- **测试命名清晰**：`test_<场景>_<预期结果>`
