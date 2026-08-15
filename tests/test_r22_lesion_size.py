# -*- coding: utf-8 -*-
"""R22 病灶尺寸-术语一致性测试：称结节但测量>3cm（应称肿块）/ 称肿块但<1cm（应称结节）。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from engine import RuleEngine

eng = RuleEngine()


def _hits(report):
    return [f for f in eng.run(report, {}) if f.rule_id == "R22-SIZE"]


def test_nodule_35cm_over_3():
    hits = _hits("影像描述：右肺上叶见结节，直径约3.5cm，边界清晰。\n影像诊断：右肺结节，建议随访。")
    assert hits and "3.5" in hits[0].message and "肿块" in hits[0].message


def test_nodule_20cm_ok():
    assert not _hits("影像描述：右肺上叶见结节，直径约2.0cm。\n影像诊断：右肺结节。")


def test_nodule_double_dimension_over():
    hits = _hits("影像描述：见结节，大小约4.2×3.1cm。\n影像诊断：结节。")
    assert hits and "4.2" in hits[0].message


def test_mass_08cm_under():
    hits = _hits("影像描述：见肿块，大小约0.8cm。\n影像诊断：肿块。")
    assert hits and "结节" in hits[0].message


def test_mass_50cm_ok():
    assert not _hits("影像描述：见肿块，大小约5.0cm。\n影像诊断：肿块。")


def test_nodule_mm_conversion():
    hits = _hits("影像描述：见结节，直径约35mm。\n影像诊断：结节。")
    assert hits and "3.5" in hits[0].message


def test_no_measurement_ok():
    assert not _hits("影像描述：右肺上叶见结节，边界清晰。\n影像诊断：结节。")


def test_nodule_boundary_3cm_ok():
    assert not _hits("影像描述：见结节，大小约3.0cm。\n影像诊断：结节。")


def test_nodule_small_double_ok():
    assert not _hits("影像描述：右肺上叶见结节，大小约1.2×0.8cm。\n影像诊断：结节。")
