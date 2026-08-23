"""检索增强（RAG）抽象层。

- SimpleCorpusRetriever：零依赖内存关键词召回，语料来自既有词表 / 历史修正登记表。
- ChromaRetriever：可选 chromadb 向量库，用于 P2 生产环境灌入本院规范 / RadLex / ACR。
"""
from __future__ import annotations

import os
import re
from typing import List, Protocol


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 4) -> List[str]: ...


def _tokens(text: str):
    toks = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", text)
    return set(t for t in toks if len(t) >= 1)


class SimpleCorpusRetriever:
    def __init__(self, corpus: List[str]):
        self.corpus = corpus

    def retrieve(self, query: str, top_k: int = 4) -> List[str]:
        q = _tokens(query)
        scored = []
        for doc in self.corpus:
            overlap = len(q & _tokens(doc))
            if overlap:
                scored.append((overlap, doc))
        scored.sort(key=lambda x: -x[0])
        return [doc for _, doc in scored[:top_k]]


def build_default_corpus() -> List[str]:
    corpus: List[str] = []
    try:
        from highfreq_lexicon import _HIGHFREQ_WORDS
        for term, cat in _HIGHFREQ_WORDS:
            corpus.append(f"高频放射术语：{term}（{cat}）")
    except Exception:
        pass
    try:
        from anatomy_lexicon import ANATOMY_SYNONYMS
        for k, vs in ANATOMY_SYNONYMS.items():
            corpus.append(f"解剖同义：{k} ↔ {'/'.join(vs)}")
    except Exception:
        pass
    reg_path = os.path.join(os.path.dirname(__file__), "..", "放射科报告修正登记表.docx")
    if os.path.exists(reg_path):
        corpus.append("（历史修正登记表可用，建议 P2 接入 docx 抽取为向量语料）")
    return corpus


class ChromaRetriever:
    def __init__(self, collection_name: str = "radiology_qc", path: str = None):
        try:
            import chromadb
        except ImportError:
            raise RuntimeError("chromadb 未安装：pip install chromadb")
        self._client = chromadb.PersistentClient(path=path) if path else chromadb.Client()
        self._col = self._client.get_or_create_collection(collection_name)

    def retrieve(self, query: str, top_k: int = 4) -> List[str]:
        res = self._col.query(query_texts=[query], n_results=top_k)
        return [d for d in (res.get("documents", [[]])[0] or [])]


def get_retriever(kind: str = "simple", **kwargs) -> Retriever:
    if kind == "simple":
        return SimpleCorpusRetriever(build_default_corpus())
    if kind == "chroma":
        return ChromaRetriever(**kwargs)
    raise ValueError(f"unknown retriever kind: {kind}")
