"""B 类知识资源（知识图谱 / 实体关系 / 报告规范）目录完整性回归。

说明
----
本文件验证 src/dataset_catalog.py 中 KNOWLEDGE_RESOURCES（B 类）的登记质量：
- 11 个资源（6 个英文：RadGraph / RadLex Playbook / RadElement / RadReport / LOINC /
  ACR 分级词典；5 个中文专项：CBLUE / CCKS / CMedKG / CHIP / 中文放射同义词表规划）
  均在册；
- 字段完整、type 分类正确、related 关联可达；
- catalog_markdown() 渲染包含这些资源；
- 辅助函数按类型检索正确。

B 类资源是术语/图谱/模板规范（非报告语料），不进入文本级质控回归，
仅作为 catalog 单一事实来源的一部分接受结构校验。原始术语库均需授权下载，
本模块只登记元数据，不打包任何原始数据。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dataset_catalog as dc


# 11 个 B 类资源 key → 期望 type（6 英文 + 5 中文专项，2026-07-31 增补）
_EXPECTED = {
    "radgraph": "knowledge_graph",
    "radlex_playbook": "nomenclature",
    "radelement": "common_data_element",
    "radreport": "template_library",
    "loinc": "ontology",
    "acr_reporting_lexicons": "reporting_lexicon",
    # —— 中文专项 ——
    "cblue": "nlp_benchmark",
    "ccks": "nlp_benchmark",
    "cmedkg": "knowledge_graph",
    "chip": "nlp_benchmark",
    "zh_radiology_synonyms": "nomenclature",
}

_REQUIRED_FIELDS = {"name", "owner", "type", "scope", "license", "use_in_app", "access"}


def test_b_class_resources_present():
    # 6 个 B 类资源全部登记
    for key in _EXPECTED:
        assert key in dc.KNOWLEDGE_RESOURCES, f"目录缺少 B 类资源 {key}"
    # list_knowledge_resources 数量吻合
    assert len(dc.list_knowledge_resources()) == len(_EXPECTED)


def test_knowledge_resource_fields_complete():
    # 每个资源字段完整、type 取值合法
    valid_types = {
        "knowledge_graph", "nomenclature", "common_data_element",
        "template_library", "ontology", "reporting_lexicon",
        "nlp_benchmark",
    }
    for key, meta in dc.KNOWLEDGE_RESOURCES.items():
        missing = _REQUIRED_FIELDS - set(meta.keys())
        assert not missing, f"{key} 缺字段: {missing}"
        assert meta["type"] in valid_types, f"{key} type 非法: {meta['type']}"
        for f in ("name", "owner", "scope", "license", "use_in_app", "access"):
            assert isinstance(meta[f], str) and meta[f].strip(), f"{key}.{f} 为空"


def test_knowledge_resource_type_correct():
    # 各资源 type 与登记一致
    for key, rtype in _EXPECTED.items():
        assert dc.KNOWLEDGE_RESOURCES[key]["type"] == rtype, f"{key} type 应为 {rtype}"


def test_knowledge_resources_by_type():
    # 按类型检索正确
    assert "radgraph" in dc.knowledge_resources_by_type("knowledge_graph")
    assert "radreport" in dc.knowledge_resources_by_type("template_library")
    assert "loinc" in dc.knowledge_resources_by_type("ontology")
    assert dc.knowledge_resources_by_type("nonexistent_type") == []


def test_knowledge_resource_related_reachable():
    # related 引用的 key 必须在任一已知资源/数据集中可达（不悬空）
    known = set(dc.DATASETS) | set(dc.ONTOLOGIES) | set(dc.KNOWLEDGE_RESOURCES)
    for key, meta in dc.KNOWLEDGE_RESOURCES.items():
        for rel in meta.get("related", []):
            assert rel in known, f"{key}.related 指向未知 key: {rel}"


def test_get_knowledge_resource_ok():
    # 辅助函数取数
    assert dc.get_knowledge_resource("radgraph")["name"] == "RadGraph"
    assert dc.get_knowledge_resource("__nope__") is None


def test_catalog_markdown_includes_b_class():
    # markdown 渲染含 B 类小节与关键资源名（英文 + 中文）
    md = dc.catalog_markdown()
    assert "B 类" in md
    for name in ("RadGraph", "RadReport", "LOINC", "RadLex Playbook",
                 "CBLUE", "CMedKG"):
        assert name in md, f"catalog_markdown 未含 {name}"


# —— 中文专项资源（2026-07-31 增补）专项校验 ——
_ZH_EXPECTED = {
    "cblue": "nlp_benchmark",
    "ccks": "nlp_benchmark",
    "cmedkg": "knowledge_graph",
    "chip": "nlp_benchmark",
    "zh_radiology_synonyms": "nomenclature",
}


def test_chinese_resources_present():
    # 5 个中文专项资源全部登记
    for key in _ZH_EXPECTED:
        assert key in dc.KNOWLEDGE_RESOURCES, f"目录缺少中文资源 {key}"
    # 全局计数随中文条目增长（6 → 11）
    assert len(dc.list_knowledge_resources()) == 11


def test_chinese_resources_typed():
    # 中文资源 type 取值合法且与登记一致
    all_types = {
        "knowledge_graph", "nomenclature", "common_data_element",
        "template_library", "ontology", "reporting_lexicon", "nlp_benchmark",
    }
    for key, rtype in _ZH_EXPECTED.items():
        meta = dc.KNOWLEDGE_RESOURCES[key]
        assert meta["type"] == rtype, f"{key} type 应为 {rtype}"
        assert meta["type"] in all_types, f"{key} type 非法: {meta['type']}"


def test_chinese_resources_chinese_content():
    # 中文资源名称/范围确实含中文，避免误登记的英文条目混入中文专项
    for key in _ZH_EXPECTED:
        meta = dc.KNOWLEDGE_RESOURCES[key]
        assert any(ord(c) > 0x4E00 for c in meta["name"]), f"{key}.name 不含中文"
        assert any(ord(c) > 0x4E00 for c in meta["scope"]), f"{key}.scope 不含中文"


def test_no_dataset_field_leak_in_knowledge():
    # 知识资源不应混入数据集特有字段（保持两类语义清晰）
    for key, meta in dc.KNOWLEDGE_RESOURCES.items():
        assert "has_report" not in meta, f"{key} 不应含 has_report 字段"
        assert "size" not in meta, f"{key} 不应含 size 字段"
