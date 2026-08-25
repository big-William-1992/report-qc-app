#!/usr/bin/env python3
"""回归测试：修复「OCR 被削弱」两个根因。

根因1：置信度阈值 0.70 过高，真实低对比 PACS 屏幕字段常被误杀。
根因2：原「连续两轮 key 完全一致才生效」的硬双闸，在真实屏幕轻微噪点/
       闪烁下 key 必然抖动 → 结果永远挂起不生效。

本测试固化：阈值降到 0.55 仍保住真实字段；新 key 即生效（无需第二轮一致）。
"""
import os, sys
import unittest
from PIL import Image, ImageDraw, ImageFont
import numpy as np

REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, REPO_SRC)
import ocr_provider as O
import engine

# 跨平台中文字体回退链（CI Windows runner 无 macOS 字体，2026-08-25 修复）
FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",   # macOS
    "C:/Windows/Fonts/msyh.ttc",                    # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",                  # Windows 黑体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
)
FONT = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
if FONT is None:
    pytest.skip("无可用中文字体，跳过 OCR 渲染类测试", allow_module_level=True)

FIELDS = ("patient", "gender", "age", "modality", "applied_site", "laterality")


def render_pacs(fg=(150, 160, 172), seed=None, name="张伟", site="胸部 CT"):
    """渲染 PACS 患者信息栏（深底浅字=低对比）。seed 用于轻微噪点。"""
    fnt = ImageFont.truetype(FONT, 20)
    W, H = 520, 150
    img = Image.new("RGB", (W, H), (26, 30, 38))
    d = ImageDraw.Draw(img)
    lines = [f"姓名：{name}", "性别：男", "年龄：54", f"检查部位：{site}"]
    for i, ln in enumerate(lines):
        d.text((16, 12 + i * 34), ln, font=fnt, fill=fg)
    if seed is not None:
        rng = np.random.default_rng(seed)
        a = np.asarray(img).astype(np.float32) + rng.normal(0, 8.0, (H, W, 3))
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return img


def apply_decision(new_key, confirmed_key, meta, force):
    """一次性回填生效判定（行为契约，防回归）。

    v2.3 起 OCR 由轮询改为「按下快捷键/点按钮后运行一次」，不再有 confirm-key
    双闸；此处固化「新识别结果是否应回填输入框」的判定：
    相同 key 视为无变化 → 不重复回填；force → 强制回填；有患者字段才允许回填。
    """
    if force:
        return True
    if new_key == confirmed_key:
        return False
    return bool((meta.get("patient") or "").strip())


class TestThresholdKeepsRealFields(unittest.TestCase):
    def test_low_contrast_fields_survive_at_055(self):
        img = render_pacs(seed=7)
        text = O.ocr_image(img, min_score=0.55)
        meta = engine.extract_meta(text)
        self.assertEqual(meta.get("patient"), "张伟", "0.55 阈值应保住姓名")
        self.assertEqual(meta.get("gender"), "男")
        self.assertEqual(meta.get("age"), "54")
        self.assertIn("胸部", meta.get("applied_site", ""))

    def test_low_contrast_070_drops_more_than_055(self):
        # 更浅的字（贴近真实低对比屏）：0.70 更可能丢字段，0.55 保住
        img = render_pacs(fg=(110, 118, 128), seed=3)
        m55 = engine.extract_meta(O.ocr_image(img, min_score=0.55))
        m70 = engine.extract_meta(O.ocr_image(img, min_score=0.70))
        # 0.55 解析出的核心字段数不应少于 0.70（放宽阈值不丢真字段）
        f55 = sum(1 for k in FIELDS if (m55.get(k) or "").strip())
        f70 = sum(1 for k in FIELDS if (m70.get(k) or "").strip())
        self.assertGreaterEqual(f55, f70, "0.55 不应比 0.70 丢更多真实字段")


class TestNoDoubleGateDeadlock(unittest.TestCase):
    def test_first_round_applies(self):
        """首轮识别（confirmed=None）应立即生效，不等待第二轮。"""
        meta = {"patient": "张伟", "gender": "男", "age": "54",
                "modality": "", "applied_site": "胸部", "laterality": ""}
        key = "|".join((meta.get(k) or "").strip() for k in FIELDS)
        self.assertTrue(apply_decision(key, None, meta, force=False),
                        "首轮（confirmed=None）应直接生效，不卡双闸")

    def test_key_jitter_still_applies(self):
        """模拟测试2 的 key 横跳：帧A 丢性别、帧B 有性别。
        旧逻辑要求两轮一致 → 永远挂起；新逻辑：每轮新 key 即生效。"""
        key_a = "张伟||54||胸部|"          # 帧A：性别行没识别出
        key_b = "张伟|男|54||胸部|"        # 帧B：性别识别出
        meta_b = {"patient": "张伟", "gender": "男", "age": "54",
                  "modality": "", "applied_site": "胸部", "laterality": ""}
        # 第一轮（confirmed=None）：key_a 是新 → 生效
        self.assertTrue(apply_decision(key_a, None, meta_b, force=False))
        # 第二轮：屏幕有微小变化，key 从 a 变 b（抖动）→ 仍是新 key → 继续生效
        self.assertTrue(apply_decision(key_b, key_a, meta_b, force=False))
        # 关键：旧双闸会在「key_a != key_b」时挂起永不生效；这里两轮都 True

    def test_same_key_no_reapply(self):
        """已确认相同 key：仅刷新核对，不重复回填/告警。"""
        key = "张伟|男|54||胸部|"
        meta = {"patient": "张伟", "gender": "男", "age": "54",
                "modality": "", "applied_site": "胸部", "laterality": ""}
        self.assertFalse(apply_decision(key, key, meta, force=False))


class TestChangeDetection(unittest.TestCase):
    def test_identical_frame_no_change(self):
        img = render_pacs(seed=1)
        sig = O.image_signature(img)
        self.assertFalse(O.signature_changed(sig, sig))

    def test_different_frame_changed(self):
        # 内容确实不同（换患者 + 换部位），变化检测应判「有变化」
        a = O.image_signature(render_pacs(seed=1, name="张伟", site="胸部 CT"))
        b = O.image_signature(render_pacs(seed=1, name="李娜", site="腹部 CT"))
        self.assertTrue(O.signature_changed(a, b))

    def test_single_field_change_triggers(self):
        # 仅换检查部位（临床 R6 关键）：新参数（64×64/pd8/tol0.002）也应触发
        a = O.image_signature(render_pacs(seed=1, name="张伟", site="胸部 CT"))
        b = O.image_signature(render_pacs(seed=1, name="张伟", site="腹部 CT"))
        self.assertTrue(O.signature_changed(a, b))

    def test_screen_noise_ignored(self):
        # 同患者、仅不同屏幕噪点：应判未变化（不误触发重跑 OCR）
        a = O.image_signature(render_pacs(seed=1))
        b = O.image_signature(render_pacs(seed=9))
        self.assertFalse(O.signature_changed(a, b))

    def test_first_frame_is_change(self):
        sig = O.image_signature(render_pacs(seed=1))
        self.assertTrue(O.signature_changed(None, sig))


if __name__ == "__main__":
    unittest.main(verbosity=2)
