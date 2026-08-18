# -*- coding: utf-8 -*-
"""R18 检查完整性（原 R20 必查要素已并入）+ R21 性别-部位联动 测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine

eng = RuleEngine()


def _find(rule_id, report, meta):
    return [f for f in eng.run(report, meta) if f.rule_id == rule_id]


# ---------- R18 检查完整性（原 R20 必查要素已并入 R18-COVERAGE）----------
def test_r20_chest_ct_missing_elements():
    report = "影像描述：右肺上叶见磨玻璃影。\n影像诊断：磨玻璃结节，建议随访。"
    hits = _find("R18-COVERAGE", report, {"applied_site": "胸部CT"})
    assert hits, "胸部CT缺肺纹理/纵隔/胸膜等要素应报漏写"
    assert "肺纹理" in hits[0].message


def test_r20_chest_ct_complete_no_hit():
    report = ("影像描述：双肺纹理清晰，肺实质未见异常密度影，肺门及纵隔未见肿大淋巴结，"
              "胸膜无增厚，心脏大小正常，骨性胸廓完整。\n影像诊断：胸部未见明显异常。")
    hits = _find("R18-COVERAGE", report, {"applied_site": "胸部CT"})
    assert not hits, "胸部CT要素齐全不应报漏写"


def test_r20_lumbar_missing():
    report = "影像描述：腰椎生理曲度存在，椎体形态可。\n影像诊断：腰椎退行性变。"
    hits = _find("R18-COVERAGE", report, {"applied_site": "腰椎"})
    assert hits, "腰椎缺椎间盘/硬膜囊等要素应报"
    assert "硬膜囊" in hits[0].message


def test_r20_no_site_no_hit():
    report = "影像描述：右肺上叶见结节。\n影像诊断：结节。"
    hits = _find("R18-COVERAGE", report, {})
    assert not hits, "无登记部位/类型信息时不应误报"


def test_r20_head_ct():
    report = "影像描述：右侧基底节区见低密度灶。\n影像诊断：腔隙性脑梗死可能。"
    hits = _find("R18-COVERAGE", report, {"applied_site": "头颅CT"})
    assert hits, "头颅CT缺脑室/中线/颅骨等要素应报"
    assert "脑室" in hits[0].message


# ---------- R21 性别-部位联动 ----------
def test_r21_male_breast_mammography():
    # 2026-08-18 语义更新：乳腺检查是否判『与男性性别不符』取决于方式——
    # 钼靶/乳腺X线报（男性常规不做钼靶）；乳腺超声不报（男性乳房发育是合理适应证）。
    report = "影像描述：双侧乳腺腺体致密。\n影像诊断：乳腺未见异常。"
    hits = _find("R21-GENDER-SITE", report, {"applied_site": "乳腺", "modality": "钼靶", "gender": "男"})
    assert hits, "男性做钼靶应报检查部位与性别不符"

def test_r21_male_breast_ultrasound_ok():
    report = "影像描述：双乳腺体组织未见异常。\n影像诊断：未见异常。"
    hits = _find("R21-GENDER-SITE", report, {"applied_site": "乳腺", "modality": "超声", "gender": "男"})
    assert not hits, "男性乳腺超声（乳房发育）不应报"


def test_r21_female_prostate():
    report = "影像描述：前列腺增大。\n影像诊断：前列腺增生。"
    hits = _find("R21-GENDER-SITE", report, {"applied_site": "前列腺", "gender": "女"})
    assert hits, "女性做前列腺检查应报检查部位与性别不符"


def test_r21_female_breast_ok():
    report = "影像描述：双侧乳腺腺体致密。\n影像诊断：乳腺未见异常。"
    hits = _find("R21-GENDER-SITE", report, {"applied_site": "乳腺", "gender": "女"})
    assert not hits, "女性做乳腺检查不应报错"


def test_r21_no_gender_no_hit():
    report = "影像描述：乳腺腺体致密。\n影像诊断：乳腺未见异常。"
    hits = _find("R21-GENDER-SITE", report, {"applied_site": "乳腺"})
    assert not hits, "无性别信息时不应臆断"
