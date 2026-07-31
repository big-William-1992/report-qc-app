"""中文临床 NER（词典式）单元测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import zh_ner as ner
from zh_ner import Entity, LexiconBackend, ModelBackend, set_backend, get_backend


def _reset():
    set_backend(LexiconBackend())


def test_extract_organ_with_side():
    ents = ner.extract_entities("左肺上叶见磨玻璃密度影，右肺下叶见结节")
    organs = [e for e in ents if e.label == "部位"]
    # 左肺 → lung/left，右肺 → lung/right
    left = [e for e in organs if e.side == "left"]
    right = [e for e in organs if e.side == "right"]
    assert left and right
    assert left[0].canonical == "lung"
    assert right[0].canonical == "lung"


def test_extract_signs():
    ents = ner.extract_entities("右肺上叶见磨玻璃密度影，伴斑片状影及索条影")
    signs = {e.canonical for e in ents if e.label == "征象"}
    assert "磨玻璃影" in signs
    assert "斑片影" in signs
    assert "索条影" in signs


def test_extract_degree():
    ents = ner.extract_entities("两肺少许斑片影")
    degs = [e for e in ents if e.label == "程度"]
    assert degs and degs[0].canonical == "轻度"


def test_extract_followup():
    ents = ner.extract_entities("建议3个月后复查")
    fups = [e for e in ents if e.label == "随访"]
    assert fups


def test_non_max_suppress_organ_overlap():
    # 「肝左叶」(len3) 应作为整体被抽出，且不出现被其包含的短别名「肝」重复告警
    ents = ner.extract_entities("肝左叶见增殖灶")
    organs = [e for e in ents if e.label == "部位"]
    texts = [e.text for e in organs]
    assert "肝左叶" in texts
    assert "肝" not in texts
    # 方位应从别名内部解析
    assert organs[0].side == "left"
    assert organs[0].canonical == "liver"


def test_entities_by_label():
    signs = ner.entities_by_label("磨玻璃影、结节、钙化", "征象")
    assert len(signs) == 3


def test_backend_switch_and_model_stub():
    _reset()
    assert isinstance(get_backend(), LexiconBackend)
    set_backend(ModelBackend())
    assert isinstance(get_backend(), ModelBackend)
    try:
        get_backend().extract("测试")
        assert False, "ModelBackend 应抛出 NotImplementedError"
    except NotImplementedError:
        pass
    finally:
        _reset()


def test_empty_text():
    assert ner.extract_entities("") == []
    assert ner.extract_entities("   ") == []
