import { toast, apiFetch, escapeHtml, APP_SETTINGS, AUTH, setVal } from "./core.js";
import { splitReportSections } from "./qc.js";

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

// 粘贴截图（仅在模态打开时处理；监听挂在 window，避免焦点不在画布时 Ctrl+V 无效）
window.addEventListener('paste', (e) => {
  if (document.getElementById(OCR_MODAL).style.display !== 'flex') return;
  // 粘贴发生在输入框时保留默认行为（例如往报告文本域粘贴文字）
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
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
  // 噪声词（字段词/科室词）：无标签兜底时排除，避免把科室/字段误填成姓名
  const NOISE = /(性别|年龄|检查|部位|科室|门诊|住院|床号|影像|诊断|申请|病案|临床|设备|医院|报告|记录|呼吸|心血管|神经|骨科|普外|泌尿|妇科|产科|儿科|急诊|超声|放射|肿瘤|消化|内分泌|免疫|血液|皮肤|眼科|耳鼻喉|口腔|中医|康复|病理|心电|核医学|受理|登记|来源|类型|方法|所见|印象|建议|征象|结论|提示|说明|病床|住院号|门诊号|检查号|影像号)/;
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
  if ((m = text.match(/(?:年龄|age)[:：]?\s*(\d{1,3})/i))) out.age = m[1];
  const mod = text.match(/(CT|MRI|MR|DR|CR|DSA|XA|US|超声|核磁共振|计算机断层)/i);
  if (mod) {
    const v = mod[1].toUpperCase();
    const map = { CT:'CT', MRI:'MR', MR:'MR', DR:'DR', CR:'DR', DSA:'XA', XA:'XA', US:'US', '超声':'US', '核磁共振':'MR', '计算机断层':'CT' };
    out.modality = map[v] || v;
  }
  return out;
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
      const res = await apiFetch('/api/v1/screen/ocr', {
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

export { ocrOneClick, ocrPipeline };

Object.assign(window, { openOcrModal, closeOcrModal, ocrLoadFile, ocrGrabScreen, ocrSaveRegions, ocrRecognize, ocrResetBoxes, ocrPipeline, ocrOneClick, ocrHotkey });
