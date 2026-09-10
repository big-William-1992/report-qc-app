import { toast, apiFetch, confirmAction, escapeHtml, APP_SETTINGS, AUTH, animateNumber, setVal } from "./core.js";
import { SEV_META, gotoPage } from "./shell.js";
import { effectiveLaterality, splitReportSections, _qcAllFindings } from "./qc.js";
import { currentQcMeta } from "./rules.js";

// ==================== 待质控队列 ====================
let QUEUE_ITEMS = [];
window.ACTIVE_QUEUE_ID = null;   // 从队列载入工作区的条目，入库后自动出队

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
      '<p>队列为空。通过「数据接入 → 全部加入队列」「框选 OCR 采集」或质控页「📥 加入队列」拉入报告。</p></div>';
    return;
  }
  box.innerHTML = QUEUE_ITEMS.map(it => `
    <div class="queue-row">
      <div class="queue-main">
        <div class="queue-title">${escapeHtml(it.patient || '未知患者')} · ${escapeHtml(it.site || '—')}</div>
        <div class="queue-sub">来源：${escapeHtml(it.source || '—')} · ${escapeHtml(it.ts || '')}</div>
        ${it.findings_desc || it.diagnosis
          ? `<div class="queue-preview"><span class="preview-tag">描述</span>${escapeHtml((it.findings_desc || '').slice(0, 90))}${it.diagnosis ? `<span class="preview-tag">诊断</span>${escapeHtml(it.diagnosis.slice(0, 90))}` : ''}</div>`
          : `<div class="queue-preview">${escapeHtml((it.text || '').replace(/\s+/g, ' ').slice(0, 120))}</div>`}
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
    window.ACTIVE_QUEUE_ID = data.data.id;
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
  // 优先使用推送时携带的结构化「影像描述 / 影像诊断」；
  // 旧条目（无结构化字段）按「描述 / 结论」两段自动还原
  const parts = (it.findings_desc || it.diagnosis)
    ? { findings: it.findings_desc || '', impression: it.diagnosis || '' }
    : splitReportSections(it.text || '');
  document.getElementById('findingsText').value = parts.findings;
  document.getElementById('impressionText').value = parts.impression;
  document.getElementById('findingsCount').textContent = parts.findings.length + ' 字';
  document.getElementById('impressionCount').textContent = parts.impression.length + ' 字';
  window.ACTIVE_QUEUE_ID = qid;
  gotoPage('qc');
  await runQC();
  toast('已载入工作区，入库后自动出队', 'success');
}

async function queueRemove(qid) {
  try {
    const res = await apiFetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '移除失败');
    if (window.ACTIVE_QUEUE_ID === qid) window.ACTIVE_QUEUE_ID = null;
    await loadQueue(true);
  } catch (e) { toast('移除失败: ' + e.message, 'error'); }
}

async function queueClear() {
  if (!QUEUE_ITEMS.length) { toast('队列已是空的', 'info'); return; }
  if (!(await confirmAction('清空队列', `确认清空队列中的 ${QUEUE_ITEMS.length} 份报告？此操作不可撤销。`, true, '清空'))) return;
  try {
    const res = await apiFetch('/api/v1/queue', { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.message || '清空失败');
    window.ACTIVE_QUEUE_ID = null;
    await loadQueue(true);
    toast('队列已清空', 'success');
  } catch (e) { toast('清空失败: ' + e.message, 'error'); }
}

/** 队列批量质控入库：逐条送引擎并入库，成功后出队 */
async function queueRunAll() {
  if (!QUEUE_ITEMS.length) { toast('队列为空', 'info'); return; }
  if (!(await confirmAction('批量质控入库', `将对队列中 ${QUEUE_ITEMS.length} 份报告批量质控并入库，继续？`, false, '开始'))) return;
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
      if (d.ok) { okCount++; await apiFetch('/api/v1/queue/' + encodeURIComponent(it.id), { method: 'DELETE' }); }
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
        const explainItems = Array.isArray(f.explain) ? f.explain.map(x => String(x || '')).filter(Boolean) : [];
        const explainText = explainItems.join('\n');
        const explainLine = explainItems.length ? `<div class="finding-explain" title="${escapeHtml(explainText)}">
          <div class="finding-explain-preview">🔍 ${escapeHtml(explainItems[0])}</div>
          ${explainItems.length > 1 ? `<details class="finding-explain-more">
            <summary>+${explainItems.length - 1} 条依据</summary>
            <div class="finding-explain-detail">${escapeHtml(explainItems.slice(1).join('\n'))}</div>
          </details>` : ''}
        </div>` : '';
        return `<li class="finding-item">
          <span class="severity-dot ${f.severity}"></span>
          <span class="sev-badge ${m.cls}">${m.icon} ${m.label}</span>
          <div><div class="finding-text ${m.cls}">${escapeHtml(f.message)}</div>
          <div class="finding-meta">${escapeHtml(f.rule_id || '')} · ${escapeHtml(f.error_type || '')}</div>${explainLine}</div>
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
  window.ACTIVE_QUEUE_ID = null;   // 来自样本库，非队列条目
  closeSampleModal();
  gotoPage('qc');
  runQC();
}

async function deleteSample(sid) {
  if (!(await confirmAction('删除样本', `确认删除样本 #${sid}？删除后不可恢复。`, true, '删除'))) return;
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

/** 让浏览器/WebView 下载服务端生成的导出文件（GET /files/download） */
function downloadExportedFile(path) {
  const name = (path || '').split(/[\\/]/).pop();
  if (!name) return;
  const a = document.createElement('a');
  a.href = '/api/v1/files/download?file=' + encodeURIComponent(name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 500);
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

export { loadQueue, enqueueCurrent, enqueueText };

Object.assign(window, { loadQueue, queueRunAll, queueClear, queueLoad, queueRemove, enqueueCurrent, loadSamples, loadStatsReport, viewSample, closeSampleModal, deleteSample, exportSamples, importSamples, closeExportFmtModal, pickExportFmt, exportQcReport, loadDashboard, exportSampleReport, downloadExportedFile, loadErrorTypes, loadTrend, loadSampleToWorkspace, renderStatsReport, renderModalityChart, renderRecentTable });
