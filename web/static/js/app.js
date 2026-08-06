/**
 * 星衍AI放射质控 · Web 版前端逻辑
 * SPA 路由 / API 交互 / 结果渲染
 */

// ==================== SPA 页面切换 ====================
const PAGE_TITLES = {
  qc:        { title: '报告质控',     sub: 'AI 驱动的放射报告质量检测引擎' },
  queue:     { title: '待质控队列',   sub: '排队中的报告，逐份质控并入库后自动出队' },
  dashboard: { title: '质控看板',     sub: '数据统计与质量趋势分析' },
  ris:       { title: 'RIS 直连',     sub: '连接 PACS/RIS 数据库获取报告' },
  samples:   { title: '样本库',       sub: '已质控报告的存储与管理' },
  rules:     { title: '规则维护',     sub: '查看和管理质控规则' },
};

// 全局应用设置（从 /api/v1/settings 载入，影响 OCR/入库/自动化行为）
let APP_SETTINGS = {
  emp_id: 'demo01', default_modality: '', auto_qc_on_ocr: true, auto_enqueue: true,
  ocr_min_score: 0.55, screen_refresh_on_ocr: false, anonymize: false, theme: 'light',
  // 默认 Windows 风 Ctrl+；设置页可逐条重绑（保存后持久化到 web_settings.json）
  shortcuts: {
    run_qc:       { mods: ['ctrl'], key: 'Enter' },
    save_sample:  { mods: ['ctrl'], key: 's' },
    ocr_capture:  { mods: ['ctrl', 'shift'], key: 'o' },
    toggle_theme: { mods: ['ctrl'], key: 't' },
  },
};

// ==================== 账号 / 授权上下文 ====================
// 登录态在 localStorage 持久化：刷新页面不必重复登录
let AUTH = {
  token: localStorage.getItem('xy-token') || '',
  empId: localStorage.getItem('xy-emp') || '',
  name:  localStorage.getItem('xy-name') || '',
  role:  localStorage.getItem('xy-role') || '',
};
let LICENSE_STATUS = null;   // 最近一次授权状态聚合

// 统一 API 封装：自动附加鉴权头（授权相关请求使用；业务请求沿用原 fetch 不受影响）
async function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers || {});
  opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
  if (AUTH.token)    opts.headers['Authorization'] = 'Bearer ' + AUTH.token;
  if (AUTH.empId)    opts.headers['X-Emp-Id'] = AUTH.empId;
  return fetch(url, opts);
}

// 严重度元数据：图标 + 文字（色盲可用）
const SEV_META = {
  high:   { icon: '⛔', label: '严重', cls: 'danger' },
  medium: { icon: '⚠', label: '警告', cls: 'warning' },
  low:    { icon: 'ℹ', label: '提示', cls: 'info' },
};

function switchPage(pageName, navEl) {
  // 切换导航高亮
  document.querySelectorAll('.nav-cell').forEach(el => el.classList.remove('active'));
  if (navEl) navEl.classList.add('active');

  // 切换页面显示
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('page-' + pageName);
  if (target) target.classList.add('active');

  // 更新标题
  const info = PAGE_TITLES[pageName] || {};
  document.getElementById('pageTitle').textContent = info.title || '';
  document.getElementById('pageSubtitle').textContent = info.sub || '';

  // 页面加载时自动拉取数据
  if (pageName === 'dashboard') loadDashboard();
  if (pageName === 'samples') loadSamples();
  if (pageName === 'rules') { loadRules(); loadRulesConfig(true); }
  if (pageName === 'queue') loadQueue();
  if (pageName === 'users') loadUsers();
}

// ==================== 角色 UI 适配 + 用户管理 ====================
function applyRoleUI() {
  const isAdmin = AUTH.role === 'admin';
  document.querySelectorAll('[data-admin-only]').forEach(el => {
    el.style.display = isAdmin ? '' : 'none';
  });
  const sub = document.getElementById('userMenuSub');
  if (sub) sub.textContent = (isAdmin ? '系统管理员' : '医生') + ' · ' + (AUTH.empId || '');
}

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
function toast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ==================== 字数统计 ====================
document.getElementById('findingsText').addEventListener('input', function() {
  document.getElementById('findingsCount').textContent = this.value.length + ' 字';
});
document.getElementById('impressionText').addEventListener('input', function() {
  document.getElementById('impressionCount').textContent = this.value.length + ' 字';
});

// ==================== 报告质控：运行引擎 ====================
// 侧别取值：优先取独立下拉框；为空时从「项目/检查部位」自由文本派生，
// 让后台 R11-SIDE（项目 vs 描述/诊断 左右比对）在常见录入方式下都能启动。
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
  const findings = document.getElementById('findingsText').value.trim();
  const impression = document.getElementById('impressionText').value.trim();

  if (!findings && !impression) {
    toast('请输入影像描述或结论文本', 'error');
    return;
  }

  // 显示加载状态
  document.getElementById('findingEmpty').style.display = 'none';
  document.getElementById('findingList').style.display = 'none';
  document.getElementById('findingListContainer').innerHTML =
    '<div class="loading-spinner"><div class="spinner"></div>正在运行质控引擎...</div>';

  try {
    const report = [findings, impression].filter(Boolean).join('\n');
    const res = await fetch('/api/v1/qc/check', {
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
          user_id:    document.getElementById('mUser').value,
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
    const findings = (data.data.findings || []).map(f => ({ ...f, category: f.error_type }));
    renderQCResult({ findings, scores });

  } catch (err) {
    console.error(err);
    document.getElementById('findingListContainer').innerHTML =
      `<div class="empty-state"><div class="empty-icon">⚠️</div><p>错误：${err.message}</p></div>`;
    toast('质控运行出错: ' + err.message, 'error');
  }
}

function renderQCResult(data) {
  const { findings, scores } = data;

  // 渲染评分
  renderScore('scoreAcc', 'accuracy', scores.accuracy || 0);
  renderScore('scoreComp', 'completeness', scores.completeness || 0);
  renderScore('scoreNorm', 'normalization', scores.normalization || 0);
  renderScore('scoreTime', 'timeliness', scores.timeliness || 0);

  // 渲染发现列表
  const listEl = document.getElementById('findingList');
  const countEl = document.getElementById('findingCount');

  if (!findings || findings.length === 0) {
    document.getElementById('findingListContainer').innerHTML =
      '<div class="empty-state"><div class="empty-icon">✅</div><p>未发现问题，报告质量良好</p></div>';
    countEl.textContent = '0 条';
    return;
  }

  countEl.textContent = findings.length + ' 条';
  listEl.innerHTML = findings.map(f => {
    const m = SEV_META[f.severity] || SEV_META.low;
    return `
    <li class="finding-item">
      <span class="severity-dot ${f.severity}"></span>
      <span class="sev-badge ${m.cls}">${m.icon} ${m.label}</span>
      <div>
        <div class="finding-text">${escapeHtml(f.message)}</div>
        <div class="finding-meta">${f.rule_id} · ${f.category || ''}</div>
      </div>
    </li>`;
  }).join('');

  listEl.style.display = 'block';
}

function renderScore(elId, key, value) {
  const el = document.getElementById(elId);
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

  // 重置发现
  document.getElementById('findingListContainer').innerHTML =
    '<div class="empty-state" id="findingEmpty"><div class="empty-icon">📭</div><p>运行质控后在此显示发现</p></div>';
  document.getElementById('findingCount').textContent = '0 条';
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
    const res = await fetch('/api/v1/samples', {
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
          user_id:      document.getElementById('mUser').value,
        },
        anonymize: !!APP_SETTINGS.anonymize,
        user_id:   document.getElementById('mUser').value || APP_SETTINGS.emp_id,
      })
    });
    const data = await res.json();
    if (data.ok) {
      toast('已存入样本库（ID=' + (data.data.id || '?') + '）', 'success');
      // 从队列载入的报告，入库成功后自动出队（与桌面版 _dequeue_active 一致）
      if (ACTIVE_QUEUE_ID) {
        const qid = ACTIVE_QUEUE_ID; ACTIVE_QUEUE_ID = null;
        try { await fetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' }); } catch (e) {}
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
    const res = await fetch('/api/v1/settings');
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
    shortcuts:            APP_SETTINGS.shortcuts || {},
  };
  try {
    const res = await fetch('/api/v1/settings', {
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
    closeSettings();
    toast('设置已保存并生效', 'success');
  } catch (e) { toast('保存设置失败: ' + e.message, 'error'); }
}

// ==================== 待质控队列 ====================
let QUEUE_ITEMS = [];
let ACTIVE_QUEUE_ID = null;   // 从队列载入工作区的条目，入库后自动出队

async function loadQueue(silent = false) {
  try {
    const res = await fetch('/api/v1/queue');
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
    const res = await fetch('/api/v1/queue', {
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

/** 把整段报告粗分为「影像描述 / 影像诊断」两段 */
function splitReportSections(text) {
  const t = (text || '').trim();
  const m = t.match(/(诊断印象|影像诊断|影像结论|诊断意见|结论|印象|impression)\s*[:：]?\s*/i);
  if (m && m.index > 0) {
    return {
      findings: t.slice(0, m.index).replace(/^(检查所见|影像描述|影像所见|findings)\s*[:：]?\s*/i, '').trim(),
      impression: t.slice(m.index + m[0].length).trim(),
    };
  }
  return { findings: t.replace(/^(检查所见|影像描述|影像所见|findings)\s*[:：]?\s*/i, '').trim(), impression: '' };
}

async function queueRemove(qid) {
  try {
    const res = await fetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' });
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
    const res = await fetch('/api/v1/queue', { method: 'DELETE' });
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
      const res = await fetch('/api/v1/samples', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          report: it.text,
          meta: Object.assign({ patient: it.patient, applied_site: it.site }, it.meta || {}),
          anonymize: !!APP_SETTINGS.anonymize,
          user_id: APP_SETTINGS.emp_id,
        })
      });
      const d = await res.json();
      if (d.ok) { okCount++; await fetch('/api/v1/queue/' + encodeURIComponent(it.id), { method: 'DELETE' }); }
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
    const [statsRes, samplesRes] = await Promise.all([
      fetch('/api/v1/samples/stats/dashboard'),
      fetch('/api/v1/samples?page_size=10')
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
  // 错误类型分布 + 趋势（独立失败不影响主卡片）
  loadErrorTypes();
  loadTrend();
}

// ---------- 错误类型分布（接 /api/v1/stats/error-types） ----------
const ERR_COLORS = ['#2d6cdf', '#1fa971', '#e8941a', '#e5484d', '#7c6cf0', '#0ea5e9', '#db2777', '#65a30d'];

async function loadErrorTypes() {
  const box = document.getElementById('errorTypeChart');
  if (!box) return;
  try {
    const res = await fetch('/api/v1/stats/error-types');
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
    const res = await fetch('/api/v1/stats/trend');
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
    const max = Math.max(...entries.map(e => e[1]), 1);
    const stepX = (W - PL - PR) / Math.max(1, entries.length - 1);
    const yOf = v => PT + (H - PT - PB) * (1 - v / max);
    const pts = entries.map((e, i) => [PL + i * stepX, yOf(e[1])]);
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
      fill="var(--primary)"><title>${entries[i][0]}：${entries[i][1]} 份</title></circle>`).join('');
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
        <span style="font-size:11px;color:var(--text-muted);font-weight:600;">${mod}</span>
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
      <td style="font-size:12px;color:var(--text-muted);">${(r.ts||'--').slice(5,16)}</td>
      <td>${r.patient||'--'}</td>
      <td><span class="tag info">${r.modality||'--'}</span></td>
      <td>${r.applied_site||'--'}</td>
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
        <td>${r.gender||'--'}</td>
        <td>${r.age||'--'}</td>
        <td><span class="tag info">${r.modality||'--'}</span></td>
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
    const res = await fetch('/api/v1/samples/' + sid);
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
          <div><div class="finding-text">${escapeHtml(f.message)}</div>
          <div class="finding-meta">${escapeHtml(f.rule_id || '')} · ${escapeHtml(f.error_type || '')}</div></div>
        </li>`; }).join('')}</ul>`
        : '<div style="font-size:13px;color:var(--text-muted);">无发现，报告质量良好</div>'}
    `;
    document.getElementById('sampleLoadBtn').onclick = () => { loadSampleToWorkspace(s); };
    document.getElementById('sampleDelBtn').onclick = () => { closeSampleModal(); deleteSample(s.id); };
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
    const res = await fetch('/api/v1/samples/' + sid, { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '删除失败');
    toast('样本 #' + sid + ' 已删除', 'success');
    loadSamples();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

async function exportSamples() {
  try {
    const res = await fetch('/api/v1/samples/export', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ fmt: 'csv' })
    });
    const data = await res.json();
    toast(data.ok ? '导出成功: ' + (data.data.path||'') : '导出失败: '+data.message, data.ok?'success':'error');
  } catch(e) { toast('导出请求失败', 'error'); }
}

// ==================== 规则词表维护（R8 错别字 / R9 矛盾对 / 忽略词 / R10 模板） ====================
async function loadRulesConfig(silent = false) {
  try {
    const res = await fetch('/api/v1/qc/rules/config');
    const data = await res.json();
    const cfg = (data.data || {});
    const typos = cfg.typos || {};
    document.getElementById('cfgTypos').value =
      Object.entries(typos).map(([k, v]) => `${k}=${v}`).join('\n');
    const conflicts = cfg.conflicts || [];
    document.getElementById('cfgConflicts').value =
      conflicts.map(c => Array.isArray(c) ? c.join('|') : `${c.a || ''}|${c.b || ''}`).join('\n');
    const ignores = cfg.ignores || [];
    document.getElementById('cfgIgnores').value = ignores.map(String).join('\n');
    const tpl = cfg.template || {};
    document.getElementById('cfgTplFollowup').checked = !!tpl.require_followup;
    if (!silent) toast('规则配置已载入', 'success');
  } catch (e) {
    if (!silent) toast('载入规则配置失败: ' + e.message, 'error');
  }
}

async function saveRulesConfig() {
  const typos = {};
  document.getElementById('cfgTypos').value.split('\n').forEach(line => {
    const i = line.indexOf('=');
    if (i > 0) {
      const k = line.slice(0, i).trim(), v = line.slice(i + 1).trim();
      if (k) typos[k] = v;
    }
  });
  const conflicts = document.getElementById('cfgConflicts').value
    .split('\n').map(s => s.trim()).filter(Boolean).map(s => {
      const p = s.split('|');
      return { a: p[0], b: p[1] || p[0] };
    });
  const ignores = document.getElementById('cfgIgnores').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const tpl = { require_followup: document.getElementById('cfgTplFollowup').checked };
  try {
    const res = await fetch('/api/v1/qc/rules/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ typos, conflicts, ignores, template: tpl })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    toast('规则配置已保存并生效', 'success');
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
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
      const res = await fetch('/api/v1/samples/import/upload', { method: 'POST', body: fd });
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
async function testRisConnection() {
  const statusEl = document.getElementById('connStatus');
  statusEl.className = 'conn-status disconnected';
  statusEl.innerHTML = '<span class="led"></span> 测试中...';

  try {
    const res = await fetch('/api/v1/ris/test-connection', {
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
      })
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
  try {
    const res = await fetch('/api/v1/ris/fetch-reports', {
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
        limit: 50,
      }),
      signal: risController.signal,
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
        <td><span class="tag info">${r.modality||'--'}</span></td>
        <td>${escapeHtml(r.applied_site||'--')}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;" title="${escapeHtml(r.report_text||'')}">${escapeHtml((r.report_text||'').slice(0,80))}</td>
      </tr>
    `).join('');

    toast(`成功拉取 ${items.length} 条报告`, 'success');
  } catch(e) {
    if (e.name === 'AbortError') toast('已取消拉取', 'info');
    else toast('拉取失败: '+e.message, 'error');
  } finally {
    if (prog) prog.classList.remove('show');
    if (cancelBtn) cancelBtn.style.display = 'none';
    risController = null;
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
      const res = await fetch('/api/v1/samples', {
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
    const res = await fetch('/api/v1/qc/rules');
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
        <td><span class="tag ${r.severity==='critical'?'danger':r.severity==='warning'?'warning':'info'}">${r.severity||'info'}</span></td>
        <td><span class="tag ${r.enabled!==false?'success':'warning'}">${r.enabled!==false?'启用':'禁用'}</span></td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ==================== 工具函数 ====================
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ==================== 明暗主题切换 ====================
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('xy-theme', next);
  const t = document.getElementById('themeToggle');
  if (t) t.textContent = next === 'dark' ? '☀️' : '🌙';
}
(function initTheme() {
  const saved = localStorage.getItem('xy-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
    const t = document.getElementById('themeToggle');
    if (t) t.textContent = saved === 'dark' ? '☀️' : '🌙';
  }
})();

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
  fetch('/api/v1/screen/regions').then(r => r.json()).then(d => {
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
    const res = await fetch('/api/v1/screen/capture', { method: 'POST' });
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
    const res = await fetch('/api/v1/screen/regions', {
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

// 粘贴截图（仅在模态打开时处理）
document.getElementById('ocrCanvasWrap').addEventListener('paste', (e) => {
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
  const res = await fetch('/api/v1/ocr/base64', {
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
      const res = await fetch('/api/v1/screen/ocr', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions, refresh })
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
      const mr = await fetch('/api/v1/ocr/meta', {
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
  // 噪声词（字段词/科室词）：无标签兜底时排除，避免把科室/字段误填成姓名
  const NOISE = /(性别|年龄|检查|部位|科室|门诊|住院|床号|影像|诊断|申请|病案|临床|设备|医院|报告|记录|呼吸|心血管|神经|骨科|普外|泌尿|妇科|产科|儿科|急诊|超声|放射|肿瘤|消化|内分泌|免疫|血液|皮肤|眼科|耳鼻喉|口腔|中医|康复|病理|心电|核医学|受理|登记|来源|类型|方法|所见|印象|建议|征象|结论|提示|说明|病床|住院号|门诊号|检查号|影像号)/;
  const COMPOUND = /^(欧阳|司马|诸葛|东方|上官|令狐|皇甫|宇文|慕容|司徒|夏侯|长孙|赫连|万俟|闻人|澹台|尉迟|公孙)/;
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
    const BODYPART = /^(头部|颈部|胸部|腹部|盆腔|腰部|骶部|尾部|颅脑|头颅|鼻窦|眼眶|涎腺|鼻咽|口咽|喉部|甲状腺|上腹部|中腹部|下腹部|肾上腺|肝脏|胆囊|胰腺|脾脏|肾脏|胃肠|膀胱|前列腺|子宫|卵巢|四肢|关节|肩关节|肘关节|腕关节|髋关节|膝关节|踝关节|腰椎|颈椎|胸椎|骶骨|尾骨|股骨|胫骨|腓骨|肱骨|尺骨|桡骨|骨盆|肋骨|锁骨|脑|颈|胸|腹|盆|腰|骶|颅|颌|面|眼|耳|鼻|咽|喉|肺|肝|胆|胰|脾|肾|胃|肠|膀|乳|肩|肘|腕|髋|膝|踝|指|趾|脊|椎|骨|肋|锁|股|胫|腓|肱|桡)/;
    const PERSON = /(患者?|就\s*诊|受\s*检|病\s*员|病\s*人|name|patient|性\s*别|年\s*龄|男|女|\d)/i;
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
  if ((m = text.match(/(?:年龄|age)[:：]?\s*(\d{1,3})/i))) out.age = m[1];
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
  if (!el || !v) return;
  el.value = v;
  el.dispatchEvent(new Event('input'));
}

function ocrFill(map, meta) {
  // 前端兜底：拼接三区文本解析（姓名可能落在非 basic 区，如侧边栏/标题栏被划进 findings）
  const combined = [map.basic, map.findings, map.impression].filter(Boolean).join('\n');
  const p = parsePatientInfo(combined);
  // 后端结构化 meta 更鲁棒（extract_meta_full 已跨区补抽），优先覆盖非空字段
  if (meta) {
    if (meta.patient)   p.patient   = meta.patient;
    if (meta.gender)    p.gender    = meta.gender;
    if (meta.age)       p.age       = meta.age;
    if (meta.modality)  p.modality  = meta.modality;
  }
  setVal('mPatient', p.patient);
  setVal('mGender', p.gender);
  setVal('mAge', p.age);
  setVal('mModality', p.modality);
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
      const res = await fetch('/api/v1/screen/ocr', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ regions, refresh })
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
        const mr = await fetch('/api/v1/ocr/meta', {
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

// 供全局热键调用：若 OCR 模态已开则执行流程，否则打开模态
function ocrHotkey() {
  if (document.getElementById('ocrModal').style.display === 'flex') ocrPipeline();
  else openOcrModal();
}

// UIA 采集（Windows UI Automation）：读取前景 PACS 窗口报告全文并填充到对应区域
async function ocrUiaCapture() {
  const status = document.getElementById('ocrStatus');
  if (status) status.textContent = '正在通过 UIA 读取前景窗口报告…';
  try {
    const res = await fetch('/api/v1/uia/capture', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || 'UIA 采集失败');
    const d = data.data || {};
    if (d.findings) setVal('findingsText', (d.findings || '').trim());
    if (d.impression) setVal('impressionText', (d.impression || '').trim());
    const m = d.meta || {};
    if (m.patient) setVal('mPatient', m.patient);
    if (m.gender) setVal('mGender', m.gender);
    if (m.age) setVal('mAge', m.age);
    if (m.modality) setVal('mModality', m.modality);
    toast('UIA 已读取前景窗口报告', 'success');
  } catch (e) {
    toast('UIA 采集出错: ' + e.message, 'error');
  } finally {
    if (status) status.textContent = '';
  }
}

// ==================== 全局快捷键（可配置，默认 Windows Ctrl+ 风） ====================
const SHORTCUT_ACTIONS = {
  run_qc:       { label: '运行质控',     run: () => runQC() },
  save_sample:  { label: '存入样本库',   run: () => saveToLibrary() },
  ocr_capture:  { label: '识别并质控',   run: () => {
    if (document.getElementById('ocrModal').style.display === 'flex') ocrPipeline();
    else openOcrModal();
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
  // Esc 为固定行为：先关 OCR 模态，否则在工作区清空录入
  if (e.key === 'Escape') {
    if (document.getElementById('ocrModal').style.display === 'flex') closeOcrModal();
    else if (document.getElementById('page-qc').classList.contains('active')) clearInput();
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
function showGate(v) {
  const g = document.getElementById('gate');
  if (g) g.style.display = v ? 'flex' : 'none';
}
function gateShow(step) {
  document.querySelectorAll('.gate-step').forEach(el => el.style.display = 'none');
  const el = document.getElementById('gate-' + step);
  if (el) el.style.display = 'block';
}
function setGateErr(id, msg) { const e = document.getElementById(id); if (e) e.textContent = msg || ''; }

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
    return;
  }
  // 已有登录态：跳过登录步骤直接进入
  if (AUTH.token && status.account_count > 0) {
    showGate(false); refreshUserUI(); applyRoleUI(); updateTrialBanner(status);
    return;
  }
  showGate(true);
  if (!status.disclaimer_accepted) { gateShow('disclaimer'); loadDisclaimer(); return; }
  if (status.trial_state === 'expired' && !status.activated) { gateShow('activation'); fillMachineCode(); return; }
  gateShow(status.account_count === 0 ? 'account' : 'login');
}

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
