/**
 * 星衍AI放射质控 · Web 版前端逻辑
 * SPA 路由 / API 交互 / 结果渲染
 */

// ==================== SPA 页面切换 ====================
const PAGE_TITLES = {
  qc:        { title: '报告质控',   sub: 'AI 驱动的放射报告质量检测引擎' },
  dashboard: { title: '质控看板',   sub: '数据统计与质量趋势分析' },
  ris:       { title: 'RIS 直连',   sub: '连接 PACS/RIS 数据库获取报告' },
  samples:   { title: '样本库',     sub: '已质控报告的存储与管理' },
  rules:     { title: '规则维护',   sub: '查看和管理质控规则' },
};

function switchPage(pageName, navEl) {
  // 切换导航高亮
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
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
  if (pageName === 'rules') loadRules();
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
    const res = await fetch('/api/qc/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        findings,
        impression,
        meta: {
          patient:    document.getElementById('mPatient').value,
          gender:     document.getElementById('mGender').value,
          age:        document.getElementById('mAge').value,
          modality:   document.getElementById('mModality').value,
          applied_site: document.getElementById('mSite').value,
          laterality: document.getElementById('mLaterality').value,
          user_id:    document.getElementById('mUser').value,
        }
      })
    });

    const data = await res.json();

    if (!data.success) {
      throw new Error(data.error || '引擎执行失败');
    }

    renderQCResult(data.data);

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
  listEl.innerHTML = findings.map(f => `
    <li class="finding-item">
      <span class="severity-dot ${f.severity}"></span>
      <div>
        <div class="finding-text">${escapeHtml(f.message)}</div>
        <div class="finding-meta">${f.rule_id} · ${f.category || ''}</div>
      </div>
    </li>
  `).join('');

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

  toast('入库功能需要先运行质控（开发中）', 'info');
}

// ==================== 高级设置折叠 ====================
let settingsOpen = false;
function toggleSettings() {
  settingsOpen = !settingsOpen;
  toast(settingsOpen ? '高级设置已展开' : '高级设置已收起', 'info');
}

// ==================== 看板页：加载数据 ====================
async function loadDashboard() {
  try {
    const [statsRes, samplesRes] = await Promise.all([
      fetch('/api/samples/stats/dashboard'),
      fetch('/api/samples/list?per_page=10')
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
    const colors = { CT:'#2d8cf0', DR:'#19be6b', MR:'#ff9900', XA:'#ed4014', US:'#7c3aed' };
    return `
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;">
        <span style="font-size:13px;font-weight:700;color:var(--text-primary);">${count}</span>
        <div style="width:36px;height:${h}px;border-radius:8px;background:${colors[mod]||var(--primary)};transition:height 0.5s;"></div>
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
    const res = await fetch('/api/samples/list?per_page=50');
    const data = await res.json();
    const items = (data.data || {}).items || [];
    const tbody = document.getElementById('samplesBody');

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:32px;">暂无样本数据</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(r => `
      <tr>
        <td style="color:var(--text-muted);font-size:12px;">${r.id}</td>
        <td style="font-size:12px;">${r.ts||'--'}</td>
        <td>${r.patient||'--'}</td>
        <td>${r.gender||'--'}</td>
        <td>${r.age||'--'}</td>
        <td><span class="tag info">${r.modality||'--'}</span></td>
        <td>${r.applied_site||'--'}</td>
        <td>${r.findings_count||0}</td>
        <td><span class="tag ${(r.scores?.accuracy||0)>=90?'success':'warning'}">${(r.scores?.accuracy||0).toFixed(0)}</span></td>
        <td><span class="tag ${(r.scores?.completeness||0)>=90?'success':'warning'}">${(r.scores?.completeness||0).toFixed(0)}</span></td>
      </tr>
    `).join('');
  } catch (e) { console.error(e); }
}

async function exportSamples() {
  try {
    const res = await fetch('/api/samples/export', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ fmt: 'csv' })
    });
    const data = await res.json();
    toast(data.success ? '导出成功: ' + (data.data.path||'') : '导出失败: '+data.error, data.success?'success':'error');
  } catch(e) { toast('导出请求失败', 'error'); }
}

async function importSamples() {
  toast('导入功能：请选择文件（开发中）', 'info');
}

// ==================== RIS 直连 ====================
async function testRisConnection() {
  const statusEl = document.getElementById('connStatus');
  statusEl.className = 'conn-status disconnected';
  statusEl.innerHTML = '<span class="led"></span> 测试中...';

  try {
    const res = await fetch('/api/ris/test-connection', {
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
    if (data.data && data.data.ok) {
      statusEl.className = 'conn-status connected';
      statusEl.innerHTML = '<span class="led"></span> 已连接';
      toast('连接测试成功！', 'success');
    } else {
      statusEl.className = 'conn-status error';
      statusEl.innerHTML = '<span class="led"></span> 连接失败';
      toast('连接失败: ' + (data.data?.message||data.error), 'error');
    }
  } catch(e) {
    statusEl.className = 'conn-status error';
    statusEl.innerHTML = '<span class="led"></span> 请求异常';
    toast('连接测试异常: ' + e.message, 'error');
  }
}

async function fetchRisReports() {
  try {
    const res = await fetch('/api/ris/fetch-reports', {
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
      })
    });
    const data = await res.json();
    const items = (data.data||{}).items||[];
    document.getElementById('risResultCount').textContent = items.length + ' 条';

    const tbody = document.getElementById('risBody');
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:32px;">无数据，请检查 SQL 或连接配置</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(r => `
      <tr>
        <td>${r.patient||'--'}</td>
        <td style="font-size:12px;">${r.gender||'--'}/${r.age||'--'}</td>
        <td><span class="tag info">${r.modality||'--'}</span></td>
        <td>${r.applied_site||'--'}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;" title="${escapeHtml(r.report_text||'')}">${escapeHtml((r.report_text||'').slice(0,80))}</td>
      </tr>
    `).join('');

    toast(`成功拉取 ${items.length} 条报告`, 'success');
  } catch(e) { toast('拉取失败: '+e.message, 'error'); }
}

function sendToQC() { toast('发送到质控（选中行→填入左侧输入框）', 'info'); }
function batchQC() { toast('批量质控入库（全部拉取结果→引擎→样本库）', 'info'); }

// ==================== 规则维护 ====================
async function loadRules() {
  try {
    const res = await fetch('/api/qc/rules');
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

// ==================== 初始化 ====================
console.log('星衍AI放射质控 · Web 版 v1.0 已加载');
