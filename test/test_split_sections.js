// splitReportSections 分栏逻辑测试（node test/test_split_sections.js）
// 直接从 app.js 提取函数定义来测，避免依赖浏览器 DOM。
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'app.js'), 'utf8');

// 提取 splitReportSections 函数体
const fnStart = src.indexOf('function splitReportSections');
const fnEnd = src.indexOf('\n}\n', fnStart);
if (fnStart < 0 || fnEnd < 0) { console.error('未找到 splitReportSections'); process.exit(1); }
const fnBody = src.slice(fnStart, fnEnd + 3);
// eslint-disable-next-line no-eval
const splitReportSections = eval('(' + fnBody + ')');

let pass = 0, fail = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; console.log('  ✅', name); }
  else { fail++; console.log('  ❌', name, '\n      got :', JSON.stringify(got), '\n      want:', JSON.stringify(want)); }
}

console.log('=== splitReportSections 分栏测试 ===');

// 1) 标准：影像描述 → 影像诊断
check('标准描述+诊断', splitReportSections(
  '影像描述：胸廓对称，右肺上叶见结节。\n影像诊断：右肺上叶结节，建议复查。'
), {
  findings: '胸廓对称，右肺上叶见结节。',
  impression: '右肺上叶结节，建议复查。'
});

// 2) 检查所见 → 诊断印象
check('检查所见+诊断印象', splitReportSections(
  '检查所见：双肺纹理清晰。\n诊断印象：未见明显异常。'
), {
  findings: '双肺纹理清晰。',
  impression: '未见明显异常。'
});

// 3) 带序号：1.影像描述 2.影像诊断
check('带序号', splitReportSections(
  '影像描述：肝内未见占位。\n2.影像诊断：肝囊肿。'
), {
  findings: '肝内未见占位。',
  impression: '肝囊肿。'
});

// 4) 结论段多行
check('诊断多行', splitReportSections(
  '检查所见：左肺下叶见斑片影。\n影像结论：\n1.左肺下叶炎症可能\n2.建议治疗后复查。'
), {
  findings: '左肺下叶见斑片影。',
  impression: '1.左肺下叶炎症可能\n2.建议治疗后复查。'
});

// 5) 无诊断标题 → 整段视为描述
check('无诊断标题', splitReportSections('胸廓对称，双肺纹理清晰。'), {
  findings: '胸廓对称，双肺纹理清晰。',
  impression: ''
});

// 6) 空文本
check('空文本', splitReportSections(''), { findings: '', impression: '' });

// 7) 只含描述标题无诊断
check('仅描述标题', splitReportSections('影像描述：右肺上叶见磨玻璃影。'), {
  findings: '右肺上叶见磨玻璃影。',
  impression: ''
});

// 8) 结论标题带括号序号
check('带括号序号', splitReportSections(
  '影像描述：双肺可见条索影。\n(2) 影像诊断：双肺陈旧性病变。'
), {
  findings: '双肺可见条索影。',
  impression: '双肺陈旧性病变。'
});

// 9) 描述段内含"结论"字样不应误切
check('描述内结论不误切', splitReportSections(
  '影像描述：结论性意见以报告为准，右肺见小结节。\n诊断意见：随访。'
), {
  findings: '结论性意见以报告为准，右肺见小结节。',
  impression: '随访。'
});

console.log('\n结果:', pass, '通过,', fail, '失败');
process.exit(fail ? 1 : 0);
