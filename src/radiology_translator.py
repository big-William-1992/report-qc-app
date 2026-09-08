"""术语处理：从英文术语表提取中文，构建双向对照和英文→中文映射
================================================================================
供医学白名单和引擎使用。
"""
from __future__ import annotations

from radiology_glossary_en_zh import (
    ANATOMY_EN_ZH, FINDING_EN_ZH, MODALITY_EN_ZH,
    DISEASE_EN_ZH, CONCLUSION_EN_ZH, ALL_TERMS
)

def get_zh_to_en_map() -> dict:
    """中文 → 英文 映射（用于生成英文标签/提示）"""
    zh_to_en = {}
    for en, zh in ALL_TERMS.items():
        if en in {'GGO', 'NSCLC', 'SCLC', 'PE', 'CT', 'MRI', 'PET'}:
            continue
        if zh not in zh_to_en:
            zh_to_en[zh] = []
        if en not in zh_to_en[zh]:
            zh_to_en[zh].append(en)
    return zh_to_en

def get_en_to_zh_map() -> dict:
    """英文 → 中文 映射（大小写不敏感）"""
    result = {}
    for en, zh in ALL_TERMS.items():
        result[en.lower()] = zh
        result[en.upper()] = zh
        result[en] = zh
    return result

def translate_to_zh(text: str) -> str:
    """将英文术语翻译为中文（逐词替换）"""
    en_to_zh = get_en_to_zh_map()
    result = text
    for en, zh in sorted(en_to_zh.items(), key=lambda x: -len(x[0])):
        if len(en) < 3:
            continue
        import re
        pattern = r'\b' + re.escape(en) + r'\b'
        result = re.sub(pattern, zh, result, flags=re.IGNORECASE)
    return result

def get_stats() -> dict:
    """术语统计"""
    return {
        "anatomy": len(ANATOMY_EN_ZH),
        "finding": len(FINDING_EN_ZH),
        "modality": len(MODALITY_EN_ZH),
        "disease": len(DISEASE_EN_ZH),
        "conclusion": len(CONCLUSION_EN_ZH),
        "total": len(ALL_TERMS),
        "unique_zh": len(set(ALL_TERMS.values())),
    }

if __name__ == "__main__":
    stats = get_stats()
    print("术语统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print()
    en_to_zh = get_en_to_zh_map()
    print("示例翻译:")
    samples = ["lung", "GGO", "pulmonary embolism", "left upper lobe", "contrast-enhanced"]
    for s in samples:
        zh = en_to_zh.get(s.lower(), en_to_zh.get(s.upper(), "?"))
        print(f"  {s} → {zh}")
