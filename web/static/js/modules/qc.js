import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal, fmtShortcut, comboFromEvent, comboEquals } from "./core.js";
import { SEV_META } from "./shell.js";

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

let _qcRunning = false;        // 运行中标志：防止连击/快捷键重复提交
function _setRunQcBusy(on) {
  const btn = document.getElementById('btnRunQc');
  if (!btn) return;
  btn.disabled = on;
  const sc = (APP_SETTINGS && APP_SETTINGS.shortcuts && APP_SETTINGS.shortcuts.run_qc) || { key: 'Enter', mods: ['ctrl'] };
  const label = document.getElementById('btnRunQcLabel');
  if (label) label.textContent = on ? '⏳ 质控运行中…' : ('运行质控 ' + fmtShortcut(sc));
  btn.classList.toggle('btn-busy', on);
}
async function runQC() {
  if (_qcRunning) { toast('质控正在运行，请稍候', 'warn'); return false; }
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
  _qcRunning = true;
  _setRunQcBusy(true);

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
  } finally {
    _qcRunning = false;
    _setRunQcBusy(false);
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
  if (btnL) {
    btnL.classList.toggle('active', tab === 'list');
    btnL.setAttribute('aria-selected', tab === 'list');
  }
  if (btnA) {
    btnA.classList.toggle('active', tab === 'anno');
    btnA.setAttribute('aria-selected', tab === 'anno');
  }
  if (listBox) listBox.setAttribute('aria-hidden', tab !== 'list');
  if (empty) empty.setAttribute('aria-hidden', tab !== 'list');
  if (anno) anno.setAttribute('aria-hidden', tab !== 'anno');
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
  document.querySelectorAll('.sev-filter').forEach(b => {
    const on = b.dataset.sev === sev;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on);
  });
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
      if (window.ACTIVE_QUEUE_ID) {
        const qid = window.ACTIVE_QUEUE_ID; window.ACTIVE_QUEUE_ID = null;
        try { await apiFetch('/api/v1/queue/' + encodeURIComponent(qid), { method: 'DELETE' }); } catch (e) {}
        loadQueue(true);
      }
    }
    else toast('入库失败: ' + (data.message || ''), 'error');
  } catch (e) {
    toast('入库请求失败: ' + e.message, 'error');
  }
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

export { effectiveLaterality, splitReportSections, runQC, saveToLibrary, _qcAllFindings };

Object.assign(window, { runQC, clearInput, pasteAndSplit, saveToLibrary, showQcTab, setSevFilter, toggleClipWatch, syncClipWatchUI, onClipboardCopy });
