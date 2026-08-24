"""LLM 客户端抽象层。

底座默认 Qwen2.5-3B（本地），teacher / 训练好的模型只需改模型名即可替换。
Provider：
  - ollama  ：本地 Ollama（数据不出院，默认，满足医疗合规）
  - vllm    ：本地 vLLM 提供的 OpenAI 兼容接口（部署微调模型常用，单卡高吞吐）
  - cloud   ：云端 OpenAI 兼容 API（仅脱敏开发环境，禁止传 PHI）
全部基于标准库 urllib，无新增硬依赖；模型不可用时优雅降级（返回 error，不抛异常）。
配置优先级：函数入参 config > src/llm_config.json > 环境变量。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


def _extract_json(text: str):
    """从模型输出中稳健抽取 JSON：兼容 ```json 代码围栏、前后多余文本、截断。"""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        s = t.find(opener)
        e = t.rfind(closer)
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except Exception:
                pass
    return None


@dataclass
class LLMResponse:
    text: str = ""
    parsed: Optional[object] = None
    model: str = ""
    elapsed_ms: int = 0
    error: Optional[str] = None


# 混合推理模型（Qwen3 / DeepSeek-R1 等）默认先生成长段 <think> 推理链再给答案，
# token 数被放大几十倍 → 延迟暴涨。请求层直接关闭思考。
_THINK_OFF_RE = re.compile(r"qwen3|deepseek-r\d", re.IGNORECASE)


class LLMClient:
    def chat(self, system: str, user: str, *, json_mode: bool = True,
             temperature: float = 0.1) -> LLMResponse:
        raise NotImplementedError

    def available(self) -> bool:
        return True


class OllamaClient(LLMClient):
    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5:3b", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:
            return False

    def chat(self, system: str, user: str, *, json_mode: bool = True,
             temperature: float = 0.1) -> LLMResponse:
        t0 = time.time()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        if _THINK_OFF_RE.search(self.model or ""):
            payload["think"] = False
        last_err = None
        body = None
        for attempt in range(3):  # 重试：吸收本地服务瞬时抖动 / 旧版不识别 think 参数
            if attempt > 0:
                time.sleep(1)  # 2026-08-24 修复：重试间隔1秒，避免紧密重试风暴
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.base_url + "/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = json.loads(r.read().decode("utf-8"))
                last_err = None
                break
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"  # 2026-08-24: 不泄露完整 URL/响应体
                if "think" in payload:  # 旧版 Ollama 不支持 think → 去掉重试
                    payload.pop("think")
                    continue
                break
            except urllib.error.URLError as e:
                last_err = f"连接失败: {getattr(e.reason, 'strerror', '网络不可达')}"
            except Exception as e:
                last_err = "请求异常"  # 不泄露内部细节
                break
        if last_err:
            return LLMResponse(text="", parsed=None, model=self.model,
                               elapsed_ms=int((time.time() - t0) * 1000), error=last_err)
        content = body.get("message", {}).get("content", "")
        parsed = _extract_json(content) if json_mode else None
        return LLMResponse(
            text=content, parsed=parsed, model=self.model,
            elapsed_ms=int((time.time() - t0) * 1000), error=None,
        )


class MLXClient(LLMClient):
    """本机 Apple Silicon 训练/推理：mlx_lm 加载底座+LoRA adapter，进程内生成。

    config: {"provider":"mlx","model":<HF或本地路径>,"adapter_path":<adapters目录>,"max_tokens":512}
    """

    _cache: dict = {}
    _cache_lock = None  # 延迟初始化，避免导入时依赖 threading

    def __init__(self, model_path: str, adapter_path: Optional[str] = None,
                 max_tokens: int = 512, timeout: int = 300):
        self.model_path = model_path
        self.adapter_path = adapter_path
        self.max_tokens = max_tokens

    @classmethod
    def _get_lock(cls):
        if cls._cache_lock is None:
            import threading
            cls._cache_lock = threading.Lock()
        return cls._cache_lock

    @classmethod
    def clear_cache(cls):
        """清除模型缓存，释放内存。"""
        with cls._get_lock():
            cls._cache.clear()

    def available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        from mlx_lm import load
        key = (self.model_path, self.adapter_path)
        with self._get_lock():
            if key not in MLXClient._cache:
                MLXClient._cache[key] = load(
                    self.model_path, adapter_path=self.adapter_path)
        return MLXClient._cache[key]

    def chat(self, system: str, user: str, *, json_mode: bool = True,
             temperature: float = 0.1) -> LLMResponse:
        import time as _t
        t0 = _t.time()
        try:
            from mlx_lm import generate
            model, tok = self._load()
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
            prompt = tok.apply_chat_template(msgs, add_generation_prompt=True,
                                             tokenize=False)
            text = generate(model, tok, prompt=prompt,
                            max_tokens=self.max_tokens, verbose=False)
            return LLMResponse(text=text, parsed=_extract_json(text) if json_mode else None,
                               model=f"mlx:{os.path.basename(self.model_path.rstrip('/'))}",
                               elapsed_ms=int((_t.time() - t0) * 1000))
        except Exception as e:
            return LLMResponse(text="", parsed=None, model="mlx",
                               elapsed_ms=int((_t.time() - t0) * 1000), error=str(e))


class CloudAPIClient(LLMClient):
    """OpenAI 兼容接口（阿里云百炼 / OpenAI 等）。仅用于脱敏开发环境。"""

    def __init__(self, api_base: str, api_key: str, model: str, timeout: int = 120):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user: str, *, json_mode: bool = True,
             temperature: float = 0.1) -> LLMResponse:
        t0 = time.time()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"} if json_mode else None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_base + "/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
            choices = body.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            parsed = _extract_json(content) if json_mode else None
            return LLMResponse(
                text=content, parsed=parsed, model=self.model,
                elapsed_ms=int((time.time() - t0) * 1000), error=None,
            )
        except urllib.error.HTTPError as e:
            return LLMResponse(text="", parsed=None, model=self.model,
                               elapsed_ms=int((time.time() - t0) * 1000),
                               error=f"HTTP {e.code}")
        except Exception as e:
            return LLMResponse(text="", parsed=None, model=self.model,
                               elapsed_ms=int((time.time() - t0) * 1000),
                               error="请求异常")


def load_llm_config(path: Optional[str] = None) -> dict:
    # 2026-08-24：冻结态 __file__ 回溯失败，改用 app_paths 定位 _MEIPASS/src/llm_config.json
    if not path:
        try:
            from app_paths import frozen_resource_dir
            path = frozen_resource_dir("src", "llm_config.json")
        except ImportError:
            path = os.path.join(os.path.dirname(__file__), "llm_config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def get_llm_client(config: Optional[dict] = None) -> LLMClient:
    config = config or load_llm_config()
    provider = config.get("provider") or os.environ.get("LLM_PROVIDER", "ollama")
    if provider == "mlx":
        return MLXClient(
            model_path=config.get("model") or os.environ.get("MLX_MODEL", ""),
            adapter_path=config.get("adapter_path"),
            max_tokens=config.get("max_tokens", 512),
        )
    if provider == "ollama":
        return OllamaClient(
            base_url=config.get("base_url") or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=config.get("model") or os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
            timeout=config.get("timeout", 120),
        )
    if provider in ("cloud", "openai", "vllm"):
        return CloudAPIClient(
            api_base=config.get("api_base") or os.environ.get("LLM_API_BASE", "http://localhost:8000/v1"),
            api_key=config.get("api_key") or os.environ.get("LLM_API_KEY", "not-needed"),
            model=config.get("model") or os.environ.get("LLM_MODEL", "qwen2.5:3b"),
            timeout=config.get("timeout", 120),
        )
    raise ValueError(f"unknown LLM provider: {provider}")
