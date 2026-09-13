# 星衍放射质控软件 · 开发指南

> MetaGPT SOP · Phase 3 — Project Manager
> 版本：v4.3.6 · 更新日期：2026-09-13
> 状态：现行规范

---

## 1. 开发环境

### 1.1 前置要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+（推荐 3.11+） | 后端 + 引擎 |
| pip | 最新 | 依赖管理 |
| Git | 最新 | 版本控制 |
| Node.js | 18+（可选） | Playwright E2E 测试 |
| ruff | 最新 | 代码检查 |
| pytest | 最新 | 单元测试 |

### 1.2 安装步骤

```bash
# 1. 克隆仓库
git clone git@github.com:big-William-1992/report-qc-app.git
cd report-qc-app

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate  # Windows

# 3. 安装依赖（锁定版本）
pip install --upgrade pip
pip install -r requirements.txt -c constraints.txt

# 4. 安装开发依赖
pip install ruff pytest

# 5. 启动开发服务
uvicorn server.main:app --port 8000 --reload

# 6. 或启动桌面客户端
python desktop_app.py
```

### 1.3 验证安装

```bash
# 引擎自检
python -c "import sys; sys.path.insert(0,'src'); from engine import RuleEngine; print('OK')"

# OCR 自检
python -c "import sys; sys.path.insert(0,'src'); import ocr_provider; print(ocr_provider.availability())"

# 运行测试
python -m pytest -q

# 代码检查
ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018 src/ server/
```

---

## 2. 代码组织

### 2.1 目录结构

```
report-qc-app/
├── src/                     # 核心引擎（零第三方依赖为主）
│   ├── engine.py            # 引擎入口
│   ├── engine/              # 引擎子模块（Mixin 模式）
│   ├── assets/              # 词表 JSON、OCR 模型
│   ├── paths.py             # 资源路径解析（frozen 双模式）
│   └── ...
├── server/                  # FastAPI 后端
│   ├── main.py              # 应用入口 + 路由注册
│   ├── models.py            # ORM 模型
│   ├── schemas.py           # Pydantic 模型
│   ├── deps.py              # 依赖注入
│   ├── security.py          # 鉴权
│   └── routes/              # 路由模块（按领域拆分）
├── web/static/              # SPA 前端
│   ├── index.html           # SPA 入口
│   ├── css/style.css        # 样式
│   └── js/
│       ├── app.bundle.js    # 打包产物（部署用）
│       └── modules/         # 模块源文件（开发用）
├── tests/                   # 单元测试（pytest）
├── tools/                   # 工具脚本
├── build/                   # 打包配置（PyInstaller spec）
├── assets/                  # 运行时资源（词表/配置/数据库）
├── data/                    # 数据集（mcscset 等）
├── adapters/                # LoRA adapter 权重
├── train/                   # 训练脚本
├── benchmarks/              # 基准测试
├── docs/                    # 接口文档、安装指南
├── .github/workflows/       # CI 配置
├── requirements.txt         # 依赖声明
├── constraints.txt          # 版本锁定
├── .ruff.toml               # ruff 配置
├── pyproject.toml           # 项目元数据
├── README.md                # 项目总览
├── PRD.md                   # 产品需求文档
├── ARCHITECTURE.md          # 系统架构设计
├── DEVELOPMENT_GUIDE.md     # 本文件
├── TESTING_STRATEGY.md      # 测试策略
├── RELEASE_CHECKLIST.md     # 发布清单
├── CHANGELOG.md             # 变更日志
└── USER_GUIDE.md            # 用户指南
```

### 2.2 模块职责边界

| 模块 | 可以依赖 | 不可以依赖 |
|------|---------|-----------|
| `src/engine/` | 标准库、`src/_lexicons.py`、`src/anatomy_lexicon.py` | `server/`、`web/`、`fastapi` |
| `src/` | 标准库、`src/engine/` | `server/`、`web/` |
| `server/` | `src/`、`fastapi`、`sqlalchemy` | `web/` |
| `web/` | 无（纯前端） | `src/`、`server/` |
| `tests/` | `src/`、`server/` | — |

**原则**：依赖方向从上层到下层，不允许反向依赖。

---

## 3. 编码规范

### 3.1 Python

#### 3.1.1 基本规则

```python
# ✅ 正确
from src.engine import RuleEngine

class ReportProcessor:
    """处理报告文本的质控。"""
    
    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
    
    def process(self, text: str) -> list[Finding]:
        """对报告文本运行质控规则。"""
        ...

# ❌ 错误：不使用裸 except
except Exception:
    pass

# ❌ 错误：不使用 print 调试
print("debug:", x)

# ❌ 错误：不使用全局可变状态
_cache = {}  # 模块级可变字典
```

#### 3.1.2 类型标注

```python
# ✅ 所有公开函数必须有类型标注
def run_qc(text: str, meta: dict | None = None) -> list[Finding]:
    ...

# ✅ 使用现代语法（Python 3.10+）
def get_items(items: list[dict] | None = None) -> list[dict]:
    return items or []

# ❌ 不使用 Optional/Union（3.10+ 用 |）
def foo(x: Optional[str]) -> None:  # ❌
    ...
```

#### 3.1.3 错误处理

```python
# ✅ 捕获具体异常，记录日志
def report_exception(exc: BaseException, where: str = "") -> None:
    try:
        _log_error(exc, where)
    except Exception as e:
        logger.warning("Failed to log error", extra={"cause": str(e)})

# ✅ 异常摘要不泄露患者数据
def _safe_summary(exc: BaseException, max_len: int = 500) -> str:
    msg = str(exc)
    for label in ("姓名", "患者", "patient", "name=", "patient_id="):
        idx = msg.find(label)
        if idx >= 0:
            msg = msg[:idx] + "..."
    return msg[:max_len]
```

#### 3.1.4 文件操作

```python
# ✅ 使用 with 语句
with open(path, "r", encoding="utf-8") as f:
    data = f.read()

# ✅ 原子写入
def atomic_write(path: str, content: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)

# ✅ 资源路径统一通过 paths.py
from paths import data_dir, log_user_dir
```

### 3.2 JavaScript

#### 3.2.1 基本规则

```javascript
// ✅ 所有函数定义后用 Object.assign(window, ...) 导出
Object.assign(window, {
    openSettings,
    closeSettings,
    // ...
});

// ✅ 使用 apiFetch 而非裸 fetch
async function loadData() {
    const data = await apiFetch('/api/v1/samples/list');
    // ...
}

// ✅ HTML 转义防 XSS
function escapeHtml(text) {
    return text.replace(/[&<>"']/g, (c) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    })[c]);
}
```

#### 3.2.2 前端模块开发流程

```
1. 在 web/static/js/modules/ 创建或修改模块源文件
2. 在模块末尾用 Object.assign(window, {...}) 导出函数
3. 用 Python 脚本重新打包 app.bundle.js
4. 在 index.html 中更新 ?v= 缓存版本号
5. 验证 file:// 和 http:// 两种协议下均正常
```

**打包脚本**：
```bash
python tools/rebuild_bundle.py
```

### 3.3 CSS

```css
/* ✅ 使用 CSS 变量定义主题色 */
:root {
    --bg-primary: #1a1a2e;
    --text-primary: #e0e0e0;
    --text-muted: #888;
    --border: #333;
    --accent: #4a9eff;
    --danger: #e74c3c;
    --warning: #f39c12;
    --info: #3498db;
}

/* ✅ 严重度配色统一 */
.severity-high { color: var(--danger); }
.severity-medium { color: var(--warning); }
.severity-low { color: var(--info); }
```

---

## 4. Git 工作流

### 4.1 分支策略

```
main          ← 生产分支，始终可发布
  ├── feature/xxx   ← 功能开发
  ├── fix/xxx       ← Bug 修复
  └── docs/xxx      ← 文档更新
```

### 4.2 提交规范

```
<类型>: <简短描述>

类型：
  feat     新功能
  fix      Bug 修复
  docs     文档
  refactor 重构
  test     测试
  chore    构建/工具
  perf     性能优化
```

示例：
```
feat: add user feedback submission endpoint
fix: correct R17 lateral contradiction false negative
docs: update CHANGELOG for v4.3.6
```

### 4.3 提交检查

每次提交前确保：

```bash
# 1. 测试全绿
python -m pytest -q

# 2. Ruff 致命规则全绿
ruff check --select E9,F63,F7,F821,F822,F811,F401,F702,B018 src/ server/

# 3. 无患者数据泄露
grep -r "患者姓名" --include="*.py" src/ server/  # 应为空（脱敏逻辑）
```

### 4.4 合并检查清单

- [ ] CI 通过（pytest + build + launch-test）
- [ ] 新增/修改代码有对应测试
- [ ] CHANGELOG.md 已更新
- [ ] USER_GUIDE.md 已更新（如影响用户行为）
- [ ] 新增环境变量已记录
- [ ] 无硬编码密钥/密码
- [ ] 无患者数据硬编码

---

## 5. 开发流程

### 5.1 新功能开发

```
1. 需求分析 → PRD.md 添加功能需求 + 验收标准
2. 架构设计 → ARCHITECTURE.md 更新（如涉及架构变更）
3. 实现开发 → 编写代码 + 单元测试
4. 自测验证 → pytest + ruff + 手动验证
5. 文档更新 → CHANGELOG.md + USER_GUIDE.md
6. 提交推送 → git commit + git push
7. CI 验证 → GitHub Actions 全绿
8. 发布 → 见 RELEASE_CHECKLIST.md
```

### 5.2 Bug 修复

```
1. 复现 → 编写失败测试
2. 定位 → 最小化复现
3. 修复 → 最小改动
4. 验证 → 测试通过 + 无回归
5. 提交 → git commit -m "fix: ..."
```

### 5.3 规则引擎修改

```
1. 修改词表（assets/lexicons/*.json）或规则代码（src/engine/rules_*.py）
2. 添加/更新测试用例（tests/test_*.py）
3. 运行引擎回归测试：
   python -m pytest tests/test_radiology_errors.py -q
   python -m pytest tests/test_context_logic_rules.py -q
4. 验证误报率未上升
5. 更新 CHANGELOG.md
```

### 5.4 前端修改

```
1. 修改 web/static/js/modules/*.js
2. 重新打包：python tools/rebuild_bundle.py
3. 更新 index.html 缓存版本号：?v=YYYYMMDD
4. 验证两种加载模式：
   - file:// 双击 index.html
   - http:// uvicorn 启动后访问
5. 如有 UI 变更，更新 USER_GUIDE.md 截图说明
```

---

## 6. 常见问题

### 6.1 前端加载问题

**症状**：`file://` 模式下功能不工作

**原因**：ES 模块被 CORS 拦截

**解决**：确保使用经典脚本 bundle（`app.bundle.js`），不使用 `type="module"`

### 6.2 数据库锁定

**症状**：`database is locked`

**解决**：
```python
# server/db.py 已配置
connect_args={"timeout": 30}  # busy_timeout 30s
# 或启用 WAL
conn.execute("PRAGMA journal_mode=WAL")
```

### 6.3 OCR 不可用

**症状**：OCR 功能报依赖缺失

**解决**：
```bash
pip install rapidocr-onnxruntime Pillow numpy onnxruntime \
    opencv-python-headless pyclipper Shapely PyYAML six
```

### 6.4 Windows 打包

**症状**：PyInstaller 打包后 exe 启动报错

**解决**：
- 检查 `build/report_qc.spec` 的 `hiddenimports`
- 确保 `pythonnet` 和 `clr-loader` 版本正确
- Windows 需安装 Edge WebView2 运行时

### 6.5 测试环境污染

**症状**：本地测试通过，CI 失败

**解决**：
- 使用 `QC_DB_OVERRIDE` 环境变量隔离测试数据库
- 不要在测试中依赖导入顺序
- 检查 `conftest.py` 的数据库隔离

---

## 7. 工具链

| 工具 | 用途 | 命令 |
|------|------|------|
| ruff | 代码检查 | `ruff check src/ server/` |
| ruff format | 代码格式化 | `ruff format src/ server/` |
| pytest | 单元测试 | `python -m pytest -q` |
| pytest --cov | 覆盖率 | `python -m pytest --cov=src --cov=server` |
| pip-audit | 依赖 CVE 扫描 | `pip-audit -r requirements.txt` |
| Playwright | E2E 测试 | `npx playwright test` |
