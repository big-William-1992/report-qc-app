import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal } from "./core.js";
import { gotoPage } from "./shell.js";
import { splitReportSections, runQC } from "./qc.js";
import { enqueueCurrent } from "./data.js";

// ==================== 数据接入 ====================
// 数据接入页加载：显示推送配置信息（轮询已移除，改用 PACS 推送模式）
async function loadRisPage() {
  // 推送端点在服务端直接可用，无需前端额外加载
  const pushStatus = document.getElementById('pushStatus');
  if (pushStatus) {
    pushStatus.className = 'conn-status connected';
    pushStatus.innerHTML = '<span class="led"></span> 就绪';
  }
}

async function testRisConnection() {
  const statusEl = document.getElementById('connStatus');
  statusEl.className = 'conn-status disconnected';
  statusEl.innerHTML = '<span class="led"></span> 测试中...';

  try {
    const res = await apiFetch('/api/v1/ris/test-connection', {
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
  window.ACTIVE_QUEUE_ID = null;
  gotoPage('qc');
  await runQC();
  if (APP_SETTINGS.auto_enqueue) await enqueueCurrent('RIS', true);
  toast(`已将选中报告载入工作区${sel.length > 1 ? `（另有 ${sel.length - 1} 份未处理，可点批量质控）` : ''}`, 'success');
}

/** 全部拉取结果 → 引擎 → 样本库（批量质控入库） */
async function batchQC() {
  if (!risItems.length) { toast('没有可质控的报告，请先拉取', 'error'); return; }
  if (!(await confirmAction('批量质控入库', `将把 ${risItems.length} 份报告逐份质控并入库，继续？`, false, '开始'))) return;
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

export { loadRisPage };

Object.assign(window, { testRisConnection, fetchRisReports, cancelRis, risEnqueueAll, sendToQC, batchQC, loadRisPage });
