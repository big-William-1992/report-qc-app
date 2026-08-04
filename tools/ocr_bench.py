#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星衍质控 · OCR 识别准确率基准工具
=================================

用途
----
1) **合成基准**（默认）：用系统中文字体渲染一份标准放射报告，按多种字号跑 RapidOCR，
   给出字符级 recall（识别命中字符数 / 原文字符数，忽略空白），用于回归验证
   「换模型 / 换预处理 / 换 onnxruntime 版本」是否导致识别率下滑。
2) **真实截图评测**：`--image 截图.png --gt 标准答案.txt`，用真实 PACS 截图算准确率。
3) **快速查看**：`--image 截图.png --dump`，只打印 OCR 结果，不算分（无标准答案时用）。

为什么合成基准分数偏高
----------------------
合成图是「纯白底 + 无压缩 + 无缩放」的理想印刷体，分数天然接近 100%。真实 PACS 截图
存在小字号、抗锯齿、深色主题、JPEG 压缩、中英混排等因素，实际准确率通常低于合成值。
因此 `--degrade` 会额外跑一组「降质」样本（下采样 + 高斯噪声 + JPEG 压缩），
更接近真实场景，可作为下限参考。

用法
----
    # 用启动器同款解释器运行（保证与 App 运行时环境一致）
    /Users/xiejun/.workbuddy/binaries/python/envs/default/bin/python3 tools/ocr_bench.py
    python3 tools/ocr_bench.py --degrade
    python3 tools/ocr_bench.py --image /tmp/pacs.png --gt /tmp/pacs.txt
    python3 tools/ocr_bench.py --image /tmp/pacs.png --dump

    # A/B 对比换模型是否划算（如评估 PP-OCRv5 相比内置 PP-OCRv3 的增益）
    python3 tools/ocr_bench.py --degrade                       # 基线（内置 v3）
    python3 tools/ocr_bench.py --degrade \
        --det ~/ocrv5/det.onnx --rec ~/ocrv5/rec.onnx          # 候选（v5）
    # 注意：det 与 rec 必须同代，v5 det 不能配 v3 rec。
"""
from __future__ import annotations

import argparse
import difflib
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# ---- 标准答案：一份典型胸部 CT 报告（中文 + 数字 + 英文缩写混排）----
GT_LINES = [
    "姓名：张三   性别：男   年龄：45岁   检查号：CT20260804-013",
    "检查部位：胸部   检查项目：胸部CT平扫",
    "检查所见：",
    "双肺纹理清晰，走行自然，未见明显增粗、紊乱。",
    "双肺野未见实质性病灶，气管及支气管通畅。",
    "纵隔居中，纵隔淋巴结未见肿大。",
    "心影大小、形态未见异常。双侧胸腔未见积液。",
    "诊断印象：",
    "1. 胸部CT平扫未见明显异常。",
    "2. 必要时建议复查。",
]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def load_font(size: int):
    from PIL import ImageFont
    for fp in FONT_CANDIDATES:
        if not Path(fp).exists():
            continue
        for idx in (0, 1):
            try:
                return ImageFont.truetype(fp, size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


def render(lines, size: int, pad: int = 20):
    """把文本渲染成一张白底黑字图（模拟报告截图）。"""
    from PIL import Image, ImageDraw
    lh = int(size * 1.6)
    font = load_font(size)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    width = max(probe.textbbox((0, 0), ln, font=font)[2] for ln in lines)
    img = Image.new("RGB", (width + pad * 2, len(lines) * lh + pad * 2), "white")
    draw = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        draw.text((pad, pad + i * lh), ln, fill="black", font=font)
    return img


def degrade(img, scale: float = 0.75, noise: int = 6, jpeg_q: int = 60):
    """模拟真实 PACS 截图的降质：下采样 → 高斯噪声 → JPEG 压缩。"""
    from PIL import Image
    import numpy as np
    w, h = img.size
    small = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
    arr = np.asarray(small).astype("int16")
    arr += np.random.normal(0, noise, arr.shape).astype("int16")
    small = Image.fromarray(arr.clip(0, 255).astype("uint8"))
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=jpeg_q)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def score(gt_text: str, rec_text: str):
    """字符级 recall / precision（忽略所有空白字符）。"""
    gt = "".join(c for c in gt_text if not c.isspace())
    rec = "".join(c for c in rec_text if not c.isspace())
    if not gt:
        return 1.0, 1.0, 0, 0
    sm = difflib.SequenceMatcher(None, gt, rec)
    matched = sum(b.size for b in sm.get_matching_blocks())
    recall = matched / len(gt)
    precision = matched / len(rec) if rec else 0.0
    return recall, precision, len(gt), len(rec)


def show_diff(gt_text: str, rec_text: str, limit: int = 10):
    """
    字符级差异明细。

    注意：不做「逐行对照」——OCR 会把一行里用空格分隔的多列（如「姓名 性别 年龄」）
    拆成多个文本框各占一行，行序天然不同，逐行比对会产生大量假差异。
    这里把两侧都压成「去空白的字符流」再比对，只报告真正的漏识 / 错识 / 多识。
    """
    gt = "".join(c for c in gt_text if not c.isspace())
    rec = "".join(c for c in rec_text if not c.isspace())
    ops = [op for op in difflib.SequenceMatcher(None, gt, rec).get_opcodes()
           if op[0] != "equal"]
    if not ops:
        print("    ✅ 字符级完全一致")
        return
    for tag, i1, i2, j1, j2 in ops[:limit]:
        ctx = gt[max(0, i1 - 6):i2 + 6]
        if tag == "replace":
            print(f"    ✗ 错识「{gt[i1:i2]}」→「{rec[j1:j2]}」   上下文：…{ctx}…")
        elif tag == "delete":
            print(f"    ✗ 漏识「{gt[i1:i2]}」                上下文：…{ctx}…")
        else:  # insert
            print(f"    ✗ 多识「{rec[j1:j2]}」                上下文：…{ctx}…")
    if len(ops) > limit:
        print(f"    …（另有 {len(ops) - limit} 处差异省略）")


class AltEngine:
    """
    用指定的一组 .onnx 模型构造独立引擎，用于 A/B 对比（如 PP-OCRv3 vs PP-OCRv5）。

    复用 ocr_provider 的预处理（灰度+CLAHE+小区域放大）与置信度过滤，
    只替换底层模型，确保对比只有「模型」这一个变量。
    """

    def __init__(self, ocr_provider, det: str, rec: str, cls: str | None = None):
        from rapidocr_onnxruntime import RapidOCR
        # 换模型前先校验字典（未内嵌字典的 rec 会在解码时崩，提前给可读提示）
        ocr_provider._check_rec_dict(rec, str(Path(rec).parent))
        kw = {"det_model_path": det, "rec_model_path": rec}
        if cls:
            kw["cls_model_path"] = cls
        self._eng = RapidOCR(**kw)
        self._op = ocr_provider

    def ocr_image(self, img, min_score: float | None = None):
        arr = self._op.preprocess_for_ocr(img)
        result, _ = self._eng(arr)
        if not result:
            return ""
        thr = self._op.MIN_SCORE if min_score is None else min_score
        lines = []
        for item in result:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            payload = item[1]
            if isinstance(payload, (list, tuple)):
                txt = payload[0] if payload else ""
                sc = payload[1] if len(payload) > 1 else None
            else:
                txt, sc = payload, (item[2] if len(item) > 2 else None)
            try:
                sc = float(sc) if sc is not None else None
            except (TypeError, ValueError):
                sc = None
            if sc is not None and sc < thr:
                continue
            if txt:
                lines.append(txt)
        return "\n".join(lines).strip()


def run_image(engine, img, label: str, gt_text: str | None):
    t0 = time.time()
    try:
        text = engine.ocr_image(img)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] ❌ 识别异常：{exc!r}")
        return None
    dt = time.time() - t0
    if gt_text is None:
        print(f"\n--- {label} | {dt:.2f}s | 识别结果 ---")
        print(text)
        return None
    recall, precision, n_gt, n_rec = score(gt_text, text)
    print(f"\n--- {label} | recall={recall*100:.1f}% precision={precision*100:.1f}% "
          f"| 原文{n_gt}字 / 识别{n_rec}字 | {dt:.2f}s ---")
    show_diff(gt_text, text)
    return recall


def main():
    ap = argparse.ArgumentParser(description="星衍质控 OCR 准确率基准")
    ap.add_argument("--image", help="真实截图路径（给定则评测该图，不跑合成基准）")
    ap.add_argument("--gt", help="该截图对应的标准答案 txt（缺省则只打印识别结果）")
    ap.add_argument("--dump", action="store_true", help="只打印识别结果，不算分")
    ap.add_argument("--sizes", default="16,20,24", help="合成基准字号，逗号分隔")
    ap.add_argument("--degrade", action="store_true", help="额外跑一组降质样本（更接近真实截图）")
    ap.add_argument("--det", help="A/B 对比：指定检测模型 .onnx（如 PP-OCRv5 det）")
    ap.add_argument("--rec", help="A/B 对比：指定识别模型 .onnx（需与 det 同代）")
    ap.add_argument("--cls", help="A/B 对比：指定方向分类模型 .onnx（可选）")
    args = ap.parse_args()

    import ocr_provider
    ok, why = ocr_provider.availability()
    print(f"OCR 引擎可用性：{ok} {why}")
    if not ok:
        print("→ 请先安装依赖：pip install -r requirements.txt")
        sys.exit(1)

    # 默认用项目内置引擎；给了 --det/--rec 则换成待评测的候选模型
    engine = ocr_provider
    if args.det or args.rec:
        if not (args.det and args.rec):
            print("❌ --det 与 --rec 必须成对给出（检测与识别模型需同代，v5 det 不能配 v3 rec）")
            sys.exit(2)
        try:
            engine = AltEngine(ocr_provider, args.det, args.rec, args.cls)
            print(f"A/B 模式：det={Path(args.det).name} rec={Path(args.rec).name}")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 候选模型加载失败：{exc}")
            sys.exit(3)

    if args.image:
        from PIL import Image
        img = Image.open(args.image).convert("RGB")
        gt_text = None
        if args.gt and not args.dump:
            gt_text = Path(args.gt).read_text(encoding="utf-8")
        run_image(engine, img, Path(args.image).name, gt_text)
        return

    gt_text = "\n".join(GT_LINES)
    summary = []
    for size in [int(s) for s in args.sizes.split(",") if s.strip()]:
        img = render(GT_LINES, size)
        r = run_image(engine, img, f"合成 {size}px 清晰印刷体", gt_text)
        if r is not None:
            summary.append((f"{size}px 清晰", r))
        if args.degrade:
            r2 = run_image(engine, degrade(img), f"合成 {size}px 降质(0.75x+噪声+JPEG60)",
                           gt_text)
            if r2 is not None:
                summary.append((f"{size}px 降质", r2))

    print("\n════════ 汇总 ════════")
    for name, r in summary:
        print(f"  {name:<16} recall = {r*100:.1f}%")
    print("提示：合成清晰样本分数偏乐观；真实 PACS 截图请用 --image/--gt 实测。")


if __name__ == "__main__":
    main()
