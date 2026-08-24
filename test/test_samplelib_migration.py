"""samplelib 迁移路径探测单测：_legacy_sample_db_candidates / _appdata_db。"""
import os
import sys
import tempfile
from unittest import mock

from samplelib import _legacy_sample_db_candidates, _appdata_db, _legacy_source_dir


def test_candidates_deduped():
    cands = _legacy_sample_db_candidates()
    paths = [os.path.abspath(c) for c in cands]
    assert len(paths) == len(set(paths))


def test_appdata_db_first():
    cands = _legacy_sample_db_candidates()
    assert os.path.abspath(cands[0]) == os.path.abspath(_appdata_db())


def test_legacy_source_dir_is_project_root():
    src_dir = _legacy_source_dir()
    assert os.path.isdir(os.path.join(src_dir, "src")) or os.path.isdir(os.path.join(src_dir, "assets"))


def test_candidates_second_is_legacy_assets():
    cands = _legacy_sample_db_candidates()
    if len(cands) >= 2:
        assert "assets" in cands[1] and "samples.db" in cands[1]
