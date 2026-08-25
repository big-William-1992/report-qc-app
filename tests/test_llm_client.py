"""llm_client.py 单元测试 — 补齐 0% 覆盖 (2026-08-25 审计 P3)。

覆盖:
- _extract_json(): 围栏/裸数组/噪音包裹/非法输入
- OllamaClient: think 参数剥离重试 / 连接失败降级
- CloudAPIClient: available 无 key False / HTTP 400 降级
- get_llm_client(): provider 分派

全部 mock 网络层(urllib.request.urlopen), 无真实外呼。
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import llm_client as lc  # noqa: E402


class TestExtractJson(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(lc._extract_json('[{"a":1}]'), [{"a": 1}])

    def test_code_fence(self):
        self.assertEqual(lc._extract_json('```json\n[1,2]\n```'), [1, 2])

    def test_fence_no_lang(self):
        self.assertEqual(lc._extract_json('```\n[]\n```'), [])

    def test_noise_wrapped(self):
        t = '好的，结果如下：\n[{"error_type":"R8-TYPO"}]\n以上。'
        self.assertEqual(lc._extract_json(t), [{"error_type": "R8-TYPO"}])

    def test_empty_none(self):
        self.assertIsNone(lc._extract_json(""))
        self.assertIsNone(lc._extract_json(None))

    def test_invalid_none(self):
        self.assertIsNone(lc._extract_json("完全不是JSON的文本"))

    def test_think_block_stripped(self):
        """Qwen3 输出常带 <think></think> 前缀, JSON 抽取须穿透。"""
        t = '<think>\n\n</think>\n\n[{"x":true}]'
        self.assertEqual(lc._extract_json(t), [{"x": True}])


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class TestOllamaClient(unittest.TestCase):
    def _client(self):
        return lc.OllamaClient(base_url="http://mock:11434", model="qwen2.5:3b")

    def test_available_false_when_down(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("conn refused")):
            self.assertFalse(self._client().available())

    def test_chat_ok(self):
        body = json.dumps({"message": {"content": '[{"e":1}]'}}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            r = self._client().chat("sys", "user")
        self.assertIsNone(r.error)
        self.assertEqual(r.parsed, [{"e": 1}])

    def test_chat_http_error_degrades(self):
        """非 think 类 HTTPError → 重试后仍失败, 返回 error 而不抛异常。"""
        err = urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            r = self._client().chat("sys", "user")
        self.assertIsNotNone(r.error)
        self.assertIsNone(r.parsed)


class TestCloudAPIClient(unittest.TestCase):
    def test_available_requires_key(self):
        c = lc.CloudAPIClient(api_base="http://x/v1", api_key="", model="m")
        self.assertFalse(c.available())
        c2 = lc.CloudAPIClient(api_base="http://x/v1", api_key="sk-test", model="m")
        self.assertTrue(c2.available())

    def test_chat_400_degrades(self):
        c = lc.CloudAPIClient(api_base="http://x/v1", api_key="k", model="m")
        # enable_thinking 场景: DashScope 对 qwen3 非流式要求 false,
        # 服务端 400 时必须优雅降级(返回 error)而非崩溃
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            r = c.chat("s", "u")
        self.assertEqual(r.error, "HTTP 400")
        self.assertIsNone(r.parsed)

    def test_chat_ok_parses(self):
        body = json.dumps(
            {"choices": [{"message": {"content": "[]"}}]}).encode()
        c = lc.CloudAPIClient(api_base="http://x/v1", api_key="k", model="m")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            r = c.chat("s", "u")
        self.assertEqual(r.parsed, [])


class TestProviderDispatch(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            lc.get_llm_client({"provider": "nope"})

    def test_mlx_provider(self):
        from llm_client import MLXClient
        c = lc.get_llm_client({"provider": "mlx", "model": "/tmp/x"})
        self.assertIsInstance(c, MLXClient)

    def test_cloud_provider(self):
        from llm_client import CloudAPIClient
        cfg = {"provider": "cloud", "api_base": "https://a/v1",
               "api_key": "k", "model": "m"}
        self.assertIsInstance(lc.get_llm_client(cfg), CloudAPIClient)


if __name__ == "__main__":
    unittest.main()
