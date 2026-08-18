/**
 * 星衍AI放射质控 · Web 版前端逻辑
 * SPA 路由 / API 交互 / 结果渲染
 */

// ==================== SPA 页面切换 ====================
// 全局应用设置（从 /api/v1/settings 载入，影响 OCR/入库/自动化行为）
let APP_SETTINGS = {
  emp_id: 'demo01', default_modality: '', auto_qc_on_ocr: true, auto_enqueue: true,
  ocr_min_score: 0.55, screen_refresh_on_ocr: false, anonymize: false, theme: 'light',
  ocr_dynamic: true,   // 动态语义识别（整屏OCR按标题切分，PACS滚动不变形）
  ocr_silent: false,   // 静默模式：一键识别质控完成后不强制弹窗，保持后台运行
  // 默认 Windows 风 Ctrl+；设置页可逐条重绑（保存后持久化到 web_settings.json）
  shortcuts: {
    run_qc:       { mods: ['ctrl'], key: 'Enter' },
    save_sample:  { mods: ['ctrl'], key: 's' },
    paste_split:  { mods: ['ctrl', 'shift'], key: 'v' },
    ocr_capture:  { mods: ['ctrl', 'shift'], key: 'o' },
    toggle_theme: { mods: ['ctrl'], key: 't' },
  },
};

// ==================== 账号 / 授权上下文 ====================
// 登录态在 localStorage 持久化：刷新页面不必重复登录
let LICENSE_STATUS = null;   // 最近一次授权状态聚合

// 统一 API 封装：自动附加鉴权头（授权相关请求使用；业务请求沿用原 fetch 不受影响）
// 严重度元数据：图标 + 文字（色盲可用）
// 窄屏（≤1024px）侧边栏抽屉：展开/收起 + 遮罩
// ==================== 角色 UI 适配 + 用户管理 ====================
async function loadUsers() {
  try {
    const res = await apiFetch('/api/v1/accounts');
    const d = await res.json();
    const users = d.data || [];
    // 拉取科室列表填充下拉
    let depts = [];
    try {
      const dr = await apiFetch('/api/v1/departments');
      const dd = await dr.json();
      depts = dd.data || [];
    } catch (e) { console.warn('load departments failed', e); }
    const tbody = document.getElementById('usersBody');
    tbody.innerHTML = users.map(u => {
      const opts = ['<option value="">-- 未分配 --</option>']
        .concat(depts.map(dp =>
          `<option value="${dp.id}" ${u.dept_id === dp.id ? 'selected' : ''}>${escapeHtml(dp.name)}</option>`))
        .join('');
      return `
      <tr>
        <td>${escapeHtml(u.emp_id)}</td>
        <td>${escapeHtml(u.name || '--')}</td>
        <td>
          <select onchange="changeUserRole('${escapeHtml(u.emp_id)}', this.value)" ${u.emp_id === AUTH.empId ? 'disabled' : ''}>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>管理员</option>
            <option value="doctor" ${u.role === 'doctor' ? 'selected' : ''}>医生</option>
          </select>
        </td>
        <td>
          <select onchange="changeUserDept('${escapeHtml(u.emp_id)}', this.value ? parseInt(this.value, 10) : null)">
            ${opts}
          </select>
        </td>
        <td style="white-space:nowrap;">
          <button class="btn btn-outline btn-sm" onclick="resetUserPwd('${escapeHtml(u.emp_id)}')">🔑 重置密码</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) { console.error(e); }
}

async function changeUserRole(empId, role) {
  if (!confirm(`确认将 ${empId} 的角色改为 ${role === 'admin' ? '管理员' : '医生'}？`)) return;
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/role', {
    method: 'POST', body: JSON.stringify({ role })
  });
  const d = await res.json();
  toast(d.ok ? '角色已更新' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

async function changeUserDept(empId, deptId) {
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/dept', {
    method: 'POST', body: JSON.stringify({ dept_id: deptId })
  });
  const d = await res.json();
  toast(d.ok ? '科室已更新' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

async function resetUserPwd(empId) {
  const pw = prompt('输入新密码（至少 6 位）：');
  if (!pw || pw.length < 6) { toast('密码至少 6 位', 'error'); return; }
  const res = await apiFetch('/api/v1/accounts/' + encodeURIComponent(empId) + '/password', {
    method: 'POST', body: JSON.stringify({ password: pw })
  });
  const d = await res.json();
  toast(d.ok ? '密码已重置' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
}

async function addDepartment() {
  const name = prompt('输入新科室名称：');
  if (!name || !name.trim()) return;
  const res = await apiFetch('/api/v1/departments', {
    method: 'POST', body: JSON.stringify({ name: name.trim() })
  });
  const d = await res.json();
  toast(d.ok ? '科室已创建' : ('失败：' + (d.message || '')), d.ok ? 'success' : 'error');
  if (d.ok) loadUsers();
}

function gotoPage(pageName) {
  switchPage(pageName, document.querySelector(`.nav-cell[data-page="${pageName}"]`));
}

// ==================== Toast 通知 ====================
// ==================== 字数统计 ====================
document.getElementById('findingsText').addEventListener('input', function() {
  document.getElementById('findingsCount').textContent = this.value.length + ' 字';
});
document.getElementById('impressionText').addEventListener('input', function() {
  document.getElementById('impressionCount').textContent = this.value.length + ' 字';
});

// ==================== 粘贴即分栏 ====================
// 在描述/诊断输入框粘贴时，若剪贴板是"含诊断标题的整段报告"，自动分栏填入两框并质控。
// 只在满足以下条件时接管：内容包含诊断标题关键词且该标题不在行首（说明是整段全文）。
function _pasteAutoSplit(target, pasted) {
  const t = (pasted || '').trim();
  if (!t) return false;
  // 仅当明显是"描述+诊断"整段报告时才接管，避免打断普通粘贴
  const impTitle = /(?:^|\n)\s*(?:[（(]?\d+[)）]?[.、．]?\s*)?(影像诊断|诊断印象|影像结论|诊断意见|诊断结论|结论|印象|impression|conclusion)\s*[:：]?\s*(?=[\s\S]*)/i;
  const m = t.match(impTitle);
  if (!m || m.index <= 0) return false;
  const { findings, impression } = splitReportSections(t);
  const other = target.id === 'findingsText' ? 'impressionText' : 'findingsText';
  target.value = target.id === 'findingsText' ? findings : impression;
  document.getElementById(other).value = target.id === 'findingsText' ? impression : findings;
  // 刷新字数
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = document.getElementById('findingsText').value.length + ' 字';
  if (ic) ic.textContent = document.getElementById('impressionText').value.length + ' 字';
  toast('检测到整段报告，已自动分栏填位', 'success');
  setTimeout(() => { if (typeof runQC === 'function') runQC(); }, 60);
  return true;
}

['findingsText', 'impressionText'].forEach(function(id) {
  const el = document.getElementById(id);
  el.addEventListener('paste', function(e) {
    // 浏览器默认 paste 前拦截：取剪贴板文本判断是否整段报告
    const raw = (e.clipboardData || {}).getData ? e.clipboardData.getData('text') : '';
    if (raw && _pasteAutoSplit(el, raw)) {
      e.preventDefault();   // 已接管分栏，阻止默认粘贴
    }
  });
});

// ==================== 报告质控：运行引擎 ====================
// 侧别取值：优先取独立下拉框；为空时从「项目/检查部位」自由文本派生，
// 让后台左右侧比对规则（R2-LATERALITY 跨段 + R17 逐部位精确比对）在常见录入方式下都能启动。
function effectiveLaterality() {
  const lat = (document.getElementById('mLaterality') || {}).value || '';
  if (lat.trim()) return lat.trim();
  const site = (document.getElementById('mSite') || {}).value || '';
  if (/双|两|左右/.test(site)) return '双侧';
  if (/左/.test(site) && !/右/.test(site)) return '左';
  if (/右/.test(site) && !/左/.test(site)) return '右';
  return '';
}

async function runQC() {
  // 输入框仅质控页存在；剪贴板/热键后台触发时若未切页则为 null，须保护
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) { toast('请先在质控页输入描述/结论', 'warn'); return false; }
  const findings = fEl.value.trim();
  const impression = iEl.value.trim();

  if (!findings && !impression) {
    toast('请输入影像描述或结论文本', 'error');
    return false;
  }

  // 显示加载状态（元素可能不存在：剪贴板/热键触发时若未切到质控页，跳过而不崩）
  // 关键：不能覆盖 findingListContainer 的 innerHTML——那会销毁其子元素
  // findingEmpty/findingList，导致后续 _renderFindingList 里 getElementById
  // 找不到 ul、loading 永远不消失（一直转圈）。只切换子元素显示。
  const fe = document.getElementById('findingEmpty');
  const fl = document.getElementById('findingList');
  if (fe) fe.style.display = 'none';
  if (fl) {
    fl.style.display = 'block';
    fl.innerHTML = '<li class="loading-row"><div class="spinner"></div>正在运行质控引擎...</li>';
  }

  try {
    const report = [findings, impression].filter(Boolean).join('\n');
    const res = await apiFetch('/api/v1/qc/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        report,
        meta: {
          patient:    document.getElementById('mPatient').value,
          gender:     document.getElementById('mGender').value,
          age:        document.getElementById('mAge').value,
          modality:   document.getElementById('mModality').value,
          applied_site: document.getElementById('mSite').value,
          laterality: effectiveLaterality(),
          user_id:    (document.getElementById('mUser') || {}).value || APP_SETTINGS.emp_id,
        }
      })
    });

    const data = await res.json();

    if (!data.ok) {
      throw new Error(data.message || '引擎执行失败');
    }

    // FastAPI 返回 score 为中文维度键、findings 用 error_type；映射为前端期望结构
    const scoreMap = { '准确性': 'accuracy', '完整性': 'completeness', '规范性': 'normalization', '及时性': 'timeliness' };
    const scoreObj = data.data.score || {};
    const scores = {};
    for (const [k, v] of Object.entries(scoreObj)) scores[scoreMap[k] || k] = v;
    const qcFindings = (data.data.findings || []).map(f => ({ ...f, category: f.error_type }));
    renderQCResult({ findings: qcFindings, scores });
    return true;

  } catch (err) {
    console.error(err);
    _showQcError(err);
    return false;   // 返回失败标志，调用方（ocrOneClick）据此停止后续入库
  }
}

// 可操作的错误提示：区分网络/后端/引擎错误，给出排查建议
function _showQcError(err) {
  const msg = (err && err.message) || String(err);
  let hint = '';
  let icon = '⚠️';
  if (/fetch|Failed to fetch|NetworkError|网络|connect/i.test(msg)) {
    icon = '🌐';
    hint = '无法连接质控服务。请确认：<br>① 后端服务已启动（桌面版双击启动器）<br>② 网络正常，端口未被占用';
  } else if (/500|500 Internal|service error/i.test(msg)) {
    icon = '🔧';
    hint = '后端处理出错（500）。请查看同目录 crash.log 获取详细堆栈，或重启服务后重试。';
  } else if (/401|403|未授权|无权限/i.test(msg)) {
    icon = '🔐';
    hint = '鉴权失败。请重新登录后重试；若持续出现请联系管理员检查账号权限。';
  } else if (/timeout|超时|timed out/i.test(msg)) {
    icon = '⏱';
    hint = '请求超时。可能是报告过长或引擎繁忙，请稍后重试或精简报告内容。';
  } else if (/empty|为空|无文本/i.test(msg)) {
    icon = '📝';
    hint = '未识别到有效文本，请在「影像描述 / 影像诊断」输入框填写内容。';
  } else {
    hint = '发生未知错误，请截图反馈给管理员，并附上操作步骤。';
  }
  // 错误也显示在 findingList(ul) 内，保持 findingEmpty/findingList 子元素结构
  // （不覆盖 findingListContainer.innerHTML，否则下次 runQC 找不到 ul 又转圈）
  const errBox = document.getElementById('findingList');
  const emptyEl = document.getElementById('findingEmpty');
  if (emptyEl) emptyEl.style.display = 'none';
  if (errBox) {
    errBox.style.display = 'block';
    errBox.innerHTML =
      `<li class="error-row"><div class="empty-icon">${icon}</div>` +
      `<p><b>质控运行出错</b></p><p style="font-size:12px;color:var(--text-muted)">${hint}</p>` +
      `<p style="font-size:11px;color:var(--text-muted);opacity:.7">${escapeHtml(msg)}</p></li>`;
  }
  toast('质控运行出错: ' + msg, 'error');
}

let _qcAllFindings = [];        // 最近一次质控全部发现（供严重度筛选）
let _qcSevFilter = 'all';       // 当前严重度筛选
let _qcAnnoText = '';           // 原文标注：报告全文（描述+结论拼接，与 span 对齐）
let _qcAnnoFindings = [];       // 原文标注用 finding（含 span）

function renderQCResult(data) {
  const { findings, scores } = data;
  _qcAllFindings = findings || [];
  _qcSevFilter = 'all';

  // 保存原文标注数据：报告原文 = 描述 + 结论（与 /api/v1/qc/check 的 report 拼接一致）
  const fEl2 = document.getElementById('findingsText');
  const iEl2 = document.getElementById('impressionText');
  _qcAnnoText = [fEl2 && fEl2.value, iEl2 && iEl2.value].filter(Boolean).join('\n');
  _qcAnnoFindings = (_qcAllFindings || []).filter(f => Array.isArray(f.span) && f.span.length === 2 && f.span[1] > f.span[0]);

  // 渲染评分
  renderScore('scoreAcc', 'accuracy', scores.accuracy || 0);
  renderScore('scoreComp', 'completeness', scores.completeness || 0);
  renderScore('scoreNorm', 'normalization', scores.normalization || 0);
  renderScore('scoreTime', 'timeliness', scores.timeliness || 0);

  // 渲染发现列表（按严重度筛选后）
  _renderFindingList();
  // 同步刷新原文标注视图（若当前在标注页）
  renderAnnotatedText();
}

// 质控发现卡片：问题列表 / 原文标注 两个 tab 切换
function showQcTab(tab) {
  const listBox = document.getElementById('findingList');
  const anno = document.getElementById('annoView');
  const empty = document.getElementById('findingEmpty');
  const btnL = document.getElementById('qctabList');
  const btnA = document.getElementById('qctabAnno');
  if (btnL) btnL.classList.toggle('active', tab === 'list');
  if (btnA) btnA.classList.toggle('active', tab === 'anno');
  if (tab === 'anno') {
    renderAnnotatedText();   // 每次进入都刷新
    if (listBox) listBox.style.display = 'none';
    if (empty) empty.style.display = 'none';
    if (anno) anno.style.display = 'block';
  } else {
    if (anno) anno.style.display = 'none';
    _renderFindingList();    // 恢复列表视图
  }
}

// 原文标注：把发现按 span 高亮在原文对应位置（红=严重/橙=警告/灰=提示）
function renderAnnotatedText() {
  const box = document.getElementById('annoView');
  if (!box) return;
  const text = _qcAnnoText;
  if (!text) { box.innerHTML = ''; return; }
  const fds = _qcAnnoFindings;
  if (!fds.length) {
    box.innerHTML = '<div class="empty-state" style="padding:16px"><div class="empty-icon">✅</div><p>未发现需要标注的问题</p></div>';
    return;
  }
  // 按 span 收集高亮片段（span 相对全文，可直接切）
  const marks = fds.map(f => ({
    s: f.span[0], e: f.span[1], sev: f.severity || 'low', msg: f.message || ''
  })).sort((a, b) => a.s - b.s);
  // 去重叠：后面的标记不覆盖已标记区间（取更严重者优先——已排序则保留先出现）
  let html = '', pos = 0;
  const sevCls = { high: 'anno-danger', medium: 'anno-warning', low: 'anno-info' };
  for (const m of marks) {
    const s = Math.max(pos, m.s), e = Math.max(s, m.e);
    if (s > text.length) break;
    html += escapeHtml(text.slice(pos, s));
    const seg = text.slice(s, e);
    if (seg) {
      html += `<mark class="anno-mark ${sevCls[m.sev] || 'anno-info'}" title="${escapeHtml(m.msg)}">${escapeHtml(seg)}</mark>`;
    }
    pos = e;
  }
  html += escapeHtml(text.slice(pos));
  box.innerHTML =
    `<div class="anno-legend"><span class="anno-legend-item"><i class="anno-swatch anno-danger"></i>严重</span>` +
    `<span class="anno-legend-item"><i class="anno-swatch anno-warning"></i>警告</span>` +
    `<span class="anno-legend-item"><i class="anno-swatch anno-info"></i>提示</span></div>` +
    `<div class="anno-text">${html}</div>`;
}

function _renderFindingList() {
  // 这些元素仅在质控页存在；剪贴板/热键在非质控页触发时可能为 null，须保护
  const listEl = document.getElementById('findingList');
  const countEl = document.getElementById('findingCount');
  const filterBar = document.getElementById('findingFilterBar');
  const listContainer = document.getElementById('findingListContainer');

  const findings = _qcAllFindings;
  const emptyEl = document.getElementById('findingEmpty');
  // 空结果：显示空态（findingEmpty），隐藏 ul——不再覆盖容器 innerHTML
  if (!findings || findings.length === 0) {
    if (emptyEl) emptyEl.style.display = 'block';
    if (listEl) listEl.style.display = 'none';
    if (countEl) countEl.textContent = '0 条';
    if (filterBar) filterBar.style.display = 'none';
    return;
  }

  const filtered = _qcSevFilter === 'all'
    ? findings
    : findings.filter(f => (f.severity || 'low') === _qcSevFilter);

  if (filterBar) filterBar.style.display = 'flex';
  if (countEl) countEl.textContent = filtered.length + ' / ' + findings.length + ' 条';
  if (!listEl) return;   // 非质控页：渲染结果留给下次进入质控页时展示
  if (emptyEl) emptyEl.style.display = 'none';
  // 遍历全量 findings 保证 i 为 _qcAllFindings 全局索引（供 applyFindingFix 定位）；
  // 未通过严重度筛选的项用 display:none 隐藏（不影响按钮回调）。
  listEl.innerHTML = findings.map((f, i) => {
    if (_qcSevFilter !== 'all' && (f.severity || 'low') !== _qcSevFilter) return '';
    const m = SEV_META[f.severity] || SEV_META.low;
    // 确定性错别字（R8 词典 / R19 读音推导）：提供「应用修正」——直接把 suggestion 替换进
    // 报告文本（span 定位）并重新质控；同时保留「记入词库」——写入规则库供下次自动识别。
    // 非错别字规则：若引擎给 suggestion 则展示「建议修正」供人工参考；否则不提供按钮。
    const isTypo = f.rule_id === 'R8-TYPO' || f.rule_id === 'R19-HOMOPHONE';
    const hasSug = !!f.suggestion && f.suggestion !== f.snippet;
    let fixBtns = '';
    if (isTypo && hasSug) {
      fixBtns = `
        <button class="btn btn-xs apply-fix-btn" onclick="applyFindingFix(${i})"
          title="把报告中的「${escapeHtml(f.snippet || '')}」改为「${escapeHtml(f.suggestion || '')}」并重新质控">✏️ 应用修正</button>
        <button class="btn btn-xs learn-btn" onclick="learnTypoFromFinding(this)"
          data-wrong="${escapeHtml(f.snippet || '')}" data-correct="${escapeHtml(f.suggestion || '')}"
          title="把「${escapeHtml(f.snippet || '')}」→「${escapeHtml(f.suggestion || '')}」写入规则库，以后自动识别">📚 记入词库</button>`;
    } else if (hasSug) {
      fixBtns = `<span class="sug-text" title="建议修正文本">建议：${escapeHtml(f.suggestion)}</span>`;
    }
    return `
    <li class="finding-item">
      <span class="severity-dot ${f.severity}"></span>
      <span class="sev-badge ${m.cls}">${m.icon} ${m.label}</span>
      <div>
        <div class="finding-text ${m.cls}">${escapeHtml(f.message)}</div>
        <div class="finding-meta">${f.rule_id} · ${escapeHtml(f.category || '')}${fixBtns}</div>
      </div>
    </li>`;
  }).join('');

  listEl.style.display = 'block';
}

// 严重度筛选：high / medium / low / all
function setSevFilter(sev) {
  _qcSevFilter = sev;
  document.querySelectorAll('.sev-filter').forEach(b =>
    b.classList.toggle('active', b.dataset.sev === sev));
  _renderFindingList();
}

function renderScore(elId, key, value) {
  const el = document.getElementById(elId);
  // 非质控页（剪贴板/热键后台触发）时 score 元素不存在，直接跳过渲染
  if (!el) return;
  const pct = Math.min(100, Math.max(0, value));
  let cls, label;
  if (pct >= 90) { cls = 'excellent'; label = '优秀'; }
  else if (pct >= 70) { cls = 'good'; label = '良好'; }
  else if (pct >= 50) { cls = 'fair'; label = '一般'; }
  else { cls = 'poor'; label = '待改进'; }

  el.textContent = value.toFixed(1);
  el.className = 'score-val ' + cls;

  // 更新进度条
  const bar = el.closest('.score-item').querySelector('.score-bar-fill');
  if (bar) {
    bar.style.width = pct + '%';
    bar.className = 'score-bar-fill ' + cls;
  }
}

// ==================== 清空输入 ====================
function clearInput() {
  document.getElementById('findingsText').value = '';
  document.getElementById('impressionText').value = '';
  document.getElementById('findingsCount').textContent = '0 字';
  document.getElementById('impressionCount').textContent = '0 字';

  // 重置评分
  ['scoreAcc','scoreComp','scoreNorm','scoreTime'].forEach(id => {
    const el = document.getElementById(id);
    el.textContent = '--';
    el.className = 'score-val excellent';
    const bar = el.closest('.score-item').querySelector('.score-bar-fill');
    if (bar) { bar.style.width = '0%'; }
  });

  // 重置发现（只切换子元素显示，不覆盖容器——避免销毁 findingEmpty/findingList）
  const flc = document.getElementById('findingListContainer');
  const fe2 = document.getElementById('findingEmpty');
  const fl2 = document.getElementById('findingList');
  if (fe2) { fe2.style.display = 'block'; fe2.innerHTML =
    '<div class="empty-icon">📭</div><p>运行质控后在此显示发现</p>'; }
  if (fl2) fl2.style.display = 'none';
  const fcnt = document.getElementById('findingCount');
  if (fcnt) fcnt.textContent = '0 条';
}

// ==================== 入库 ====================
async function saveToLibrary() {
  const findings = document.getElementById('findingsText').value.trim();
  const impression = document.getElementById('impressionText').value.trim();
  if (!findings && !impression) { toast('没有可入库的内容', 'error'); return; }

  // 未运行过质控则先跑一次，确保有评分与发现
  if (!document.querySelector('#findingList li')) {
    await runQC();
  }

  try {
    const report = [findings, impression].filter(Boolean).join('\n');
    const res = await apiFetch('/api/v1/samples', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        report,
        meta: {
          patient:      document.getElementById('mPatient').value,
          gender:       document.getElementById('mGender').value,
          age:          document.getElementById('mAge').value,
          modality:     document.getElementById('mModality').value,
          applied_site: document.getElementById('mSite').value,
          laterality:   effectiveLaterality(),
          user_id:      (document.getElementById('mUser') || {}).value || APP_SETTINGS.emp_id,
        },
        anonymize: !!APP_SETTINGS.anonymize,
        user_id:   (document.getElementById('mUser') || {}).value || APP_SETTINGS.emp_id,
      })
    });
    const data = await res.json();
    if (data.ok) {
      toast('已存入样本库（ID=' + (data.data.id || '?') + '）', 'success');
      // 从队列载入的报告，入库成功后自动出队（与桌面版 _dequeue_active 一致）
      if (ACTIVE_QUEUE_ID) {
        const qid = ACTIVE_QUEUE_ID; ACTIVE_QUEUE_ID = null;
        try { await apiFetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' }); } catch (e) {}
        loadQueue(true);
      }
    }
    else toast('入库失败: ' + (data.message || ''), 'error');
  } catch (e) {
    toast('入库请求失败: ' + e.message, 'error');
  }
}

// ==================== 系统设置（真实持久化） ====================
async function loadSettings(applyUI = true) {
  try {
    const res = await apiFetch('/api/v1/settings');
    const data = await res.json();
    if (data.ok) APP_SETTINGS = Object.assign(APP_SETTINGS, data.data || {});
  } catch (e) { /* 离线兜底：沿用默认值 */ }
  if (applyUI) {
    // 工号与默认模态回填到质控页
    const u = document.getElementById('mUser');
    if (u && APP_SETTINGS.emp_id) u.value = APP_SETTINGS.emp_id;
    const m = document.getElementById('mModality');
    if (m && !m.value && APP_SETTINGS.default_modality) m.value = APP_SETTINGS.default_modality;
    // 主题
    if (APP_SETTINGS.theme && !localStorage.getItem('xy-theme')) {
      document.documentElement.setAttribute('data-theme', APP_SETTINGS.theme);
    }
    const rf = document.getElementById('ocrRefresh');
    if (rf) rf.checked = !!APP_SETTINGS.screen_refresh_on_ocr;
    updateShortcutHints();
    // 启动时同步已保存的自定义快捷键到全局后台热键（持久化后跨重启生效）
    if (window.pywebview && window.pywebview.api && window.pywebview.api.applyGlobalHotkeys
        && APP_SETTINGS.shortcuts) {
      window.pywebview.api.applyGlobalHotkeys(APP_SETTINGS.shortcuts).catch(function () {});
    }
  }
  return APP_SETTINGS;
}

function openSettings() {
  const s = APP_SETTINGS;
  document.getElementById('setEmpId').value       = s.emp_id || '';
  document.getElementById('setModality').value    = s.default_modality || '';
  document.getElementById('setOcrScore').value    = s.ocr_min_score ?? 0.55;
  document.getElementById('setTheme').value       = document.documentElement.getAttribute('data-theme') || s.theme || 'light';
  document.getElementById('setAutoQC').checked        = !!s.auto_qc_on_ocr;
  document.getElementById('setAutoEnqueue').checked   = !!s.auto_enqueue;
  document.getElementById('setScreenRefresh').checked = !!s.screen_refresh_on_ocr;
  document.getElementById('setAnonymize').checked     = !!s.anonymize;
  document.getElementById('setOcrDynamic').checked    = !!s.ocr_dynamic;
  document.getElementById('setOcrSilent').checked     = !!s.ocr_silent;
  syncClipWatchUI();           // 同步桌面壳「监听剪贴板」开关状态
  renderShortcuts();
  populateLicenseSettings();   // 填授权状态 + 机器码
  document.getElementById('settingsModal').style.display = 'flex';
}

// 把当前 APP_SETTINGS.shortcuts 渲染到设置页各行
function renderShortcuts() {
  for (const action in SHORTCUT_ACTIONS) _renderShortcutRow(action);
}

function resetShortcuts() {
  APP_SETTINGS.shortcuts = {
    run_qc:       { mods: ['ctrl'], key: 'Enter' },
    save_sample:  { mods: ['ctrl'], key: 's' },
    ocr_capture:  { mods: ['ctrl', 'shift'], key: 'o' },
    toggle_theme: { mods: ['ctrl'], key: 't' },
  };
  renderShortcuts();
  toast('快捷键已恢复默认（保存后生效）', 'info');
}

// 侧边栏速查卡 + 运行按钮 跟随当前配置刷新
function updateShortcutHints() {
  const sc = APP_SETTINGS.shortcuts || {};
  document.querySelectorAll('.tips-key[data-action]').forEach(el => {
    el.textContent = fmtShortcut(sc[el.dataset.action]);
  });
  const run = document.getElementById('btnRunQc');
  if (run) run.textContent = '▶ 运行质控 ' + fmtShortcut(sc.run_qc);
}
function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }

/** 检查更新（2026-08-18 接入）：调 /api/v1/update/check，提示最新状态与下载链接。 */
async function checkForUpdate() {
  const btn = document.getElementById('updateCheckBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 检查中…'; }
  try {
    const res = await apiFetch('/api/v1/update/check');
    const data = await res.json();
    const d = (data && data.data) || {};
    const status = d.status || 'error';
    if (status === 'update') {
      toast('🆕 ' + (d.message || '发现新版本'), 'warn');
      if (d.url) {
        if (confirm('发现新版本，是否前往下载？\n' + d.url)) window.open(d.url, '_blank');
      }
    } else if (status === 'latest') {
      toast('✅ ' + (d.message || '已是最新版本'), 'success');
    } else {
      toast(d.message || '检查更新失败', 'info');
    }
  } catch (e) {
    toast('检查更新失败：' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 检查更新'; }
  }
}

async function saveSettings() {
  const payload = {
    emp_id:                document.getElementById('setEmpId').value.trim() || 'demo01',
    default_modality:      document.getElementById('setModality').value,
    ocr_min_score:         parseFloat(document.getElementById('setOcrScore').value) || 0.55,
    theme:                 document.getElementById('setTheme').value,
    auto_qc_on_ocr:        document.getElementById('setAutoQC').checked,
    auto_enqueue:          document.getElementById('setAutoEnqueue').checked,
    screen_refresh_on_ocr: document.getElementById('setScreenRefresh').checked,
    anonymize:             document.getElementById('setAnonymize').checked,
    ocr_dynamic:           document.getElementById('setOcrDynamic').checked,
    ocr_silent:            document.getElementById('setOcrSilent').checked,
    shortcuts:            APP_SETTINGS.shortcuts || {},
  };
  try {
    const res = await apiFetch('/api/v1/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    APP_SETTINGS = Object.assign(APP_SETTINGS, data.data || payload);
    // 立即生效：主题 / 工号 / 抓屏开关
    document.documentElement.setAttribute('data-theme', payload.theme);
    localStorage.setItem('xy-theme', payload.theme);
    const t = document.getElementById('themeToggle');
    if (t) t.textContent = payload.theme === 'dark' ? '☀️' : '🌙';
    const u = document.getElementById('mUser'); if (u) u.value = payload.emp_id;
    const rf = document.getElementById('ocrRefresh'); if (rf) rf.checked = payload.screen_refresh_on_ocr;
    updateShortcutHints();
    // 同步自定义快捷键到桌面壳全局后台热键（PACS 聚焦时也能用新组合）
    if (window.pywebview && window.pywebview.api && window.pywebview.api.applyGlobalHotkeys) {
      window.pywebview.api.applyGlobalHotkeys(payload.shortcuts || {}).catch(function () {});
    }
    closeSettings();
    toast('设置已保存并生效', 'success');
  } catch (e) { toast('保存设置失败: ' + e.message, 'error'); }
}

// ==================== 待质控队列 ====================
let QUEUE_ITEMS = [];
let ACTIVE_QUEUE_ID = null;   // 从队列载入工作区的条目，入库后自动出队

async function loadQueue(silent = false) {
  try {
    const res = await apiFetch('/api/v1/queue');
    const data = await res.json();
    QUEUE_ITEMS = ((data.data || {}).items) || [];
  } catch (e) {
    QUEUE_ITEMS = [];
    if (!silent) toast('队列读取失败: ' + e.message, 'error');
  }
  refreshQueueBadge();
  renderQueue();
  return QUEUE_ITEMS;
}

function refreshQueueBadge() {
  const badge = document.getElementById('navQueueBadge');
  const label = document.getElementById('navQueueLabel');
  const n = QUEUE_ITEMS.length;
  if (badge) { badge.textContent = n > 99 ? '99+' : n; badge.style.display = n ? 'block' : 'none'; }
  if (label) label.textContent = n ? `待质控 · ${n}` : '待质控队列';
}

function renderQueue() {
  const box = document.getElementById('queueList');
  if (!box) return;
  if (!QUEUE_ITEMS.length) {
    box.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div>' +
      '<p>队列为空。通过「RIS 直连 → 全部加入队列」「框选 OCR 采集」或质控页「📥 加入队列」拉入报告。</p></div>';
    return;
  }
  box.innerHTML = QUEUE_ITEMS.map(it => `
    <div class="queue-row">
      <div class="queue-main">
        <div class="queue-title">${escapeHtml(it.patient || '未知患者')} · ${escapeHtml(it.site || '—')}</div>
        <div class="queue-sub">来源：${escapeHtml(it.source || '—')} · ${escapeHtml(it.ts || '')}</div>
        <div class="queue-preview">${escapeHtml((it.text || '').replace(/\s+/g, ' ').slice(0, 120))}</div>
      </div>
      <div class="queue-acts">
        <button class="btn btn-primary btn-sm" onclick="queueLoad('${it.id}')">▶ 加载质控</button>
        <button class="btn btn-outline btn-sm" onclick="queueRemove('${it.id}')">✕ 移除</button>
      </div>
    </div>`).join('');
}

/** 把当前工作区报告加入队列 */
async function enqueueCurrent(source = '手动', silent = false) {
  const findings = document.getElementById('findingsText').value.trim();
  const impression = document.getElementById('impressionText').value.trim();
  if (!findings && !impression) { if (!silent) toast('没有可入队的内容', 'error'); return null; }
  const text = [findings, impression].filter(Boolean).join('\n');
  return enqueueText(text, {
    patient:      document.getElementById('mPatient').value,
    gender:       document.getElementById('mGender').value,
    age:          document.getElementById('mAge').value,
    modality:     document.getElementById('mModality').value,
    applied_site: document.getElementById('mSite').value,
    laterality:   effectiveLaterality(),
  }, source, silent);
}

async function enqueueText(text, meta, source, silent = false) {
  try {
    const res = await apiFetch('/api/v1/queue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text, meta: meta || {},
        patient: (meta || {}).patient || '', site: (meta || {}).applied_site || '',
        source,
      })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '入队失败');
    ACTIVE_QUEUE_ID = data.data.id;
    await loadQueue(true);
    if (!silent) toast(data.data.duplicated ? '该报告已在队列中' : '已加入待质控队列', data.data.duplicated ? 'info' : 'success');
    return data.data.id;
  } catch (e) {
    if (!silent) toast('入队失败: ' + e.message, 'error');
    return null;
  }
}

/** 队列条目载入质控工作区并立即跑一次质控 */
async function queueLoad(qid) {
  const it = QUEUE_ITEMS.find(x => x.id === qid);
  if (!it) return;
  const m = it.meta || {};
  setVal('mPatient', it.patient || m.patient);
  setVal('mGender', m.gender);
  setVal('mAge', m.age);
  setVal('mModality', m.modality);
  setVal('mSite', it.site || m.applied_site);
  setVal('mLaterality', m.laterality);
  // 队列正文按「描述 / 结论」两段还原（无明确分段则整体进描述）
  const parts = splitReportSections(it.text || '');
  document.getElementById('findingsText').value = parts.findings;
  document.getElementById('impressionText').value = parts.impression;
  document.getElementById('findingsCount').textContent = parts.findings.length + ' 字';
  document.getElementById('impressionCount').textContent = parts.impression.length + ' 字';
  ACTIVE_QUEUE_ID = qid;
  gotoPage('qc');
  await runQC();
  toast('已载入工作区，入库后自动出队', 'success');
}

/** 把整段报告粗分为「影像描述 / 影像诊断」两段。
 *  支持常见的报告排版：
 *   - 描述标题：影像描述 / 检查所见 / 影像所见 / 所见 / findings
 *   - 诊断标题：影像诊断 / 诊断印象 / 影像结论 / 诊断意见 / 结论 / 印象 / impression
 *   - 可带冒号、换行、序号（1. 影像诊断：…）
 *  原则：靠前的标题归描述段，靠后的标题归诊断段；诊断标题靠后，未找到时整段视为描述。
 */
function splitReportSections(text) {
  const t = (text || '').trim();
  if (!t) return { findings: '', impression: '' };

  // 诊断标题（可能带序号/冒号/换行）
  const impRe = /(?:^|\n)\s*(?:[（(]?\d+[)）]?[.、．]?\s*)?(影像诊断|诊断印象|影像结论|诊断意见|诊断结论|结论|印象|impression|conclusion)\s*[:：]?\s*(?=[\s\S]*)/i;
  const imp = t.match(impRe);
  if (imp && imp.index > 0) {
    const impText = t.slice(imp.index);
    let findings = t.slice(0, imp.index).trim();
    // 去掉描述段自身的标题（保留正文）
    findings = findings.replace(/^(?:影像描述|检查所见|影像所见|所见|findings|description)\s*[:：]?\s*/i, '').trim();
    // 去掉诊断标题本身（保留诊断正文）
    const impBody = impText.replace(impRe, '').trim();
    return { findings, impression: impBody };
  }
  // 无诊断标题 → 整段视为描述（去掉可能的描述标题）
  return { findings: t.replace(/^(?:影像描述|检查所见|影像所见|所见|findings|description)\s*[:：]?\s*/i, '').trim(), impression: '' };
}

/** 粘贴剪贴板全文 → 自动分栏（靠前描述、靠后诊断）→ 填入对应输入框 → 自动质控。
 *  返回 Promise<boolean>：是否成功读取剪贴板。
 */
async function pasteAndSplit() {
  try {
    let full = '';
    if (navigator.clipboard && navigator.clipboard.readText) {
      full = await navigator.clipboard.readText();
    }
    if (!full) {
      // 降级：老式浏览器/非安全上下文无 Clipboard API 时提示手动粘贴
      toast('未读取到剪贴板内容，请先点击输入框用 Ctrl+V/Cmd+V 粘贴', 'warn');
      return false;
    }
    full = (full || '').trim();
    if (!full) {
      toast('剪贴板为空', 'warn');
      return false;
    }
    const { findings, impression } = splitReportSections(full);
    const fEl = document.getElementById('findingsText');
    const iEl = document.getElementById('impressionText');
    if (fEl) fEl.value = findings;
    if (iEl) iEl.value = impression;
    // 直接 set value 不触发 input 事件，需手动刷新字数统计
    const fc = document.getElementById('findingsCount');
    const ic = document.getElementById('impressionCount');
    if (fc) fc.textContent = findings.length + ' 字';
    if (ic) ic.textContent = impression.length + ' 字';
    toast('已自动分栏：影像描述 ' + findings.length + ' 字 / 影像诊断 ' + impression.length + ' 字', 'success');
    // 自动执行质控
    setTimeout(() => { if (typeof runQC === 'function') runQC(); }, 60);
    return true;
  } catch (err) {
    toast('读取剪贴板失败：' + (err && err.message || err), 'warn');
    return false;
  }
}

// ==================== 剪贴板监听（复制即质控，桌面壳轮询推送） ====================
// desktop_app.py 后台线程检测到剪贴板出现新文本后，经 evaluate_js 调用本函数：
// 自动分栏填入描述/诊断 → 刷新字数 → 质控。浏览器环境（无桌面壳）不会触发。
function onClipboardCopy(text) {
  if (!text || !text.trim()) return;
  const { findings, impression } = splitReportSections(text);
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (fEl) fEl.value = findings;
  if (iEl) iEl.value = impression;
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = findings.length + ' 字';
  if (ic) ic.textContent = impression.length + ' 字';
  toast('📋 检测到剪贴板新报告，已分栏填入并质控', 'success');
  // 先切到质控页再质控（runQC 依赖质控页 DOM 元素 findingListContainer 等；
  // 否则用户停在设置/样本库等页面时元素为 null，抛 Cannot set properties of null）
  if (document.getElementById('page-qc')) {
    switchPage('qc', document.querySelector('.nav-cell[data-page="qc"]'));
  }
  setTimeout(() => { if (typeof runQC === 'function') runQC(); }, 60);
}

// 设置页「监听剪贴板」开关 → 调桌面壳原生桥（浏览器环境无桥，仅提示）
function toggleClipWatch(on) {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.setClipWatch) {
      window.pywebview.api.setClipWatch(!!on).catch(function () {});
      toast(on ? '剪贴板监听已开启（复制即质控）' : '剪贴板监听已关闭', 'success');
    } else {
      toast('仅桌面版支持剪贴板监听（浏览器无法后台读取剪贴板）', 'warn');
    }
  } catch (e) {
    toast('切换监听失败：' + (e && e.message || e), 'error');
  }
}

// 打开设置时同步桌面壳监听状态到开关
function syncClipWatchUI() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.getClipWatch) {
      window.pywebview.api.getClipWatch().then(function (on) {
        const el = document.getElementById('setClipWatch');
        if (el) el.checked = !!on;
      }).catch(function () {});
    }
  } catch (e) { /* 忽略 */ }
}

async function queueRemove(qid) {
  try {
    const res = await apiFetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '移除失败');
    if (ACTIVE_QUEUE_ID === qid) ACTIVE_QUEUE_ID = null;
    await loadQueue(true);
  } catch (e) { toast('移除失败: ' + e.message, 'error'); }
}

async function queueClear() {
  if (!QUEUE_ITEMS.length) { toast('队列已是空的', 'info'); return; }
  if (!confirm(`确认清空队列中的 ${QUEUE_ITEMS.length} 份报告？此操作不可撤销。`)) return;
  try {
    const res = await apiFetch('/api/v1/queue', { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '清空失败');
    ACTIVE_QUEUE_ID = null;
    await loadQueue(true);
    toast('队列已清空', 'success');
  } catch (e) { toast('清空失败: ' + e.message, 'error'); }
}

/** 队列批量质控入库：逐条送引擎并入库，成功后出队 */
async function queueRunAll() {
  if (!QUEUE_ITEMS.length) { toast('队列为空', 'info'); return; }
  if (!confirm(`将对队列中 ${QUEUE_ITEMS.length} 份报告批量质控并入库，继续？`)) return;
  const prog = document.getElementById('queueProgress');
  const fill = document.getElementById('queueProgressFill');
  if (prog) prog.classList.add('show');
  const items = QUEUE_ITEMS.slice();
  let okCount = 0, failCount = 0;
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    try {
      const res = await apiFetch('/api/v1/samples', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          report: it.text,
          meta: Object.assign({ patient: it.patient, applied_site: it.site }, it.meta || {}),
          anonymize: !!APP_SETTINGS.anonymize,
          user_id: APP_SETTINGS.emp_id,
        })
      });
      const d = await res.json();
      if (d.ok) {
        okCount++;
        // 2026-08-18：入库成功后出队失败单独提示——此前 DELETE 异常计入 failCount，
        // toast 报『失败』与实际不符，且重跑会对同一报告重复入库（接口无去重）
        try {
          await apiFetch('/api/v1/queue/' + encodeURIComponent(it.id), { method: 'DELETE' });
        } catch (e) {
          toast('第 ' + (i + 1) + ' 份已入库但移出队列失败，稍后可手动移出', 'warn');
        }
      }
      else failCount++;
    } catch (e) { failCount++; }
    if (fill) fill.style.width = Math.round(((i + 1) / items.length) * 100) + '%';
  }
  if (prog) prog.classList.remove('show');
  if (fill) fill.style.width = '0%';
  await loadQueue(true);
  toast(`批量完成：入库 ${okCount} 份${failCount ? '，失败 ' + failCount + ' 份' : ''}`, failCount ? 'warning' : 'success');
}

// ==================== 看板页：加载数据 ====================
async function loadDashboard() {
  try {
    // 统一走 apiFetch：/api/v1/samples 用 require_emp（需鉴权），裸 fetch 会 401
    const [statsRes, samplesRes] = await Promise.all([
      apiFetch('/api/v1/samples/stats/dashboard'),
      apiFetch('/api/v1/samples?page_size=10')
    ]);

    const stats = (await statsRes.json()).data || {};
    const samples = (await samplesRes.json()).data || {};

    // 统计卡片
    animateNumber('statTotal', stats.total || 0);
    animateNumber('statToday', stats.today || 0);
    animateNumber('statWeek', stats.this_week || 0);
    const passRate = stats.total ? ((1 - (stats.by_severity?.critical||0)/stats.total)*100).toFixed(1) : '--';
    document.getElementById('statPassRate').textContent = passRate + '%';

    // 模态分布柱状图
    renderModalityChart(stats.by_modality || {});

    // 最近记录表
    renderRecentTable(samples.items || []);

  } catch (err) {
    console.error('Dashboard load error:', err);
  }
  // 错误类型分布 + 趋势 + 分类统计报表（独立失败不影响主卡片）
  loadErrorTypes();
  loadTrend();
  loadStatsReport();
}

// ---------- 错误类型分布（接 /api/v1/stats/error-types） ----------
const ERR_COLORS = ['#2d6cdf', '#1fa971', '#e8941a', '#e5484d', '#7c6cf0', '#0ea5e9', '#db2777', '#65a30d'];

async function loadErrorTypes() {
  const box = document.getElementById('errorTypeChart');
  if (!box) return;
  try {
    const res = await apiFetch('/api/v1/stats/error-types');
    const data = await res.json();
    const stats = data.data || {};
    const entries = Object.entries(stats).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    document.getElementById('errTypeTotal').textContent = entries.length + ' 项';
    if (!entries.length) {
      box.innerHTML = '<div class="empty-state"><div class="empty-icon">✅</div><p>暂无错误记录</p></div>';
      return;
    }
    const max = Math.max(...entries.map(e => e[1]), 1);
    const sum = entries.reduce((a, e) => a + e[1], 0);
    box.innerHTML = entries.map(([name, cnt], i) => `
      <div class="errbar-row">
        <span class="errbar-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        <span class="errbar-track">
          <span class="errbar-fill" style="width:${Math.max(3, (cnt / max) * 100)}%;background:${ERR_COLORS[i % ERR_COLORS.length]}"></span>
        </span>
        <span class="errbar-val">${cnt}</span>
      </div>`).join('') +
      `<div style="margin-top:10px;font-size:12px;color:var(--text-muted);text-align:right;">共 ${sum} 条发现</div>`;
  } catch (e) {
    box.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><p>加载失败</p></div>';
  }
}

// ---------- 质控量趋势（接 /api/v1/stats/trend），内联 SVG 折线 ----------
async function loadTrend() {
  const box = document.getElementById('trendChart');
  if (!box) return;
  try {
    const res = await apiFetch('/api/v1/stats/trend');
    const data = await res.json();
    const stats = data.data || {};
    let entries = Object.entries(stats).sort((a, b) => a[0].localeCompare(b[0])).slice(-30);
    document.getElementById('trendTotal').textContent = entries.length + ' 天有数据';
    if (!entries.length) {
      box.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><p>暂无数据</p></div>';
      return;
    }
    if (entries.length === 1) entries = [[entries[0][0], entries[0][1]], entries[0]]; // 单点也画得出线
    const W = 460, H = 220, PL = 34, PR = 10, PT = 14, PB = 26;
    // 契约：/stats/trend 返回 {date: {n, avg_acc}}，取 n 画线（2026-08-18 修复 NaN 空图）
    const max = Math.max(...entries.map(e => e[1].n), 1);
    const stepX = (W - PL - PR) / Math.max(1, entries.length - 1);
    const yOf = v => PT + (H - PT - PB) * (1 - v / max);
    const pts = entries.map((e, i) => [PL + i * stepX, yOf(e[1].n)]);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${H - PB} L${pts[0][0].toFixed(1)},${H - PB} Z`;
    // Y 轴 3 条参考线
    const grid = [0, 0.5, 1].map(f => {
      const v = Math.round(max * f), y = yOf(v);
      return `<line x1="${PL}" y1="${y}" x2="${W - PR}" y2="${y}" stroke="var(--border-color)" stroke-dasharray="3 4"/>
              <text x="${PL - 6}" y="${y + 4}" text-anchor="end" font-size="10" fill="var(--text-muted)">${v}</text>`;
    }).join('');
    // X 轴首/中/末日期
    const idxs = [...new Set([0, Math.floor(entries.length / 2), entries.length - 1])];
    const xLabels = idxs.map(i => `<text x="${(PL + i * stepX).toFixed(1)}" y="${H - 8}" text-anchor="middle"
      font-size="10" fill="var(--text-muted)">${entries[i][0].slice(5)}</text>`).join('');
    const dots = pts.map((p, i) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6"
      fill="var(--primary)"><title>${entries[i][0]}：${entries[i][1].n} 份</title></circle>`).join('');
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:240px;">
      <defs><linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--primary)" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="var(--primary)" stop-opacity="0.02"/>
      </linearGradient></defs>
      ${grid}
      <path d="${area}" fill="url(#trendGrad)"/>
      <path d="${line}" fill="none" stroke="var(--primary)" stroke-width="2.2" stroke-linejoin="round"/>
      ${dots}${xLabels}
    </svg>`;
  } catch (e) {
    box.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><p>加载失败</p></div>';
  }
}

// ---------- 质控问题分类统计报表（时间段 + TOP 榜 + 医生排行榜） ----------
function _rangeStart(range) {
  const now = new Date();
  if (range === '7d') { const d = new Date(now); d.setDate(d.getDate() - 7); return d; }
  if (range === '30d') { const d = new Date(now); d.setDate(d.getDate() - 30); return d; }
  if (range === 'quarter') {
    const q = Math.floor(now.getMonth() / 3);
    return new Date(now.getFullYear(), q * 3, 1);
  }
  return null;   // all
}

async function loadStatsReport() {
  const rangeEl = document.getElementById('reportRange');
  const range = rangeEl ? rangeEl.value : '30d';
  const start = _rangeStart(range);
  const q = [];
  if (start) q.push('start=' + start.toISOString().slice(0, 10));
  try {
    const res = await apiFetch('/api/v1/stats/report' + (q.length ? '?' + q.join('&') : ''));
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '加载失败');
    renderStatsReport(data.data || {});
  } catch (e) {
    ['reportErrTop', 'reportRuleTop', 'reportDoctorRank'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="muted" style="font-size:13px;">加载失败: ' + escapeHtml(e.message) + '</div>';
    });
  }
}

function renderStatsReport(st) {
  const p = st.period || {};
  const setTxt = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  setTxt('reportPeriod', (p.start || '最早') + ' ~ ' + (p.end || '至今'));
  setTxt('reportTotal', p.total || 0);
  setTxt('reportCrit', p.critical || 0);
  setTxt('reportWarn', p.warning || 0);
  setTxt('reportInfo', p.info || 0);

  // 问题类型 TOP 榜（横向条形）
  const et = (st.error_type_top || []).slice(0, 8);
  const errBox = document.getElementById('reportErrTop');
  if (!errBox) return;
  if (!et.length) { errBox.innerHTML = '<div class="empty-state"><div class="empty-icon">✅</div><p>该区间无质控问题</p></div>'; }
  else {
    const max = Math.max(...et.map(e => e.count), 1);
    const colors = ['#e5484d', '#e8941a', '#f5b50a', '#2d6cdf', '#1fa971', '#7c6cf0', '#0ea5e9', '#db2777'];
    errBox.innerHTML = et.map((e, i) => `
      <div class="errbar-row">
        <span class="errbar-name" title="${escapeHtml(e.name)}">${escapeHtml(e.name)}</span>
        <span class="errbar-track"><span class="errbar-fill" style="width:${Math.max(4, (e.count / max) * 100)}%;background:${colors[i % colors.length]}"></span></span>
        <span class="errbar-val">${e.count}</span>
      </div>`).join('');
  }

  // 规则命中 TOP 榜
  const rt = (st.rule_top || []).slice(0, 8);
  const ruleBox = document.getElementById('reportRuleTop');
  if (!ruleBox) return;
  if (!rt.length) { ruleBox.innerHTML = '<div class="empty-state"><div class="empty-icon">✅</div><p>该区间无规则命中</p></div>'; }
  else {
    const max = Math.max(...rt.map(e => e.count), 1);
    ruleBox.innerHTML = rt.map(e => `
      <div class="errbar-row">
        <span class="errbar-name" style="font-family:monospace;font-size:11px;" title="${escapeHtml(e.rule_id)}">${escapeHtml(e.rule_id)}</span>
        <span class="errbar-track"><span class="errbar-fill" style="width:${Math.max(4, (e.count / max) * 100)}%;background:var(--primary)"></span></span>
        <span class="errbar-val">${e.count}</span>
      </div>`).join('');
  }

  // 医生排行榜
  const dr = (st.doctor_rank || []).slice(0, 8);
  const drBox = document.getElementById('reportDoctorRank');
  if (!drBox) return;
  if (!dr.length) { drBox.innerHTML = '<div class="empty-state"><div class="empty-icon">👨‍⚕️</div><p>该区间无医生质控记录</p></div>'; }
  else {
    const max = Math.max(...dr.map(d => d.findings), 1);
    drBox.innerHTML = dr.map((d, i) => `
      <div class="errbar-row">
        <span class="errbar-name" title="${escapeHtml(d.user_id)}">${i + 1}. ${escapeHtml(d.name || d.user_id)} <span class="muted" style="font-size:11px;">(${d.samples} 份报告)</span></span>
        <span class="errbar-track"><span class="errbar-fill" style="width:${Math.max(4, (d.findings / max) * 100)}%;background:#7c6cf0"></span></span>
        <span class="errbar-val">${d.findings} 问题</span>
      </div>`).join('');
  }
}

function animateNumber(elId, target) {
  const el = document.getElementById(elId);
  const start = parseInt(el.textContent) || 0;
  const duration = 600;
  const startTime = performance.now();

  function step(now) {
    const p = Math.min((now - startTime) / duration, 1);
    el.textContent = Math.round(start + (target - start) * easeOutCubic(p));
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

function renderModalityChart(byModality) {
  const container = document.getElementById('modalityChart');
  const entries = Object.entries(byModality).sort((a,b) => b[1]-a[1]);
  const maxVal = Math.max(...entries.map(e=>e[1]), 1);

  container.innerHTML = entries.map(([mod, count]) => {
    const h = Math.max(20, (count / maxVal) * 200);
    const colors = { CT:'#2d6cdf', DR:'#1fa971', MR:'#e8941a', XA:'#e5484d', US:'#7c6cf0' };
    return `
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;">
        <span style="font-size:13px;font-weight:700;color:var(--text-primary);">${count}</span>
        <div style="width:36px;height:${h}px;border-radius:8px;background:${colors[mod]||'var(--primary)'};transition:height 0.5s;"></div>
        <span style="font-size:11px;color:var(--text-muted);font-weight:600;">${escapeHtml(mod)}</span>
      </div>`;
  }).join('');

  if (entries.length === 0) {
    container.innerHTML = '<div style="flex:1;text-align:center;color:var(--text-muted);padding:40px 0;">暂无数据</div>';
  }
}

function renderRecentTable(items) {
  const tbody = document.getElementById('recentBody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:24px;">暂无数据</td></tr>';
    return;
  }
  tbody.innerHTML = items.slice(0,8).map(r => `
    <tr>
      <td style="font-size:12px;color:var(--text-muted);">${escapeHtml((r.ts||'--').slice(5,16))}</td>
      <td>${escapeHtml(r.patient||'--')}</td>
      <td><span class="tag info">${escapeHtml(r.modality||'--')}</span></td>
      <td>${escapeHtml(r.applied_site||'--')}</td>
      <td>
        <span class="tag ${(r.scores?.accuracy||0)>=90?'success':'warning'}">
          ${(r.scores?.accuracy||0).toFixed(0)}
        </span>
      </td>
    </tr>
  `).join('');
}

// ==================== 样本库 ====================
async function loadSamples() {
  try {
    const res = await apiFetch('/api/v1/samples?page_size=50');
    const data = await res.json();
    const items = (data.data || {}).items || [];
    const tbody = document.getElementById('samplesBody');

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text-muted);padding:32px;">暂无样本数据</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(r => `
      <tr>
        <td style="color:var(--text-muted);font-size:12px;">${r.id}</td>
        <td style="font-size:12px;">${r.ts||'--'}</td>
        <td>${escapeHtml(r.patient)||'--'}</td>
        <td>${escapeHtml(r.gender)||'--'}</td>
        <td>${escapeHtml(r.age)||'--'}</td>
        <td><span class="tag info">${escapeHtml(r.modality)||'--'}</span></td>
        <td>${escapeHtml(r.applied_site)||'--'}</td>
        <td>${r.findings_count||0}</td>
        <td><span class="tag ${(r.scores?.accuracy||0)>=90?'success':'warning'}">${(r.scores?.accuracy||0).toFixed(0)}</span></td>
        <td><span class="tag ${(r.scores?.completeness||0)>=90?'success':'warning'}">${(r.scores?.completeness||0).toFixed(0)}</span></td>
        <td style="white-space:nowrap;">
          <button class="btn btn-outline btn-sm" onclick="viewSample(${r.id})">👁 查看</button>
          <button class="btn btn-outline btn-sm" onclick="deleteSample(${r.id})">🗑</button>
        </td>
      </tr>
    `).join('');
  } catch (e) { console.error(e); }
}

// ---------- 样本详情 / 删除 ----------
async function viewSample(sid) {
  try {
    const res = await apiFetch('/api/v1/samples/' + sid);
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '读取失败');
    const s = data.data || {};
    let findings = [];
    try { findings = JSON.parse(s.findings_json || '[]'); } catch (e) {}
    let scores = {};
    try { scores = JSON.parse(s.scores_json || '{}'); } catch (e) {}

    document.getElementById('sampleModalTitle').textContent = `📄 样本 #${s.id} · ${s.patient || '未知患者'}`;
    document.getElementById('sampleModalBody').innerHTML = `
      <div class="sample-kv">
        <div><span>时间</span><b>${escapeHtml(s.ts || '--')}</b></div>
        <div><span>性别 / 年龄</span><b>${escapeHtml(s.gender || '--')} / ${escapeHtml(String(s.age || '--'))}</b></div>
        <div><span>成像方式</span><b>${escapeHtml(s.modality || '--')}</b></div>
        <div><span>检查部位</span><b>${escapeHtml(s.applied_site || '--')}${s.laterality ? '（' + escapeHtml(s.laterality) + '）' : ''}</b></div>
        <div><span>操作工号</span><b>${escapeHtml(s.user_id || '--')}</b></div>
        <div><span>发现数</span><b>${findings.length}</b></div>
        ${Object.entries(scores).map(([k, v]) =>
          `<div><span>${escapeHtml(k)}</span><b>${typeof v === 'number' ? v.toFixed(1) : escapeHtml(String(v))}</b></div>`).join('')}
      </div>
      <div style="font-size:12px;font-weight:700;margin:6px 0;">报告正文</div>
      <div class="sample-report">${escapeHtml(s.report_text || '')}</div>
      <div style="font-size:12px;font-weight:700;margin:14px 0 6px;">质控发现（${findings.length} 条）</div>
      ${findings.length ? `<ul class="finding-list" style="display:block">${findings.map(f => {
        const m = SEV_META[f.severity] || SEV_META.low;
        return `<li class="finding-item">
          <span class="severity-dot ${f.severity}"></span>
          <span class="sev-badge ${m.cls}">${m.icon} ${m.label}</span>
          <div><div class="finding-text ${m.cls}">${escapeHtml(f.message)}</div>
          <div class="finding-meta">${escapeHtml(f.rule_id || '')} · ${escapeHtml(f.error_type || '')}</div></div>
        </li>`; }).join('')}</ul>`
        : '<div style="font-size:13px;color:var(--text-muted);">无发现，报告质量良好</div>'}
    `;
    document.getElementById('sampleLoadBtn').onclick = () => { loadSampleToWorkspace(s); };
    document.getElementById('sampleDelBtn').onclick = () => { closeSampleModal(); deleteSample(s.id); };
    document.getElementById('sampleExportWordBtn').onclick = () => { exportSampleReport(s.id, 'docx'); };
    document.getElementById('sampleExportPdfBtn').onclick = () => { exportSampleReport(s.id, 'pdf'); };
    document.getElementById('sampleModal').style.display = 'flex';
  } catch (e) { toast('读取样本失败: ' + e.message, 'error'); }
}

function closeSampleModal() { document.getElementById('sampleModal').style.display = 'none'; }

function loadSampleToWorkspace(s) {
  const parts = splitReportSections(s.report_text || '');
  setVal('mPatient', s.patient); setVal('mGender', s.gender); setVal('mAge', s.age);
  setVal('mModality', s.modality); setVal('mSite', s.applied_site); setVal('mLaterality', s.laterality);
  document.getElementById('findingsText').value = parts.findings;
  document.getElementById('impressionText').value = parts.impression;
  document.getElementById('findingsCount').textContent = parts.findings.length + ' 字';
  document.getElementById('impressionCount').textContent = parts.impression.length + ' 字';
  ACTIVE_QUEUE_ID = null;   // 来自样本库，非队列条目
  closeSampleModal();
  gotoPage('qc');
  runQC();
}

async function deleteSample(sid) {
  if (!confirm(`确认删除样本 #${sid}？删除后不可恢复。`)) return;
  try {
    // 必须用 apiFetch 携带 X-Emp-Id：后端按归属校验，裸 fetch 不带身份会被拒
    const res = await apiFetch('/api/v1/samples/' + sid, { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '删除失败');
    toast('样本 #' + sid + ' 已删除', 'success');
    loadSamples();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

async function exportSamples() {
  // 让用户选择导出格式：CSV(Excel友好) / JSON(原样) / DOCX(Word报告) / PDF
  const fmt = await _pickExportFormat();
  if (!fmt) return;
  try {
    toast(`正在导出 ${fmt.toUpperCase()} 样本库...`, 'info');
    const res = await apiFetch('/api/v1/samples/export', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ fmt })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('导出失败: ' + (data.message || data.detail || ''), 'error');
      return;
    }
    const p = data.data.path || '';
    // 同时尝试触发浏览器下载（桌面 WebView / 浏览器均可）
    downloadExportedFile(p);
    toast('导出成功: ' + p, 'success');
  } catch(e) { toast('导出请求失败: ' + e.message, 'error'); }
}

/** 弹出导出格式选择（模态框，兼容 pywebview）。返回 'csv'|'json'|'docx'|'pdf' 或 null */
let _exportFmtResolve = null;
function _pickExportFormat() {
  return new Promise(resolve => {
    _exportFmtResolve = resolve;
    const t = document.getElementById('exportFmtTitle');
    if (t) t.textContent = '选择导出格式';
    const d = document.getElementById('exportFmtDesc');
    if (d) d.textContent = '导出整个样本库为以下格式：';
    const csv = document.getElementById('exportFmtCsv');
    const json = document.getElementById('exportFmtJson');
    if (csv) csv.style.display = 'grid';
    if (json) json.style.display = 'grid';
    const m = document.getElementById('exportFmtModal');
    if (m) m.style.display = 'flex';
    else resolve('docx');   // 元素缺失兜底：默认 Word
  });
}

/** 质控报告单格式选择（模态框）。返回 'docx'|'pdf' 或 null */
function _pickReportFormat() {
  return new Promise(resolve => {
    _exportFmtResolve = resolve;
    const t = document.getElementById('exportFmtTitle');
    if (t) t.textContent = '导出质控报告单';
    const d = document.getElementById('exportFmtDesc');
    if (d) d.textContent = '把当前质控结果（原报告 + 发现 + 评分）导出为报告单：';
    const csv = document.getElementById('exportFmtCsv');
    const json = document.getElementById('exportFmtJson');
    if (csv) csv.style.display = 'none';
    if (json) json.style.display = 'none';
    const m = document.getElementById('exportFmtModal');
    if (m) m.style.display = 'flex';
    else resolve('docx');
  });
}

function closeExportFmtModal() {
  const m = document.getElementById('exportFmtModal');
  if (m) m.style.display = 'none';
  if (_exportFmtResolve) { _exportFmtResolve(null); _exportFmtResolve = null; }
}

function pickExportFmt(fmt) {
  const m = document.getElementById('exportFmtModal');
  if (m) m.style.display = 'none';
  if (_exportFmtResolve) { _exportFmtResolve(fmt); _exportFmtResolve = null; }
}

/** 下载服务端生成的导出文件（GET /files/download，走 apiFetch 带鉴权头；
 *  2026-08-18 修复：此前裸 <a> 导航无 Authorization，远程部署必 401）。 */
async function downloadExportedFile(path) {
  const name = (path || '').split(/[\\/]/).pop();
  if (!name) return;
  try {
    const res = await apiFetch('/api/v1/files/download?file=' + encodeURIComponent(name));
    if (!res.ok) { toast('下载失败：' + res.status, 'error'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
  } catch (e) {
    toast('下载失败：' + (e.message || e), 'error');
  }
}

/** 当前工作区质控结果直接导出质控报告单（无需入库） */
async function exportQcReport() {
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) return;
  const report = [fEl.value, iEl.value].filter(Boolean).join('\n');
  if (!report.trim()) { toast('请先输入报告内容再导出', 'warn'); return; }
  const fmt = await _pickReportFormat();
  if (!fmt) return;
  // 复用最近一次质控发现与评分（若尚未质控则自动跑一遍）
  let findings = _qcAllFindings || [];
  let scores = null;
  try { scores = JSON.parse(document.getElementById('scoreAcc').textContent === '--' ? 'null' : '{}'); } catch (e) {}
  if (!findings.length) {
    toast('正在运行质控以获取发现...', 'info');
    const ok = await runQC();
    if (!ok) return;
    findings = _qcAllFindings || [];
  }
  // 从界面读取当前评分（仅展示用途，后端导出兼容 dict/score 两种结构）
  const scoreObj = {};
  const sids = { scoreAcc: '准确性', scoreComp: '完整性', scoreNorm: '规范性', scoreTime: '及时性' };
  for (const [elId, cn] of Object.entries(sids)) {
    const el = document.getElementById(elId);
    if (el && el.textContent !== '--') scoreObj[cn] = { score: parseFloat(el.textContent) || 0, deductions: [] };
  }
  try {
    const res = await apiFetch('/api/v1/qc/export-report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report, meta: currentQcMeta(), findings, scores: scoreObj, fmt })
    });
    const data = await res.json();
    if (!data.ok) { toast('导出失败: ' + (data.message || data.detail || ''), 'error'); return; }
    const p = data.data.path || '';
    downloadExportedFile(p);
    toast('质控报告单已导出: ' + p, 'success');
  } catch (e) { toast('导出失败: ' + e.message, 'error'); }
}


/** 样本详情：导出质控报告单（Word） */
async function exportSampleReport(sid, fmt) {
  if (!sid) { toast('样本 ID 无效', 'error'); return; }
  try {
    toast(`正在生成质控报告单（${fmt === 'pdf' ? 'PDF' : 'Word'}）...`, 'info');
    const res = await apiFetch(`/api/v1/samples/${sid}/export-report`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fmt })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('导出失败: ' + (data.message || data.detail || ''), 'error');
      return;
    }
    const p = data.data.path || '';
    downloadExportedFile(p);
    toast('质控报告单已导出: ' + p, 'success');
  } catch (e) { toast('导出失败: ' + e.message, 'error'); }
}

// ==================== 规则词表维护（R8 错别字 / R9 矛盾对 / 忽略词 / R10 模板） ====================

/** P0 一键采纳修正闭环：把某条确定性错别字的 suggestion 应用进报告文本并重新质控。
 *  实现：报告全文 = 描述+结论 拼接（与 /api/v1/qc/check 的 report 一致），span 直接定位替换，
 *  替换后按段重新分栏填入输入框，再自动跑一遍质控刷新结果。
 *  注意：其他发现（矛盾/缺失类）无安全替换值，不做自动改动。 */
async function applyFindingFix(findIdx) {
  const f = (_qcAllFindings || [])[findIdx];
  if (!f) { toast('未找到该发现', 'error'); return; }
  const span = Array.isArray(f.span) ? f.span : [-1, -1];
  const sug = (f.suggestion || '').trim();
  const wrong = (f.snippet || '').trim();
  if (span[0] < 0 || span[1] <= span[0] || !sug || !wrong) {
    toast('该问题无安全修正值，请人工修改', 'warn');
    return;
  }
  // 报告全文必须以最新输入框内容为准（用户可能改过）
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) return;
  const full = [fEl.value, iEl.value].filter(Boolean).join('\n');
  if (span[1] > full.length) { toast('原文已变化，请重新运行质控', 'warn'); return; }
  const fixedText = full.slice(0, span[0]) + sug + full.slice(span[1]);
  // 分栏回填：splitReportSections 兼容描述在前/诊断在后两种顺序
  const parts = splitReportSections(fixedText);
  fEl.value = parts.findings;
  iEl.value = parts.impression;
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = parts.findings.length + ' 字';
  if (ic) ic.textContent = parts.impression.length + ' 字';
  toast(`已应用修正：「${wrong}」→「${sug}」，正在重新质控...`, 'success');
  await runQC();
}

/** 一键采纳全部修正：调用引擎 auto_fix 批量应用所有确定性错别字修正并重新质控。 */
async function applyAllFixes() {
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (!fEl || !iEl) return;
  const report = [fEl.value, iEl.value].filter(Boolean).join('\n');
  if (!report.trim()) { toast('请输入报告内容', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report, meta: currentQcMeta(), auto_fix: true })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '执行失败');
    const fixed = data.data.fixed;
    if (!fixed || !fixed.fixed_text || fixed.fixed_text === report) {
      toast('没有可自动修正的错别字', 'info');
      return;
    }
    const parts = splitReportSections(fixed.fixed_text);
    fEl.value = parts.findings;
    iEl.value = parts.impression;
    const fc = document.getElementById('findingsCount');
    const ic = document.getElementById('impressionCount');
    if (fc) fc.textContent = parts.findings.length + ' 字';
    if (ic) ic.textContent = parts.impression.length + ' 字';
    toast(`已自动修正 ${fixed.n_fixed} 处错别字${fixed.n_manual ? '，另有 ' + fixed.n_manual + ' 处需人工确认' : ''}，正在重新质控...`, 'success');
    await runQC();
  } catch (e) {
    toast('应用修正失败: ' + e.message, 'error');
  }
}

/** 收集当前元信息（供 auto_fix 请求复用，与 runQC 的 meta 保持一致） */
function currentQcMeta() {
  const el = id => { const e = document.getElementById(id); return e ? e.value : ''; };
  return {
    patient: el('mPatient'), gender: el('mGender'), age: el('mAge'),
    modality: el('mModality'), applied_site: el('mSite'),
    laterality: effectiveLaterality(), user_id: el('mUser') || APP_SETTINGS.emp_id,
  };
}

/** P0 修正反馈闭环：QC 结果里点「采纳修正」→ 错词→正确词写入规则库 */
async function learnTypoFromFinding(btn) {
  const wrong = (btn && btn.dataset.wrong || '').trim();
  const correct = (btn && btn.dataset.correct || '').trim();
  if (!wrong || !correct) { toast('缺少错词/正确词，无法学习', 'error'); return; }
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/v1/qc/rules/learn-typo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '学习失败');
    btn.textContent = '✓ 已学习';
    btn.classList.add('done');
    toast(`已学习：${wrong} → ${correct}，下次自动识别`, 'success');
  } catch (err) {
    btn.disabled = false;
    toast('学习失败：' + (err.message || err), 'error');
  }
}

/** P0 历史报告词频学习：扫描样本库发现候选错字并展示，一键采纳 */
let _scanCandidates = [];
async function scanReportsForTypos() {
  const box = document.getElementById('scanResult');
  const btn = document.getElementById('scanBtn');
  if (!box) return;
  btn && (btn.disabled = true);
  btn && (btn.textContent = '扫描中…（读取最近 200 份报告）');
  box.innerHTML = '<div class="muted">正在扫描样本库并统计词频…</div>';
  try {
    const res = await apiFetch('/api/v1/qc/rules/scan-reports', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '扫描失败');
    _scanCandidates = (data.data && data.data.candidates) || [];
    if (_scanCandidates.length === 0) {
      box.innerHTML = '<div class="muted">未发现候选错字（样本库为空或均为标准写法）。入库的报告越多，学习越准。</div>';
    } else {
      box.innerHTML = `<div class="scan-head">发现 ${_scanCandidates.length} 个候选错字（来自历史报告词频+读音比对）</div>` +
        `<div class="scan-list">` + _scanCandidates.map((c, i) => `
          <div class="scan-item">
            <span class="scan-word">${escapeHtml(c.wrong)}</span>
            <span class="scan-arrow">→</span>
            <span class="scan-word ok">${escapeHtml(c.correct)}</span>
            <span class="scan-count">${c.count}次</span>
            <span class="scan-reason" title="${escapeHtml(c.reason)}">${escapeHtml(c.reason)}</span>
            <button class="btn btn-xs learn-btn" onclick="adoptScanCandidate(this, ${i})">采纳</button>
          </div>`).join('') + `</div>`;
    }
  } catch (err) {
    box.innerHTML = '<div class="muted">扫描失败：' + escapeHtml(err.message || err) + '</div>';
  } finally {
    btn && (btn.disabled = false);
    btn && (btn.textContent = '🔄 扫描历史报告学习');
  }
}

/** 采纳一个扫描候选错字 → 写入规则库 */
async function adoptScanCandidate(btn, idx) {
  const c = _scanCandidates[idx];
  if (!c) return;
  btn.disabled = true;
  try {
    const res = await apiFetch('/api/v1/qc/rules/learn-typo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong: c.wrong, correct: c.correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '学习失败');
    btn.textContent = '✓';
    btn.classList.add('done');
    toast(`已学习：${c.wrong} → ${c.correct}`, 'success');
  } catch (err) {
    btn.disabled = false;
    toast('采纳失败：' + (err.message || err), 'error');
  }
}

// 规则词表维护界面的数据快照（用于恢复默认/取消编辑）
let _cfgSnapshot = null;

async function loadRulesConfig(silent = false) {
  try {
    const res = await apiFetch('/api/v1/qc/rules/config');
    const data = await res.json();
    const cfg = (data.data || {});
    const typos = cfg.typos || {};
    // 错字表按错词排序，长表更易查
    document.getElementById('cfgTypos').value =
      Object.keys(typos).sort((a, b) => a.localeCompare(b, 'zh'))
        .map(k => `${k}=${typos[k]}`).join('\n');
    const conflicts = cfg.conflicts || [];
    document.getElementById('cfgConflicts').value =
      conflicts
        .map(c => {
          const a = Array.isArray(c) ? c[0] : (c.a || '').trim();
          const b = Array.isArray(c) ? c[1] : (c.b || '').trim();
          const scope = (!Array.isArray(c) && c.scope) || '正文';
          if (!a || !b || a === b) return '';
          // 范围默认正文时省略前缀，保持简洁；非正文显示 [范围]
          return scope === '正文' ? `${a}|${b}` : `[${scope}] ${a}|${b}`;
        })
        .filter(Boolean)
        .sort((x, y) => x.localeCompare(y, 'zh'))
        .join('\n');
    const ignores = cfg.ignores || [];
    document.getElementById('cfgIgnores').value = ignores.map(String).sort((a, b) => a.localeCompare(b, 'zh')).join('\n');
    const tpl = cfg.template || {};
    document.getElementById('cfgTplFollowup').checked = !!tpl.require_followup;
    // R19 错字检测（读音相似 + 形近字，高频词组锚定）；默认开启
    const r19El = document.getElementById('cfgR19');
    if (r19El) r19El.checked = cfg.enable_r19 !== false;
    // R19 灵敏度（低/中/高）
    const sensEl = document.getElementById('cfgR19Sens');
    if (sensEl) sensEl.value = ['low', 'medium', 'high'].includes(cfg.r19_sensitivity) ? cfg.r19_sensitivity : 'medium';
    // 快照：供「恢复默认/撤销编辑」使用
    _cfgSnapshot = {
      typos: document.getElementById('cfgTypos').value,
      conflicts: document.getElementById('cfgConflicts').value,
      ignores: document.getElementById('cfgIgnores').value,
    };
    _typoCache = Object.assign({}, typos);
    _typoDisabled = new Set(cfg.disabled_typos || []);
    updateCfgStats();   // 统计规则条数
    renderTypoTable();  // 可视化词库表
    if (!silent) toast('规则配置已载入', 'success');
  } catch (e) {
    if (!silent) toast('载入规则配置失败: ' + e.message, 'error');
  }
}

// 实时统计各词表条数（供界面展示）
function updateCfgStats() {
  const count = (txt) => txt.split('\n').map(s => s.trim()).filter(Boolean).length;
  const el = (id) => document.getElementById(id);
  const nTypos = count(el('cfgTypos').value || '');
  const nConf = count(el('cfgConflicts').value || '');
  const nIg = count(el('cfgIgnores').value || '');
  const stat = el('cfgStats');
  if (stat) stat.textContent = `共 ${nTypos} 条错字 · ${nConf} 条矛盾对 · ${nIg} 条忽略词`;
}

// 撤销本次编辑，回到上次载入的配置
function revertRulesConfig() {
  if (!_cfgSnapshot) { toast('尚无快照，请先载入配置', 'warn'); return; }
  document.getElementById('cfgTypos').value = _cfgSnapshot.typos;
  document.getElementById('cfgConflicts').value = _cfgSnapshot.conflicts;
  document.getElementById('cfgIgnores').value = _cfgSnapshot.ignores;
  updateCfgStats();
  toast('已撤销本次编辑', 'info');
}

async function saveRulesConfig() {
  // 错字表解析：每行「错词=正词」；格式错误（缺=号、空值）行计数并提示
  const typoLines = document.getElementById('cfgTypos').value.split('\n').map(s => s.trim()).filter(Boolean);
  const typos = {};
  let nTypoBad = 0;
  for (const line of typoLines) {
    const i = line.indexOf('=');
    const k = line.slice(0, i).trim(), v = line.slice(i + 1).trim();
    if (i <= 0 || !k || !v || k === v) { nTypoBad++; continue; }
    typos[k] = v;
  }
  if (nTypoBad > 0) toast(`有 ${nTypoBad} 行错字格式无效已跳过（应为 错词=正词 且错≠正）`, 'warn');
  // R9 矛盾对解析。支持两种格式：
  //   词A|词B                                  → 范围=正文（默认，整篇互斥）
  //   [范围] 词A|词B                          → 指定范围，范围 ∈ {正文, 描述段, 结论段, 同一句, 描述vs结论}
  // A≠B 且都非空；空行/只有一列/自反(A==B)跳过，避免生成『A 与 A 互斥』的误报规则。
  const _SCOPES = ['正文', '描述段', '结论段', '同一句', '描述vs结论'];
  const rawLines = document.getElementById('cfgConflicts').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const conflicts = [];
  let nBad = 0;
  for (const ln of rawLines) {
    let scope = '正文', body = ln;
    const m = ln.match(/^\[(.+?)\]\s*(.*)$/);
    if (m) {
      scope = m[1].trim();
      body = m[2].trim();
      if (!_SCOPES.includes(scope)) { console.warn('未知范围，已跳过：' + ln); nBad++; continue; }
    }
    const p = body.split('|').map(s => s.trim()).filter(Boolean);
    if (p.length < 2) { console.warn('矛盾对格式应为 词A|词B，已跳过：' + ln); nBad++; continue; }
    const a = p[0], b = p[1];
    if (a === b) { console.warn('矛盾对 A 与 B 相同，已跳过：' + ln); nBad++; continue; }
    // 保留已存 severity/note 元数据（2026-08-18：此前保存只回传 a/b/scope 会抹掉它们）
    const prev = (_cfgSnapshot && _cfgSnapshot.conflicts || []).find(c => c.a === a && c.b === b);
    conflicts.push({ a, b, scope,
                     severity: (prev && prev.severity) || undefined,
                     note: (prev && prev.note) || undefined });
  }
  if (nBad > 0) {
    toast(`有 ${nBad} 行矛盾对格式无效已跳过（每行应为 [范围] 词A|词B，范围∈{${_SCOPES.join('/')}}）`, 'warn');
  }
  const ignores = document.getElementById('cfgIgnores').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const tpl = { require_followup: document.getElementById('cfgTplFollowup').checked };
  const r19El = document.getElementById('cfgR19');
  const enable_r19 = r19El ? r19El.checked : true;
  const sensEl = document.getElementById('cfgR19Sens');
  const r19_sensitivity = (sensEl && ['low', 'medium', 'high'].includes(sensEl.value)) ? sensEl.value : 'medium';
  try {
    const res = await apiFetch('/api/v1/qc/rules/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ typos, conflicts, ignores, template: tpl, enable_r19, r19_sensitivity,
                             disabled_typos: Array.from(_typoDisabled) })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    // 保存成功后刷新快照与统计（保持「撤销」基准与已保存一致）
    _cfgSnapshot = {
      typos: document.getElementById('cfgTypos').value,
      conflicts: document.getElementById('cfgConflicts').value,
      ignores: document.getElementById('cfgIgnores').value,
    };
    // 2026-08-18：重建可视化词库缓存并重渲染——此前直接编辑左侧词表保存后，
    // 右侧表格仍显示旧数据；且新增词若同时在停用列表会存成『新增且停用』矛盾态
    try {
      _typoCache = {};
      _typoDisabled = new Set();
      const raw = document.getElementById('cfgTypos').value;
      for (const ln of raw.split('\n')) {
        const m = ln.match(/^(.+?)[\t|：:]\s*(.+?)\s*\[(.+?)\]\s*$/);
        if (m) _typoCache[m[1].trim()] = m[2].trim();
      }
      const dis = document.getElementById('cfgDisabledTypos');
      if (dis) {
        for (const w of dis.value.split(/[\n,，]+/).map(x => x.trim()).filter(Boolean)) _typoDisabled.add(w);
      }
      renderTypoTable();
    } catch (e) { console.warn('词库缓存重建失败', e); }
    updateCfgStats();
    toast(`规则配置已保存并生效（错字 ${Object.keys(typos).length} 条 / 矛盾对 ${conflicts.length} 条 / 忽略词 ${ignores.length} 条）`, 'success');
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  }
}

// ==================== 错别字词库可视化维护（R8：搜索/新增/启停/删除/批量导入） ====================
let _typoDisabled = new Set();
let _typoCache = {};   // {wrong: correct}

function renderTypoTable() {
  const tbody = document.getElementById('typoTableBody');
  const cntEl = document.getElementById('typoCount');
  if (!tbody) return;
  const kw = (document.getElementById('typoSearch').value || '').trim();
  const keys = Object.keys(_typoCache).sort((a, b) => a.localeCompare(b, 'zh'));
  const rows = keys.filter(k => !kw || k.includes(kw) || _typoCache[k].includes(kw));
  if (cntEl) cntEl.textContent = `${rows.length} / ${keys.length} 条`;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:16px;">'
      + (keys.length ? '没有匹配的词条' : '词库为空，点「＋ 新增」或「📥 批量导入」添加') + '</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(k => {
    const on = !_typoDisabled.has(k);
    return `<tr>
      <td><span class="typo-status ${on ? 'on' : 'off'}" title="${on ? '已启用' : '已停用'}"></span></td>
      <td>${escapeHtml(k)}</td>
      <td>${escapeHtml(_typoCache[k] || '')}</td>
      <td style="white-space:nowrap;">
        <button class="btn btn-xs ${on ? 'typoff-btn' : 'apply-fix-btn'}" onclick="toggleTypoItem('${escapeHtml(k)}', ${on})">${on ? '停用' : '启用'}</button>
        <button class="btn btn-xs danger-outline" onclick="deleteTypoItem('${escapeHtml(k)}')">删除</button>
      </td>
    </tr>`;
  }).join('');
}

function openTypoAddModal() {
  document.getElementById('typoAddWrong').value = '';
  document.getElementById('typoAddCorrect').value = '';
  document.getElementById('typoAddModal').style.display = 'flex';
}
function closeTypoAddModal() { document.getElementById('typoAddModal').style.display = 'none'; }

async function addTypoItem() {
  const wrong = document.getElementById('typoAddWrong').value.trim();
  const correct = document.getElementById('typoAddCorrect').value.trim();
  if (!wrong || !correct || wrong === correct) { toast('错词与正词均必填且不能相同', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '新增失败');
    _typoCache[wrong] = correct;
    _typoDisabled.delete(wrong);
    renderTypoTable();
    closeTypoAddModal();
    _refreshCfgTextarea();
    toast(data.message || '已新增', 'success');
  } catch (e) { toast('新增失败: ' + e.message, 'error'); }
}

async function toggleTypoItem(wrong, currentOn) {
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/toggle', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong, correct: currentOn ? '0' : '1' })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '操作失败');
    if (currentOn) _typoDisabled.add(wrong); else _typoDisabled.delete(wrong);
    renderTypoTable();
    toast(data.message || '已更新', 'success');
  } catch (e) { toast('操作失败: ' + e.message, 'error'); }
}

async function deleteTypoItem(wrong) {
  if (!confirm(`确定删除错字词条「${wrong}」吗？`)) return;
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wrong })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '删除失败');
    delete _typoCache[wrong];
    _typoDisabled.delete(wrong);
    renderTypoTable();
    _refreshCfgTextarea();
    toast(data.message || '已删除', 'success');
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

function openTypoImportModal() {
  document.getElementById('typoImportText').value = '';
  document.getElementById('typoImportModal').style.display = 'flex';
}
function closeTypoImportModal() { document.getElementById('typoImportModal').style.display = 'none'; }

async function importTypoItems() {
  const raw = document.getElementById('typoImportText').value;
  const items = raw.split('\n').map(s => s.trim()).filter(Boolean).map(line => {
    const i = line.indexOf('=');
    if (i <= 0) return null;
    return [line.slice(0, i).trim(), line.slice(i + 1).trim()];
  }).filter(Boolean);
  if (!items.length) { toast('请输入至少一条 错词=正词', 'warn'); return; }
  try {
    const res = await apiFetch('/api/v1/qc/rules/typos/batch-import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '导入失败');
    for (const [w, c] of items) { if (c) { _typoCache[w] = c; _typoDisabled.delete(w); } }
    renderTypoTable();
    closeTypoImportModal();
    _refreshCfgTextarea();
    toast(data.message || `成功 ${data.data.ok} 条`, 'success');
  } catch (e) { toast('导入失败: ' + e.message, 'error'); }
}

/** 词库变更后，同步更新左侧 textarea（保持两种维护方式一致） */
function _refreshCfgTextarea() {
  const ta = document.getElementById('cfgTypos');
  if (!ta) return;
  ta.value = Object.keys(_typoCache).sort((a, b) => a.localeCompare(b, 'zh'))
    .map(k => `${k}=${_typoCache[k]}`).join('\n');
  updateCfgStats();
}

// 恢复默认规则库（内置出厂配置），操作前需确认
async function resetRulesConfig() {
  if (!confirm('确定恢复默认规则库吗？当前所有自定义规则将被出厂配置覆盖。')) return;
  try {
    const res = await apiFetch('/api/v1/qc/rules/config/reset', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '恢复失败');
    _cfgSnapshot = null;
    await loadRulesConfig();
    toast('已恢复默认规则库', 'success');
  } catch (e) {
    toast('恢复失败: ' + e.message, 'error');
  }
}

async function importSamples() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.csv,.json';
  inp.onchange = async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    try {
      toast('正在导入样本...', 'info');
      const res = await apiFetch('/api/v1/samples/import/upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (!data.ok) throw new Error(data.message || '导入失败');
      toast(`导入完成：新增 ${data.data.inserted} 条，跳过 ${data.data.skipped} 条`, 'success');
      loadSamples();
    } catch (e) {
      toast('导入失败: ' + e.message, 'error');
    }
  };
  inp.click();
}

// ==================== RIS 直连 ====================
// ---------- P0 主动轮询质检（后台定时线程：拉取→质控→入库+入队） ----------
async function loadPollStatus() {
  try {
    const res = await apiFetch('/api/v1/ris/poll-status');
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '加载失败');
    const st = data.data || {};
    const on = !!st.enabled;
    const stEl = document.getElementById('pollStatus');
    if (stEl) {
      stEl.className = 'conn-status ' + (on ? 'connected' : 'disconnected');
      stEl.innerHTML = `<span class="led"></span> ${on ? '轮询中' : '已关闭'}`;
    }
    const cb = document.getElementById('pollEnabled'); if (cb) cb.checked = on;
    const iv = document.getElementById('pollInterval'); if (iv) iv.value = st.interval_min || 30;
    const lim = document.getElementById('pollLimit'); if (lim) lim.value = st.limit || 50;
    const aq = document.getElementById('pollAutoQc'); if (aq) aq.checked = st.auto_qc !== false;
    const ae = document.getElementById('pollAutoEnqueue'); if (ae) ae.checked = st.auto_enqueue !== false;
    const meta = document.getElementById('pollMeta');
    if (meta) {
      meta.innerHTML = (st.last_run ? `上次运行：${st.last_run} · ` : '') +
        `上次新增 ${st.last_count || 0} 份 · 已去重指纹 ${st.seen_count || 0}` +
        (st.last_error ? `<br/><span style="color:#e53e3e;">最近错误：${escapeHtml(st.last_error)}</span>` : '');
    }
  } catch (e) { /* 页面可能未加载完成，静默 */ }
  loadRisConfig(); // 进入 RIS 页顺带回填已保存连接配置（2026-08-18）
}

async function savePollConfig() {
  const cb = document.getElementById('pollEnabled');
  const iv = document.getElementById('pollInterval');
  const lim = document.getElementById('pollLimit');
  const aq = document.getElementById('pollAutoQc');
  const ae = document.getElementById('pollAutoEnqueue');
  const body = {
    enabled: cb ? cb.checked : false,
    interval_min: iv ? parseInt(iv.value) || 30 : 30,
    limit: lim ? parseInt(lim.value) || 50 : 50,
    auto_qc: aq ? aq.checked : true,
    auto_enqueue: ae ? ae.checked : true,
  };
  try {
    const res = await apiFetch('/api/v1/ris/poll-config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    loadPollStatus();
    toast('轮询配置已保存' + (body.enabled ? '，轮询已启动' : ''), 'success');
  } catch (e) { toast('保存轮询配置失败: ' + e.message, 'error'); }
}

async function runPollNow() {
  toast('正在执行一次 RIS 轮询...', 'info');
  try {
    const res = await apiFetch('/api/v1/ris/poll-now', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) { toast('轮询失败: ' + (data.message || data.detail || ''), 'error'); loadPollStatus(); return; }
    const r = data.data || {};
    toast(`轮询完成：新增 ${r.count || 0} 份报告，累计指纹 ${r.total_seen || 0}`, 'success');
    loadPollStatus();
    if ((r.count || 0) > 0) { loadQueue(); loadSamples(); loadDashboard(); }
  } catch (e) { toast('轮询请求失败: ' + e.message, 'error'); }
}

// RIS 连接表单统一收集（test-connection / fetch / save 三处同源，2026-08-18）
function _risFormPayload() {
  return {
    db_type:   document.getElementById('risDbType').value,
    host:      document.getElementById('risHost').value,
    port:      parseInt(document.getElementById('risPort').value)||0,
    database:  document.getElementById('risDbName').value,
    user:      document.getElementById('risUser').value,
    password:  document.getElementById('risPassword').value,
    query_sql: document.getElementById('risSql').value,
  };
}

// 保存 RIS 连接配置（PUT /ris/config，admin）；轮询线程复用持久化配置
async function saveRisConfig() {
  const btn = document.getElementById('risSaveBtn');
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch('/api/v1/ris/config', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(_risFormPayload()),
    });
    const data = await res.json();
    if (data.ok) { toast('RIS 配置已保存', 'success'); loadPollStatus(); }
    else toast(data.message || data.detail || '保存失败', 'error');
  } catch (e) { toast('保存失败: ' + e.message, 'error'); }
  finally { if (btn) btn.disabled = false; }
}

// 进入 RIS 页时回填已保存配置（GET /ris/config，password 脱敏不回填）
async function loadRisConfig() {
  try {
    const res = await apiFetch('/api/v1/ris/config');
    const data = await res.json();
    if (!data.ok) return;
    const cfg = data.data || {};
    const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
    set('risDbType', cfg.db_type); set('risHost', cfg.host); set('risPort', cfg.port);
    set('risDbName', cfg.database); set('risUser', cfg.user); set('risSql', cfg.query);
  } catch (e) { /* 静默：可能未配置或页面未加载完成 */ }
}

async function testRisConnection() {
  const statusEl = document.getElementById('connStatus');
  statusEl.className = 'conn-status disconnected';
  statusEl.innerHTML = '<span class="led"></span> 测试中...';

  try {
    const res = await apiFetch('/api/v1/ris/test-connection', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(_risFormPayload()),
    });
    const data = await res.json();
    if (data.ok && data.data && data.data.ok) {
      statusEl.className = 'conn-status connected';
      statusEl.innerHTML = '<span class="led"></span> 已连接';
      toast('连接测试成功！', 'success');
    } else {
      statusEl.className = 'conn-status error';
      statusEl.innerHTML = '<span class="led"></span> 连接失败';
      toast('连接失败: ' + (data.data?.message||data.message), 'error');
    }
  } catch(e) {
    statusEl.className = 'conn-status error';
    statusEl.innerHTML = '<span class="led"></span> 请求异常';
    toast('连接测试异常: ' + e.message, 'error');
  }
}

let risController = null;
let risItems = [];
async function fetchRisReports() {
  const prog = document.getElementById('risProgress');
  const cancelBtn = document.getElementById('risCancelBtn');
  if (risController) risController.abort();   // 取消上次未完成的请求
  risController = new AbortController();
  if (prog) prog.classList.add('show');
  if (cancelBtn) cancelBtn.style.display = 'inline-flex';
  const myCtrl = risController;
  try {
    const res = await apiFetch('/api/v1/ris/fetch-reports', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        db_type:   document.getElementById('risDbType').value,
        host:      document.getElementById('risHost').value,
        port:      parseInt(document.getElementById('risPort').value)||0,
        database:  document.getElementById('risDbName').value,
        user:      document.getElementById('risUser').value,
        password:  document.getElementById('risPassword').value,
        query_sql: document.getElementById('risSql').value,
      }),
      signal: myCtrl.signal,   // limit 由后端 Query 默认 50（契约对齐，2026-08-18）
    });
    const data = await res.json();
    const items = (data.data||{}).items||[];
    risItems = items;
    document.getElementById('risResultCount').textContent = items.length + ' 条';

    const tbody = document.getElementById('risBody');
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:32px;">无数据，请检查 SQL 或连接配置</td></tr>';
      return;
    }

    tbody.innerHTML = items.map((r, i) => `
      <tr>
        <td style="text-align:center;"><input type="checkbox" class="ris-check" value="${i}"></td>
        <td>${escapeHtml(r.patient||'--')}</td>
        <td style="font-size:12px;">${escapeHtml(r.gender||'--')}/${escapeHtml(r.age||'--')}</td>
        <td><span class="tag info">${escapeHtml(r.modality||'--')}</span></td>
        <td>${escapeHtml(r.applied_site||'--')}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;" title="${escapeHtml(r.report_text||'')}">${escapeHtml((r.report_text||'').slice(0,80))}</td>
      </tr>
    `).join('');

    toast(`成功拉取 ${items.length} 条报告`, 'success');
  } catch(e) {
    if (e.name === 'AbortError') toast('已取消拉取', 'info');
    else toast('拉取失败: '+e.message, 'error');
  } finally {
    // 仅当本请求仍是当前控制器时才置空（防止 abort 的旧请求清掉新建的控制器 B，
    // 导致第三次触发时无法取消 B，进度条状态互相覆盖；2026-08-18 修复）
    if (risController === myCtrl) risController = null;
    if (prog) prog.classList.remove('show');
    if (cancelBtn && !risController) cancelBtn.style.display = 'none';
  }
}

function cancelRis() {
  if (risController) risController.abort();
}

/** 收集 RIS 表格中勾选的行 */
function getCheckedRisItems() {
  const checked = [...document.querySelectorAll('#risBody .ris-check:checked')]
    .map(cb => risItems[parseInt(cb.value, 10)])
    .filter(Boolean);
  return checked;
}

/** 选中的报告送入左侧工作区并立即跑质控 + 入队 */
async function sendToQC() {
  const sel = getCheckedRisItems();
  if (!sel.length) { toast('请先在表格左侧勾选要质控的报告', 'error'); return; }
  const r = sel[0];
  const parts = splitReportSections(r.report_text || '');
  setVal('mPatient', r.patient);
  setVal('mGender', r.gender);
  setVal('mAge', r.age);
  setVal('mModality', r.modality);
  setVal('mSite', r.applied_site);
  document.getElementById('findingsText').value = parts.findings;
  document.getElementById('impressionText').value = parts.impression;
  document.getElementById('findingsCount').textContent = parts.findings.length + ' 字';
  document.getElementById('impressionCount').textContent = parts.impression.length + ' 字';
  ACTIVE_QUEUE_ID = null;
  gotoPage('qc');
  await runQC();
  if (APP_SETTINGS.auto_enqueue) await enqueueCurrent('RIS', true);
  toast(`已将选中报告载入工作区${sel.length > 1 ? `（另有 ${sel.length - 1} 份未处理，可点批量质控）` : ''}`, 'success');
}

/** 全部拉取结果 → 引擎 → 样本库（批量质控入库） */
async function batchQC() {
  if (!risItems.length) { toast('没有可质控的报告，请先拉取', 'error'); return; }
  if (!confirm(`将把 ${risItems.length} 份报告逐份质控并入库，继续？`)) return;
  let okCount = 0, failCount = 0;
  for (const r of risItems) {
    if (!r.report_text) { failCount++; continue; }
    try {
      const res = await apiFetch('/api/v1/samples', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          report: r.report_text,
          meta: { patient: r.patient, gender: r.gender, age: r.age, modality: r.modality, applied_site: r.applied_site },
          anonymize: !!APP_SETTINGS.anonymize,
          user_id: APP_SETTINGS.emp_id,
        })
      });
      const d = await res.json();
      if (d.ok) okCount++; else failCount++;
    } catch (e) { failCount++; }
  }
  toast(`批量质控完成：入库 ${okCount} 份${failCount ? '，失败 ' + failCount + ' 份' : ''}`, failCount ? 'warning' : 'success');
}

/** 全部加入待质控队列 */
async function risEnqueueAll() {
  if (!risItems.length) { toast('没有可加入队列的报告，请先拉取', 'error'); return; }
  let n = 0;
  for (const r of risItems) {
    if (!r.report_text) continue;
    const id = await enqueueText(r.report_text, {
      patient: r.patient, gender: r.gender, age: r.age,
      modality: r.modality, applied_site: r.applied_site,
    }, 'RIS', true);
    if (id) n++;
  }
  toast(`已将 ${n} 份报告加入待质控队列`, n ? 'success' : 'info');
}

// ==================== 规则维护 ====================
async function loadRules() {
  try {
    const res = await apiFetch('/api/v1/qc/rules');
    const data = await res.json();
    const rules = (data.data||[]);

    const tbody = document.getElementById('rulesBody');
    if (!rules.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:32px;">无规则数据</td></tr>';
      return;
    }

    tbody.innerHTML = rules.map(r => `
      <tr>
        <td style="font-family:monospace;font-size:12px;">${r.rule_id||'--'}</td>
        <td>${r.name||'--'}</td>
        <td><span class="tag info">${r.category||'--'}</span></td>
        <td><span class="tag ${(SEV_META[r.severity]||{}).cls||'info'}">${r.severity||'info'}</span></td>
        <td><span class="tag ${r.enabled!==false?'success':'warning'}">${r.enabled!==false?'启用':'禁用'}</span></td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ==================== 工具函数 ====================
// ==================== 明暗主题切换 ====================
// ==================== 全局快捷键 ====================
// ==================== 框选 OCR（三段识别） ====================
// 三区对应 PACS：basic=病人基础信息 / findings=影像描述 / impression=影像诊断
// 后端 /api/v1/screen/ocr 按 basic/findings/impression 键返回，故框 key 与之对齐。
const OCR_MODAL = 'ocrModal';
const ocrState = {
  mode: 'file',        // 'file'=本地上传/粘贴图；'screen'=截取 PACS 全屏
  img: null, naturalW: 0, naturalH: 0,
  screen: null,        // { b64, width, height, thumb_width, thumb_height }
  // 框以图片显示尺寸的比例表示 (0~1)
  boxes: [
    { key: 'basic',      name: '病人基础信息', color: '#3b82f6', x: 0.03, y: 0.02, w: 0.94, h: 0.26 },
    { key: 'findings',   name: '影像描述',     color: '#22c55e', x: 0.03, y: 0.30, w: 0.94, h: 0.32 },
    { key: 'impression', name: '影像诊断',     color: '#f59e0b', x: 0.03, y: 0.64, w: 0.94, h: 0.33 },
  ],
  drag: null,
};

function openOcrModal() {
  if (!document.getElementById('page-qc').classList.contains('active')) switchPage('qc', document.querySelector('.nav-cell[data-page="qc"]'));
  document.getElementById(OCR_MODAL).style.display = 'flex';
  // 载入上次「记住框位」保存的比例框
  apiFetch('/api/v1/screen/regions').then(r => r.json()).then(d => {
    const wr = (d.data || {}).web_regions;
    if (wr && typeof wr === 'object') {
      ocrState.boxes.forEach(b => {
        if (wr[b.key]) Object.assign(b, {
          x: +wr[b.key].x, y: +wr[b.key].y, w: +wr[b.key].w, h: +wr[b.key].h
        });
      });
      if (ocrState.img) ocrRender();
    }
  }).catch(() => {});
}
function closeOcrModal() { document.getElementById(OCR_MODAL).style.display = 'none'; }

function ocrResetBoxes() {
  ocrState.boxes = [
    { key: 'basic',      name: '病人基础信息', color: '#3b82f6', x: 0.03, y: 0.02, w: 0.94, h: 0.26 },
    { key: 'findings',   name: '影像描述',     color: '#22c55e', x: 0.03, y: 0.30, w: 0.94, h: 0.32 },
    { key: 'impression', name: '影像诊断',     color: '#f59e0b', x: 0.03, y: 0.64, w: 0.94, h: 0.33 },
  ];
  ocrRender();
}

function ocrLoadFile(e) {
  const f = e.target.files && e.target.files[0];
  if (f) ocrSetImageFromUrl(URL.createObjectURL(f));
}
function ocrSetImageFromUrl(url) {
  ocrState.mode = 'file';
  ocrState.screen = null;
  const img = new Image();
  img.onload = () => {
    ocrState.img = img;
    ocrState.naturalW = img.naturalWidth;
    ocrState.naturalH = img.naturalHeight;
    document.getElementById('ocrPlaceholder').style.display = 'none';
    document.getElementById('ocrCanvas').style.display = 'block';
    ocrRender();
  };
  img.onerror = () => toast('图片加载失败', 'error');
  img.src = url;
}

/** 截取 PACS 全屏：调用后端 /api/v1/screen/capture，原图缓存在服务端，前端只拿缩略图 */
async function ocrGrabScreen() {
  const hint = document.getElementById('ocrSourceHint');
  const status = document.getElementById('ocrStatus');
  if (status) status.textContent = '正在截取全屏...';
  try {
    const res = await apiFetch('/api/v1/screen/capture', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '截屏失败');
    const d = data.data;
    ocrState.mode = 'screen';
    ocrState.screen = {
      b64: d.image_base64, width: d.width, height: d.height,
      thumb_width: d.thumb_width, thumb_height: d.thumb_height,
    };
    ocrState.naturalW = d.thumb_width;     // 画布按缩略图尺寸渲染，比例框 0~1 通用
    ocrState.naturalH = d.thumb_height;
    const img = new Image();
    img.onload = () => {
      ocrState.img = img;
      document.getElementById('ocrPlaceholder').style.display = 'none';
      document.getElementById('ocrCanvas').style.display = 'block';
      ocrRender();
    };
    img.src = 'data:image/png;base64,' + d.image_base64;
    if (hint) hint.textContent = `已截取 ${d.width}×${d.height}（缩略显示），拖动三框分别框选后点识别`;
    if (status) status.textContent = '';
    toast('已截取全屏，拖动三框框选区域', 'success');
  } catch (e) {
    if (status) status.textContent = '';
    toast('截屏失败: ' + e.message, 'error');
  }
}

/** 持久化当前框位（PUT /api/v1/screen/regions），下次进入自动复原 */
async function ocrSaveRegions() {
  try {
    const regions = {};
    ocrState.boxes.forEach(b => {
      regions[b.key] = {
        x: +b.x.toFixed(4), y: +b.y.toFixed(4),
        w: +b.w.toFixed(4), h: +b.h.toFixed(4),
      };
    });
    const res = await apiFetch('/api/v1/screen/regions', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(regions)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    toast('已记住框位，下次自动复原', 'success');
  } catch (e) {
    toast('保存框位失败: ' + e.message, 'error');
  }
}

// 粘贴截图（挂 document 而非 ocrCanvasWrap：paste 事件只到达聚焦元素，
// 画布无 tabindex 时事件不会到达该容器，2026-08-18 修复粘贴失效）
document.addEventListener('paste', (e) => {
  if (document.getElementById(OCR_MODAL).style.display !== 'flex') return;
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const it of items) {
    if (it.type.startsWith('image/')) {
      ocrSetImageFromUrl(URL.createObjectURL(it.getAsFile()));
      e.preventDefault();
      break;
    }
  }
});

function ocrScale() {
  const cv = document.getElementById('ocrCanvas');
  const wrap = document.getElementById('ocrCanvasWrap');
  const maxW = wrap.clientWidth - 12;
  const maxH = 460;
  let w = ocrState.naturalW, h = ocrState.naturalH;
  const r = Math.min(maxW / w, maxH / h, 1);
  w = Math.round(w * r); h = Math.round(h * r);
  if (cv.width !== w) cv.width = w;
  if (cv.height !== h) cv.height = h;
  return { w, h };
}

function ocrRender() {
  const cv = document.getElementById('ocrCanvas');
  const ctx = cv.getContext('2d');
  if (!ocrState.img) return;
  const { w, h } = ocrScale();
  ctx.clearRect(0, 0, w, h);
  ctx.drawImage(ocrState.img, 0, 0, w, h);
  ocrState.boxes.forEach((b, idx) => {
    const x = b.x * w, y = b.y * h, bw = b.w * w, bh = b.h * h;
    ctx.strokeStyle = b.color; ctx.lineWidth = 2;
    ctx.strokeRect(x, y, bw, bh);
    ctx.fillStyle = b.color; ctx.font = '12px sans-serif';
    const tw = ctx.measureText(b.name).width + 10;
    ctx.fillRect(x, y - 18, tw, 18);
    ctx.fillStyle = '#fff'; ctx.fillText(b.name, x + 5, y - 5);
    ctx.fillStyle = b.color;
    ctx.fillRect(x + bw - 10, y + bh - 10, 10, 10);
    ctx.beginPath(); ctx.arc(x + 12, y + 12, 9, 0, 7); ctx.fill();
    ctx.fillStyle = b.color; ctx.font = 'bold 11px sans-serif';
    ctx.fillText(String(idx + 1), x + 8, y + 16);
  });
}

// 鼠标交互：移动 / 缩放
(function bindOcrCanvas() {
  const cv = document.getElementById('ocrCanvas');
  function pos(e) {
    const rect = cv.getBoundingClientRect();
    return { px: e.clientX - rect.left, py: e.clientY - rect.top };
  }
  cv.addEventListener('mousedown', (e) => {
    const p = pos(e);
    const { w, h } = ocrScale();
    for (let i = ocrState.boxes.length - 1; i >= 0; i--) {
      const b = ocrState.boxes[i];
      const bx = b.x * w, by = b.y * h, bw = b.w * w, bh = b.h * h;
      if (p.px >= bx + bw - 12 && p.px <= bx + bw + 2 && p.py >= by + bh - 12 && p.py <= by + bh + 2) {
        ocrState.drag = { idx: i, mode: 'resize' }; return;
      }
      if (p.px >= bx && p.px <= bx + bw && p.py >= by && p.py <= by + bh) {
        ocrState.drag = { idx: i, mode: 'move', ox: p.px - bx, oy: p.py - by }; return;
      }
    }
  });
  window.addEventListener('mousemove', (e) => {
    if (!ocrState.drag) return;
    const p = pos(e);
    const { w, h } = ocrScale();
    const b = ocrState.boxes[ocrState.drag.idx];
    if (ocrState.drag.mode === 'move') {
      b.x = Math.max(0, Math.min(1 - b.w, (p.px - ocrState.drag.ox) / w));
      b.y = Math.max(0, Math.min(1 - b.h, (p.py - ocrState.drag.oy) / h));
    } else {
      b.w = Math.max(0.05, Math.min(1 - b.x, (p.px - b.x * w) / w));
      b.h = Math.max(0.05, Math.min(1 - b.y, (p.py - b.y * h) / h));
    }
    ocrRender();
  });
  window.addEventListener('mouseup', () => { ocrState.drag = null; });
})();

async function ocrCropAndRecognize(box) {
  const off = document.createElement('canvas');
  off.width = Math.max(1, Math.round(box.w * ocrState.naturalW));
  off.height = Math.max(1, Math.round(box.h * ocrState.naturalH));
  const octx = off.getContext('2d');
  octx.drawImage(ocrState.img, box.x * ocrState.naturalW, box.y * ocrState.naturalH, off.width, off.height, 0, 0, off.width, off.height);
  const b64 = off.toDataURL('image/png').split(',')[1];
  const res = await apiFetch('/api/v1/ocr/base64', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_base64: b64 })
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.message || 'OCR 失败');
  return (data.data && data.data.text) || '';
}

async function ocrRecognize() {
  const status = document.getElementById('ocrStatus');
  const btn = document.getElementById('ocrRecognizeBtn');
  const setBusy = (on) => {
    if (status) status.textContent = on ? 'OCR 推理中（画面未变的区域会直接复用，不重复计算）…' : '';
    if (btn) { btn.disabled = on; btn.textContent = on ? '识别中…' : '识别并填充'; }
  };
  if (ocrState.mode === 'screen') {
    if (!ocrState.screen) { toast('请先点「🖥 截取 PACS 画面」', 'error'); return; }
    setBusy(true);
    try {
      const regions = {};
      ocrState.boxes.forEach(b => regions[b.key] = {
        x: +b.x.toFixed(4), y: +b.y.toFixed(4), w: +b.w.toFixed(4), h: +b.h.toFixed(4)
      });
      const refresh = document.getElementById('ocrRefresh')?.checked;
      const res = await apiFetch('/api/v1/screen/ocr', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions, refresh, dynamic: !!APP_SETTINGS.ocr_dynamic, dynamic_region: APP_SETTINGS.ocr_dynamic ? unionRegions(regions) : null })
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.message || 'OCR 失败');
      const t = (data.data && data.data.texts) || {};
      ocrFill({ basic: t.basic, findings: t.findings, impression: t.impression },
               (data.data && data.data.meta) || null);
      toast('三段识别完成，已填充到对应区域', 'success');
    } catch (e) { toast('OCR 出错: ' + e.message, 'error'); }
    finally { setBusy(false); }
    return;
  }
  // 文件 / 粘贴图模式：本地裁剪三段后逐张调 /api/v1/ocr/base64
  if (!ocrState.img) { toast('请先粘贴或选择报告截图', 'error'); return; }
  setBusy(true);
  try {
    const results = await Promise.all(ocrState.boxes.map(async (b) => ({ key: b.key, text: await ocrCropAndRecognize(b) })));
    const map = {}; results.forEach(r => map[r.key] = r.text);
    // 图片模式也走后端 extract_meta_full（与屏幕模式一致），姓名回填更稳健；失败则回退前端解析
    let meta = null;
    try {
      const mr = await apiFetch('/api/v1/ocr/meta', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ basic: map.basic || '', findings: map.findings || '', impression: map.impression || '' })
      });
      const md = await mr.json();
      if (md.ok && md.data && md.data.meta) meta = md.data.meta;
    } catch (e) { /* 忽略，走前端兜底 */ }
    ocrFill(map, meta);
    toast('三段识别完成，已填充到对应区域', 'success');
  } catch (e) { toast('OCR 出错: ' + e.message, 'error'); }
  finally { setBusy(false); }
}

// 姓名/性别边界词：识别到这些词即视为「下一个字段」，截断姓名提取，避免吞入性别/年龄
const NAME_BOUND = /(性别|年龄|岁|年|月|日|检查|部位|科室|门诊|住院|床号|住院号|临床|设备|医院|影像|诊断|申请|病案|[男女]|male|female|\d)/i;
function parseName(raw) {
  if (!raw) return '';
  if (/[\u4e00-\u9fa5]/.test(raw)) {
    let s = raw.replace(/\s+/g, '');           // 去掉 OCR 误插的空格（中文名）
    const cut = s.search(NAME_BOUND);
    if (cut > 0) s = s.slice(0, cut);          // 截断到字段边界（性别/年龄等）
    return s;
  }
  const cut = raw.search(new RegExp(NAME_BOUND.source, 'i'));
  return cut > 0 ? raw.slice(0, cut).trim() : raw.trim();
}
function parsePatientInfo(text) {
  const out = {}; let m;
  // 噪声词（字段词/科室词）：无标签兜底时排除，避免把科室/字段误填成姓名。
  // 2026-08-18 补「患者/病人/受检者/就诊」：无分隔写法『患者张三』此前兜底2 会把
  // 人称词本身误当姓名（NOISE 缺词），现排除后仍取不到则交给兜底3（整行 2-3 字）不匹配，返回空。
  const NOISE = /(患者|病人|受检者|就诊|性别|年龄|检查|部位|科室|门诊|住院|床号|影像|诊断|申请|病案|临床|设备|医院|报告|记录|呼吸|心血管|神经|骨科|普外|泌尿|妇科|产科|儿科|急诊|超声|放射|肿瘤|消化|内分泌|免疫|血液|皮肤|眼科|耳鼻喉|口腔|中医|康复|病理|心电|核医学|受理|登记|来源|类型|方法|所见|印象|建议|征象|结论|提示|说明|病床|住院号|门诊号|检查号|影像号)/;
  const COMPOUND = /^(欧阳|司马|诸葛|东方|上官|令狐|皇甫|宇文|慕容|司徒|夏侯|长孙|赫连|万俟|闻人|澹台|尉迟|公孙)/;
  // 部位词（兜底2/3 共用）：排除『胸部』『腰椎』等部位词，避免把部位误填成姓名
  const BODYPART = /^(头部|颈部|胸部|腹部|盆腔|腰部|骶部|尾部|颅脑|头颅|鼻窦|眼眶|涎腺|鼻咽|口咽|喉部|甲状腺|上腹部|中腹部|下腹部|肾上腺|肝脏|胆囊|胰腺|脾脏|肾脏|胃肠|膀胱|前列腺|子宫|卵巢|四肢|关节|肩关节|肘关节|腕关节|髋关节|膝关节|踝关节|腰椎|颈椎|胸椎|骶骨|尾骨|股骨|胫骨|腓骨|肱骨|尺骨|桡骨|骨盆|肋骨|锁骨|脑|颈|胸|腹|盆|腰|骶|颅|颌|面|眼|耳|鼻|咽|喉|肺|肝|胆|胰|脾|肾|胃|肠|膀|乳|肩|肘|腕|髋|膝|踝|指|趾|脊|椎|骨|肋|锁|股|胫|腓|肱|桡)/;
  // 人称/性别/年龄标识（兜底2 共用）
  const PERSON = /(患者?|就\s*诊|受\s*检|病\s*员|病\s*人|name|patient|性\s*别|年\s*龄|男|女|\d)/i;
  // 姓名：优先按标签（姓名/患者姓名/病人姓名/name）提取；OCR 常把姓名与性别连写或插入空格，需清洗
  // 姓名标签对 OCR 误读容错：姓各/性名/姓 名(插空格)/忠者(患者误读)/就诊人/受检者/Patient 等
  const nameRe = /(?:姓\s*[名各]|性\s*[名各]|名\s*[:：]|患\s*[者吉][:：\s\u3000]+|忠\s*[者吉][:：\s\u3000]+|病\s*[人欠员][:：\s\u3000]+|受\s*检\s*者[:：\s\u3000]+|就\s*诊\s*人[:：\s\u3000]+|患者?姓名|病人姓名|name|patient)[:：]?\s*([\u4e00-\u9fa5A-Za-z]+(?:\s+[\u4e00-\u9fa5A-Za-z]+){0,3})/i;
  if ((m = text.match(nameRe))) {
    let cand = parseName(m[1]);
    // 姓名后若紧跟性别/年龄/字段词（『赵六性别』『王五男』），从中截断只留姓名，
    // 与后端 _name_char 行为对齐（遇性别/年龄/字段词即停）。
    cand = cand.replace(/^(.*?)(性别|年龄|男|女|检查|部位|科室|影像|诊断|申请|病案|门诊|住院|床号|sex|male|female).*$/i, '$1');
    out.patient = cand.trim();
  }
  // 兜底1：无标签的 PACS 列表格式「张三 男 45Y」，按「中文串 + 性别字」启发式提取。
  // 优先 2-3 字，候选含科室首字则从左剥离（呼吸内科→张三），避免把科室吞入姓名。
  if (!out.patient) {
    const compact = text.replace(/\s+/g, '');
    const DEPT = '呼吸内科外科科室门诊住院急诊放射超声影像神经骨科泌尿妇科产科儿科肿瘤消化内分泌免疫血液皮肤眼科耳鼻喉口腔中医康复病理心电核医学';
    const stripDept = (s) => { while (s.length > 2 && DEPT.includes(s[0])) s = s.slice(1); return s; };
    let m3 = compact.match(/([\u4e00-\u9fa5]{2,3})[男女]/);
    if (m3) { const c = stripDept(m3[1]); if (c.length >= 2 && !NOISE.test(c)) out.patient = c; }
    if (!out.patient) {
      m3 = compact.match(/([\u4e00-\u9fa5]{4})[男女]/);
      if (m3) { const c = stripDept(m3[1]); if (c.length >= 2 && COMPOUND.test(c) && !NOISE.test(c)) out.patient = c; }
    }
  }
  // 兜底2：完全无标签/无性别字时，仅在「整行无字段词」且行内含『人称/性别/年龄』标识的行中，
  // 取行首像人名的 2-3 字中文串，排除字段词/科室词/部位词，避免把部位/正文误填成姓名。
  if (!out.patient) {
    for (const line of text.split(/\n+/)) {
      if (NOISE.test(line)) continue;
      if (!PERSON.test(line)) continue;
      const mm = line.match(/^\s*(?:[A-Za-z0-9\-]+)?\s*([\u4e00-\u9fa5]{2,3}(?:\s*[\u4e00-\u9fa5])?)/);
      if (!mm) continue;
      const c = mm[1].replace(/\s+/g, '');
      if (c.length < 2) continue;
      if (NOISE.test(c) || BODYPART.test(c)) continue;
      if (c.length <= 3 || COMPOUND.test(c)) { out.patient = c; break; }
    }
  }
  // 兜底3：整行恰好就是 2-3 字中文串（如姓名独占一行的 PACS 大字段：张三 / 王小明），
  // 无标签也无性别字，兜底1/2 都会漏。用 fullmatch 限定「整行即姓名」，排除「未见实质性病灶」
  // 这类长句；部位词(BODYPART)仍排除，故『胸部』『腰椎』不会误填。仅作最后兜底。
  if (!out.patient) {
    for (const line of text.split(/\n+/)) {
      const s = line.trim();
      if (!/^[\u4e00-\u9fa5]{2,3}$/.test(s)) continue;
      if (NOISE.test(s) || BODYPART.test(s)) continue;
      out.patient = s; break;
    }
  }
  if (!out.patient) out.patient = '';
  // 性别：归一化（男/M/male → 男；女/F/female → 女）
  if ((m = text.match(/(?:性别|gender|sex)[:：]?\s*(男|女|M|F|male|female)/i))) {
    const g = m[1].toLowerCase();
    out.gender = (g === 'm' || g === 'male' || g === '男') ? '男' : '女';
  } else if (/男/.test(text) && !/女/.test(text)) {
    out.gender = '男';
  } else if (/女/.test(text) && !/男/.test(text)) {
    out.gender = '女';
  }
  // 年龄：阿拉伯数字或中文数字（2026-08-18 补『四十五岁/二十三岁』等写法）
  const _CN2N = { '一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10 };
  function _cnAge(s) {
    if (!s) return '';
    if (/^\d+$/.test(s)) return String(parseInt(s, 10));
    let n = 0, acc = 0;
    for (const ch of s) {
      if (ch === '十') { n = (n || 1) * 10; }
      else if (_CN2N[ch] !== undefined) { if (n === 0) n = _CN2N[ch]; else acc += n, n = 0; }
      else return '';
    }
    return String(n + acc);
  }
  if ((m = text.match(/(?:年龄|age)[:：]?\s*([\d一二两三四五六七八九十]{1,3})(?=\s*(?:岁|Y|y|$))/i)))
    out.age = _cnAge(m[1]);
  else if ((m = text.match(/(?:年龄|age)[:：]?\s*([\d一二两三四五六七八九十]{1,3})岁/)))
    out.age = _cnAge(m[1]);
  const mod = text.match(/(CT|MRI|MR|DR|CR|DSA|XA|US|超声|核磁共振|计算机断层)/i);
  if (mod) {
    const v = mod[1].toUpperCase();
    const map = { CT:'CT', MRI:'MR', MR:'MR', DR:'DR', CR:'DR', DSA:'XA', XA:'XA', US:'US', '超声':'US', '核磁共振':'MR', '计算机断层':'CT' };
    out.modality = map[v] || v;
  }
  return out;
}

function setVal(id, v) {
  const el = document.getElementById(id);
  // 空串也应赋值：连续处理两份报告时，字段为空的第二份必须清掉第一份残留（2026-08-18）
  if (!el || v === undefined || v === null) return;
  el.value = v;
  el.dispatchEvent(new Event('input'));
}

function ocrFill(map, meta) {
  // 前端兜底：拼接三区文本解析（姓名可能落在非 basic 区，如侧边栏/标题栏被划进 findings）
  const combined = [map.basic, map.findings, map.impression].filter(Boolean).join('\n');
  const p = parsePatientInfo(combined);
  // 后端结构化 meta 更鲁棒（extract_meta_full 已跨区补抽），优先覆盖非空字段。
  // 2026-08-18 修复：applied_site/laterality 此前被丢弃，OCR 识别出的部位/侧别
  // 不会进入质控（runQC 的 applied_site 恒空 → R6 登记部位不符无法基于 OCR 触发）。
  if (meta) {
    if (meta.patient)   p.patient   = meta.patient;
    if (meta.gender)    p.gender    = meta.gender;
    if (meta.age)       p.age       = meta.age;
    if (meta.modality)  p.modality  = meta.modality;
    if (meta.applied_site) p.applied_site = meta.applied_site;
    if (meta.laterality)   p.laterality   = meta.laterality;
  }
  setVal('mPatient', p.patient);
  setVal('mGender', p.gender);
  setVal('mAge', p.age);
  setVal('mModality', p.modality);
  // 2026-08-18：无条件 setVal（空串清残留）——此前仅非空赋值，连续处理时
  // 第二份未识别部位/侧别会残留第一份的值进质控（meta 恒含 applied_site/laterality）
  setVal('mSite', p.applied_site || '');
  setVal('mLaterality', p.laterality || '');
  if (map.findings) setVal('findingsText', map.findings.trim());
  if (map.impression) setVal('impressionText', map.impression.trim());
}

async function ocrPipeline() {
  try {
    if (ocrState.mode === 'screen') {
      if (!ocrState.screen) { toast('请先点「🖥 截取 PACS 画面」', 'error'); return; }
      const regions = {};
      ocrState.boxes.forEach(b => regions[b.key] = {
        x: +b.x.toFixed(4), y: +b.y.toFixed(4), w: +b.w.toFixed(4), h: +b.h.toFixed(4)
      });
      const refresh = document.getElementById('ocrRefresh')?.checked;
      const res = await apiFetch('/api/v1/screen/ocr', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions, refresh, dynamic: !!APP_SETTINGS.ocr_dynamic, dynamic_region: APP_SETTINGS.ocr_dynamic ? unionRegions(regions) : null })
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.message || 'OCR 失败');
      const t = (data.data && data.data.texts) || {};
      ocrFill({ basic: t.basic, findings: t.findings, impression: t.impression },
               (data.data && data.data.meta) || null);
    } else {
      if (!ocrState.img) { toast('请先粘贴或选择报告截图', 'error'); return; }
      const results = await Promise.all(ocrState.boxes.map(async (b) => ({ key: b.key, text: await ocrCropAndRecognize(b) })));
      const map = {}; results.forEach(r => map[r.key] = r.text);
      // 图片模式同样走后端结构化抽取，保证与屏幕模式一致的稳健回填
      let meta = null;
      try {
        const mr = await apiFetch('/api/v1/ocr/meta', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ basic: map.basic || '', findings: map.findings || '', impression: map.impression || '' })
        });
        const md = await mr.json();
        if (md.ok && md.data && md.data.meta) meta = md.data.meta;
      } catch (e) { /* 忽略，走前端兜底 */ }
      ocrFill(map, meta);
    }
    toast('已识别并填充，正在导入并质控...', 'info');
    closeOcrModal();
    await saveToLibrary();  // 导入（其内部先运行质控）
    toast('识别 → 导入 → 质控 完成', 'success');
  } catch (e) { toast('流程出错: ' + e.message, 'error'); }
}

// 计算三区（basic/findings/impression）的外接矩形（比例坐标）。
// 用于动态识别限定 OCR 范围：只识别 PACS 报告区，屏蔽屏幕其他无关文字。
function unionRegions(regions) {
  if (!regions) return null;
  let xs = [], ys = [], xe = [], ye = [];
  for (const key of ['basic', 'findings', 'impression']) {
    const r = regions[key];
    if (!r || !(r.w > 0 && r.h > 0)) continue;
    xs.push(r.x); ys.push(r.y); xe.push(r.x + r.w); ye.push(r.y + r.h);
  }
  if (!xs.length) return null;
  return { x: Math.min(...xs), y: Math.min(...ys),
           w: Math.max(...xe) - Math.min(...xs),
           h: Math.max(...ye) - Math.min(...ys) };
}

// 一键识别（不弹框选界面）：复用已保存框位，后端一次请求即完成 抓屏→OCR→填充→质控→入库
let _ocrOneClickBusy = false;
async function ocrOneClick() {
  if (_ocrOneClickBusy) return;     // 防重入：全局热键与 SPA 快捷键可能同时命中
  _ocrOneClickBusy = true;
  try {
    // 1) 读已保存框位；无则提示先去设置
    let regions = null;
    try {
      const r = await apiFetch('/api/v1/screen/regions').then(x => x.json());
      regions = (r && r.data && r.data.web_regions) || null;
    } catch (e) { regions = null; }
    if (!regions || !(regions.basic || regions.findings || regions.impression)) {
      toast('未记住框位：请先点「📷 框选设置」框选三区并「记住框位」', 'error');
      return;
    }

    toast('📷 正在识别 PACS 报告…');
    // 2) 截屏前让出焦点，避免截到应用自身（仅 WebView 桌面端有效）
    _ocrHideApp();
    await new Promise(res => setTimeout(res, 350));

    // 3) 一次请求完成：refresh=true 后端自动重新抓屏 + 识别。
    //    默认 dynamic=true（标题切分）：滚动不变形，但只在三区外接矩形内 OCR，
    //    避免把 PACS 报告区外的无关文字（工具栏/图像区/其他窗口）也识别进来；
    //    设置页关闭动态时才退回三区逐一精确裁剪。
    const silent = !!APP_SETTINGS.ocr_silent;   // 静默模式：后台质控完不强制弹窗
    const useDynamic = !!APP_SETTINGS.ocr_dynamic;
    const dynamicRegion = useDynamic ? unionRegions(regions) : null;
    let ocr;
    try {
      ocr = await apiFetch('/api/v1/screen/ocr', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions, refresh: true, dynamic: useDynamic,
                               dynamic_region: dynamicRegion })
      }).then(x => x.json());
    } catch (err) {
      if (!silent) _ocrShowApp();
      toast('OCR 失败：' + ((err && err.message) || err), 'error');
      return;
    }

    if (!ocr || !ocr.ok) {
      if (!silent) _ocrShowApp();
      toast('OCR 失败：' + ((ocr && ocr.message) || '未知错误'), 'error');
      return;
    }
    const t = (ocr.data && ocr.data.texts) || {};
    ocrFill({ basic: t.basic, findings: t.findings, impression: t.impression },
             (ocr.data && ocr.data.meta) || null);

    // 4) 质控：runQC 返回 false 表示失败（空文本/引擎错），此时不再入库、直接收尾。
    //    saveToLibrary 内部仅当无质控结果时才补跑，这里已先显式跑过。
    toast('已识别并填充，正在质控…', 'info');
    const qcOk = await runQC();
    if (qcOk) {
      try {
        await saveToLibrary();
        toast('识别 → 质控 → 导入 完成', 'success');
      } catch (err) {
        toast('入库失败：' + ((err && err.message) || err), 'error');
      }
    }
    // 5) 收尾：非静默模式才把窗口弹回来展示结果；静默模式保持后台，仅 toast 提示
    if (!silent) _ocrShowApp();
  } catch (e) {
    // 未捕获异常兜底：窗口恢复显示，避免"窗口永久隐藏"（2026-08-18）
    if (!silent) _ocrShowApp();
    toast('一键 OCR 异常：' + ((e && e.message) || e), 'error');
  } finally {
    _ocrOneClickBusy = false;
  }
}

// 通过 pywebview 原生桥隐藏/显示窗口（截屏前让出焦点）。浏览器环境无桥则无操作。
function _ocrHideApp() {
  try { window.pywebview && window.pywebview.api && window.pywebview.api.hide_app && window.pywebview.api.hide_app(); } catch (e) {}
}
function _ocrShowApp() {
  try { window.pywebview && window.pywebview.api && window.pywebview.api.show_app && window.pywebview.api.show_app(); } catch (e) {}
}

// 供全局热键调用：若 OCR 模态已开（用户在设置框位）则执行模态内流程，否则一键识别
function ocrHotkey() {
  if (document.getElementById('ocrModal').style.display === 'flex') ocrPipeline();
  else ocrOneClick();
}


// ==================== 全局快捷键（可配置，默认 Windows Ctrl+ 风） ====================
const SHORTCUT_ACTIONS = {
  run_qc:       { label: '运行质控',     run: () => runQC() },
  save_sample:  { label: '存入样本库',   run: () => saveToLibrary() },
  paste_split:  { label: '粘贴全文并分栏', run: () => pasteAndSplit() },
  ocr_capture:  { label: '识别并质控',   run: () => {
    if (document.getElementById('ocrModal').style.display === 'flex') ocrPipeline();
    else ocrOneClick();
  } },
  toggle_theme: { label: '明暗主题切换', run: () => toggleTheme() },
};

// 把 {mods:[...], key:"..."} 渲染成展示串，如 "Ctrl+Shift+O"
function fmtShortcut(sc) {
  if (!sc || !sc.key) return '未设置';
  const names = { ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', meta: 'Win' };
  const mods = (sc.mods || []).map(m => names[m] || m).join('+');
  const k = sc.key.length === 1 ? sc.key.toUpperCase() : sc.key;
  return mods ? (mods + '+' + k) : k;
}

// 从 keydown 事件解析出组合键描述
function comboFromEvent(e) {
  const mods = [];
  if (e.ctrlKey)  mods.push('ctrl');
  if (e.shiftKey) mods.push('shift');
  if (e.altKey)   mods.push('alt');
  if (e.metaKey)  mods.push('meta');
  let key = e.key;
  if (key === ' ') key = 'Space';
  else if (key.length === 1) key = key.toLowerCase();   // 字母统一小写
  return { mods, key };
}

// 组合键精确匹配（修饰键集合 + 主键 都要一致）
function comboEquals(sc, evt) {
  if (!sc || !sc.key || !evt) return false;
  const a = (sc.mods || []).slice().sort().join(',');
  const b = evt.mods.slice().sort().join(',');
  if (a !== b) return false;
  const k = sc.key.length === 1 ? sc.key.toLowerCase() : sc.key;
  return k === evt.key;
}

let _capturingShortcut = null;   // 设置页“按下新快捷键”捕获中

document.addEventListener('keydown', function(e) {
  // 设置页正在捕获：记录组合键并写回该动作，不触发业务动作
  if (_capturingShortcut) {
    e.preventDefault();
    if (e.key === 'Escape') { _cancelCapture(); return; }
    const sc = comboFromEvent(e);
    APP_SETTINGS.shortcuts = APP_SETTINGS.shortcuts || {};
    APP_SETTINGS.shortcuts[_capturingShortcut] = sc;
    _renderShortcutRow(_capturingShortcut);
    _endCapture();
    return;
  }
  const evt = comboFromEvent(e);
  const sc = APP_SETTINGS.shortcuts || {};
  for (const action in SHORTCUT_ACTIONS) {
    if (comboEquals(sc[action], evt)) {
      e.preventDefault();
      SHORTCUT_ACTIONS[action].run();
      return;
    }
  }
  // Esc 为固定行为：先关 OCR 模态；工作区焦点在输入框内时不清空（防误触清空报告）
  if (e.key === 'Escape') {
    if (document.getElementById('ocrModal').style.display === 'flex') { closeOcrModal(); return; }
    const t = e.target;
    if (t && t.closest && t.closest('input,textarea,select')) return; // 编辑中 Esc（取消输入法/退出全屏）不清空
    if (document.getElementById('page-qc').classList.contains('active')) clearInput();
  }
});

function _startCapture(action, btn) {
  _capturingShortcut = action;
  document.querySelectorAll('.sc-rebind').forEach(b => b.classList.remove('capturing'));
  btn.classList.add('capturing');
  btn.textContent = '按下新快捷键…';
}
function _endCapture() {
  _capturingShortcut = null;
  document.querySelectorAll('.sc-rebind').forEach(b => {
    b.classList.remove('capturing');
    const a = b.dataset.action;
    b.textContent = '重绑';
  });
}
function _cancelCapture() {
  const btn = document.querySelector('.sc-rebind.capturing');
  _endCapture();
}
function _renderShortcutRow(action) {
  const cell = document.querySelector('.sc-key[data-action="' + action + '"]');
  if (cell) cell.textContent = fmtShortcut((APP_SETTINGS.shortcuts || {})[action]);
}

// ==================== 账号 / 授权（启动闸门） ====================

async function loadDisclaimer() {
  try {
    const r = await apiFetch('/api/v1/license/disclaimer');
    const d = await r.json();
    const t = document.getElementById('disclaimerText');
    if (t && d.data) t.textContent = d.data.text || '';
  } catch (e) {}
}

function fillMachineCode() {
  apiFetch('/api/v1/license/machine-code').then(r => r.json()).then(d => {
    const mid = d.data && d.data.machine_id;
    const g = document.getElementById('gateMid'); if (g && mid) g.textContent = mid;
    const s = document.getElementById('setMachineId'); if (s && mid) s.textContent = mid;
  }).catch(() => {});
}

async function gateAccept() {
  setGateErr('gaErr', '');
  try {
    await apiFetch('/api/v1/license/disclaimer', { method: 'POST' });
    bootstrapGate();
  } catch (e) { setGateErr('gaErr', '网络错误：' + e.message); }
}
function gateReject() {
  const t = document.getElementById('disclaimerText');
  if (t) t.innerHTML = '<p style="color:#c0392b">您未同意用户协议，无法使用本软件。请关闭窗口。</p>';
  const b1 = document.getElementById('btnAccept'); if (b1) b1.style.display = 'none';
  const b2 = document.getElementById('btnReject'); if (b2) { b2.textContent = '关闭'; b2.onclick = () => window.close(); }
}

async function gateCreate() {
  setGateErr('gaErr', '');
  const empId = document.getElementById('gaEmpId').value.trim();
  const name  = document.getElementById('gaName').value.trim();
  const pwd   = document.getElementById('gaPwd').value;
  const pwd2  = document.getElementById('gaPwd2').value;
  if (!empId) return setGateErr('gaErr', '请输入工号');
  if (pwd.length < 6) return setGateErr('gaErr', '密码至少 6 位');
  if (pwd !== pwd2) return setGateErr('gaErr', '两次密码不一致');
  try {
    const res = await apiFetch('/api/v1/accounts', {
      method: 'POST', body: JSON.stringify({ emp_id: empId, name, password: pwd })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '创建失败');
    AUTH.token = d.data.token; AUTH.empId = d.data.emp_id; AUTH.name = d.data.name || '';
    AUTH.role  = d.data.role || 'doctor';
    localStorage.setItem('xy-token', AUTH.token);
    localStorage.setItem('xy-emp', AUTH.empId);
    localStorage.setItem('xy-name', AUTH.name);
    localStorage.setItem('xy-role', AUTH.role);
    applyRoleUI(); enterApp(LICENSE_STATUS);
  } catch (e) { setGateErr('gaErr', e.message); }
}

async function gateLogin() {
  setGateErr('glErr', '');
  const empId = document.getElementById('glEmpId').value.trim();
  const pwd   = document.getElementById('glPwd').value;
  if (!empId || !pwd) return setGateErr('glErr', '请输入工号和密码');
  try {
    const res = await apiFetch('/api/v1/accounts/login', {
      method: 'POST', body: JSON.stringify({ emp_id: empId, password: pwd })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '登录失败');
    AUTH.token = d.data.token; AUTH.empId = d.data.emp_id; AUTH.name = d.data.name || '';
    AUTH.role  = d.data.role || 'doctor';
    localStorage.setItem('xy-token', AUTH.token);
    localStorage.setItem('xy-emp', AUTH.empId);
    localStorage.setItem('xy-name', AUTH.name);
    localStorage.setItem('xy-role', AUTH.role);
    applyRoleUI(); enterApp(LICENSE_STATUS);
  } catch (e) { setGateErr('glErr', e.message); }
}

async function gateActivate() {
  setGateErr('gActErr', '');
  const code = document.getElementById('gActCode').value.trim();
  if (!code) return setGateErr('gActErr', '请输入激活码');
  try {
    const res = await apiFetch('/api/v1/license/activate', {
      method: 'POST', body: JSON.stringify({ code })
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.message || '激活失败');
    LICENSE_STATUS = d.data;
    enterApp(d.data);
  } catch (e) { setGateErr('gActErr', e.message); }
}

function enterApp(status) {
  showGate(false);
  LICENSE_STATUS = status || LICENSE_STATUS;
  loadSettings();
  refreshUserUI();
  updateTrialBanner(LICENSE_STATUS);
}

function updateTrialBanner(status) {
  const b = document.getElementById('trialBanner');
  if (!b) return;
  if (!status || status.activated) { b.style.display = 'none'; return; }
  if (status.trial_state === 'expired') {
    b.className = 'trial-banner expired';
    b.textContent = '⚠ 免费试用期已结束，请到「设置 → 授权与激活」输入激活码以继续使用。';
    b.style.display = 'block';
  } else if (status.trial_state === 'trial') {
    b.className = 'trial-banner';
    b.textContent = '🕒 免费试用期剩余 ' + status.trial_days_left + ' 天（共 ' + status.trial_days_total + ' 天）';
    b.style.display = status.trial_days_left <= 14 ? 'block' : 'none';
  } else {
    b.style.display = 'none';
  }
}

function refreshUserUI() {
  const av = document.getElementById('userAvatar');
  const nm = document.getElementById('userName');
  const mi = document.getElementById('userMenuName');
  const ms = document.getElementById('userMenuSub');
  const mr = document.getElementById('userMenuStatus');
  const label = AUTH.name || AUTH.empId || '未登录';
  if (av) av.textContent = (label[0] || '?').toUpperCase();
  if (nm) nm.textContent = AUTH.empId || '未登录';
  if (mi) mi.textContent = label;
  if (ms) ms.textContent = AUTH.empId ? '工号 ' + AUTH.empId : '';
  if (mr) {
    const st = LICENSE_STATUS;
    if (!st) mr.textContent = '';
    else if (st.activated) mr.textContent = '✅ 已激活';
    else if (st.trial_state === 'trial') mr.textContent = '🕒 试用期 ' + st.trial_days_left + ' 天';
    else mr.textContent = '⚠ 需激活';
  }
  if (AUTH.empId) {
    const u = document.getElementById('mUser'); if (u) u.value = AUTH.empId;
    if (APP_SETTINGS) APP_SETTINGS.emp_id = AUTH.empId;
  }
}

function populateLicenseSettings() {
  fillMachineCode();
  apiFetch('/api/v1/license/status').then(r => r.json()).then(d => {
    LICENSE_STATUS = d.data;
    const st = d.data, el = document.getElementById('setLicenseStatus');
    if (el && st) {
      if (st.activated) el.textContent = '✅ 已激活（永久）';
      else if (st.trial_state === 'trial') el.textContent = '🕒 试用期 · 剩余 ' + st.trial_days_left + ' 天';
      else el.textContent = '⚠ 试用期已结束，需激活';
    }
    refreshUserUI();
    updateTrialBanner(st);
  }).catch(() => {});
}

async function bootstrapGate() {
  loadSettings();   // 修复：进入前先加载设置（此前从未在启动期调用）
  let status = null;
  try {
    const r = await apiFetch('/api/v1/license/status');
    const d = await r.json();
    status = d.data; LICENSE_STATUS = status;
  } catch (e) {
    // 后端不可达（开发态）：直接放行，避免锁死界面
    showGate(false); refreshUserUI(); applyRoleUI();
    maybeShowOnboarding();
    return;
  }
  // 授权门校验优先于登录态快捷路径（2026-08-18 修复）：
  // 此前『已有 token 直接放行』导致试用过期的登录用户永久绕过激活门，只剩顶部横幅，
  // 与免责声明「试用期后需输入有效的激活码方可继续使用」矛盾。
  showGate(true);
  if (!status.disclaimer_accepted) { gateShow('disclaimer'); loadDisclaimer(); return; }
  if (status.trial_state === 'expired' && !status.activated) { gateShow('activation'); fillMachineCode(); return; }
  // 授权状态通过后，已登录用户快捷进入（免重复登录）
  if (AUTH.token && status.account_count > 0) {
    showGate(false); refreshUserUI(); applyRoleUI(); updateTrialBanner(status);
    maybeShowOnboarding();
    return;
  }
  gateShow(status.account_count === 0 ? 'account' : 'login');
}

// ==================== 首次使用引导 Onboarding ====================
const ONBOARDING_KEY = 'xy-onboarding-done';

function maybeShowOnboarding() {
  if (localStorage.getItem(ONBOARDING_KEY) === '1') return;
  // 延迟到主界面渲染完成
  setTimeout(() => {
    const ov = document.getElementById('onboardingOverlay');
    if (ov) { showOnboarding(); }
  }, 350);
}

function showOnboarding() {
  const ov = document.getElementById('onboardingOverlay');
  if (!ov) return;
  _onbStep = 1;
  ov.style.display = 'flex';
  _renderOnboarding();
}

function closeOnboarding() {
  localStorage.setItem(ONBOARDING_KEY, '1');
  const ov = document.getElementById('onboardingOverlay');
  if (ov) ov.style.display = 'none';
}

// 从设置页重新打开引导
function openOnboardingFromSettings() {
  showOnboarding();
}

let _onbStep = 1;

function _renderOnboarding() {
  document.querySelectorAll('.onboarding-step').forEach(el =>
    el.classList.toggle('active', parseInt(el.dataset.step, 10) === _onbStep));
  document.querySelectorAll('.onb-dot').forEach(el =>
    el.classList.toggle('active', parseInt(el.dataset.dot, 10) === _onbStep));
  const next = document.getElementById('onbNext');
  if (next) {
    if (_onbStep === 3) { next.textContent = '完成'; }
    else { next.textContent = '下一步'; }
  }
}

// 示例报告：加载一份含典型问题的演示报告并直接质控
function _loadOnbDemo() {
  const findings =
    '检查所见：胸廓对称，双肺纹理清晰。右肺上叶见一磨玻离影，大小约8mm，边界清。\n' +
    '左肺下叶见一小结结，直径约4mm。纵隔居中，未见肿大淋巴結。心脏大小正常。\n' +
    '双侧胸腔未见积液。';
  const impression = '右肺上叶磨玻璃影，建议随访。左肺下叶小结节，建议定期复查。';
  const fEl = document.getElementById('findingsText');
  const iEl = document.getElementById('impressionText');
  if (fEl) fEl.value = findings;
  if (iEl) iEl.value = impression;
  const fc = document.getElementById('findingsCount');
  const ic = document.getElementById('impressionCount');
  if (fc) fc.textContent = findings.length + ' 字';
  if (ic) ic.textContent = impression.length + ' 字';
  closeOnboarding();
  setTimeout(() => { if (typeof runQC === 'function') runQC(); }, 120);
}

// app.js 在 body 末尾加载，DOM 已就绪，直接绑定（勿用 DOMContentLoaded——此时早已触发）
(function _bindOnboarding() {
  const next = document.getElementById('onbNext');
  const skip = document.getElementById('onbSkip');
  const demo = document.getElementById('onbTryDemo');
  if (next) next.addEventListener('click', function() {
    if (_onbStep >= 3) { closeOnboarding(); }
    else { _onbStep++; _renderOnboarding(); }
  });
  if (skip) skip.addEventListener('click', closeOnboarding);
  if (demo) demo.addEventListener('click', _loadOnbDemo);
})();

function toggleUserMenu(e) {
  e.stopPropagation();
  const m = document.getElementById('userMenu');
  if (m) m.style.display = m.style.display === 'block' ? 'none' : 'block';
}
document.addEventListener('click', () => {
  const m = document.getElementById('userMenu');
  if (m) m.style.display = 'none';
});

function logout() {
  localStorage.removeItem('xy-token');
  localStorage.removeItem('xy-emp');
  localStorage.removeItem('xy-name');
  AUTH.token = ''; AUTH.empId = ''; AUTH.name = '';
  location.reload();
}
function openActivateFromSettings() {
  closeSettings();
  showGate(true); gateShow('activation'); fillMachineCode();
}
function copyMachineId() {
  const el = document.getElementById('setMachineId');
  const txt = el ? el.textContent : '';
  if (navigator.clipboard && txt) {
    navigator.clipboard.writeText(txt).then(
      () => toast('机器码已复制', 'success'),
      () => toast('复制失败，请手动复制', 'error'));
  }
}

// ==================== 初始化 ====================
bootstrapGate();
console.log('星衍AI放射质控 · Web 版 v1.0 已加载');
