"""schema_version 单测：default_rules_config / load_rules_config / save_rules_config。"""
import json
import os
import tempfile

from engine import (
    default_rules_config, load_rules_config, save_rules_config,
    RULES_CONFIG_SCHEMA_VERSION,
)


def test_default_contains_schema_version():
    cfg = default_rules_config()
    assert cfg["schema_version"] == RULES_CONFIG_SCHEMA_VERSION


def test_save_adds_schema_version_if_missing():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "rules_config.json")
        bare = {"typos": {"错别字": "正确字"}, "conflicts": []}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bare, f)
        cfg = load_rules_config(path)
        assert cfg["schema_version"] == RULES_CONFIG_SCHEMA_VERSION


def test_load_existing_preserves_custom():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "rules_config.json")
        custom = {
            "schema_version": 1,
            "typos": {"自定义错": "自定义正"},
            "conflicts": [{"a": "x", "b": "y"}],
            "enable_r19": False,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(custom, f)
        cfg = load_rules_config(path)
        assert cfg["schema_version"] == 1
        assert cfg["conflicts"] == [{"a": "x", "b": "y"}]
        assert cfg["enable_r19"] is False


def test_save_sets_schema_version():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "rules_config.json")
        save_rules_config({}, path)
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg["schema_version"] == RULES_CONFIG_SCHEMA_VERSION


def test_missing_file_falls_back_to_defaults():
    cfg = load_rules_config("/tmp/_nonexistent_rules_test_.json")
    assert cfg["schema_version"] == RULES_CONFIG_SCHEMA_VERSION
    assert isinstance(cfg["typos"], dict)
