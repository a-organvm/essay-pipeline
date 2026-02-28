"""Provider-agnostic LLM client for ORGAN-V essay-pipeline.

Supports multiple LLM providers via stdlib urllib.request (no external HTTP deps).
Follows the config pattern from analytics-engine: dataclass + from_env + configured.

Factory function create_client() reads LLM_PROVIDER env var, falling through to
the first configured provider in priority order:
    Anthropic → OpenAI → Gemini → Perplexity → Ollama

CLI smoke test:
    LLM_PROVIDER=anthropic python -c "
        from src.llm_client import create_client
        c = create_client()
        print(c.generate('You are a test.', 'Say hello.').text)
    "
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


PIPELINE_VERSION = "0.3.0"


@dataclass
class LLMResponse:
    """Structured response from an LLM provider."""

    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    """Protocol for LLM provider implementations."""

    @property
    def configured(self) -> bool: ...

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse: ...


def _http_post(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    """Make an HTTP POST request and return parsed JSON response."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class AnthropicClient:
    """Claude API client."""

    api_key: str = ""  # allow-secret
    model: str = "claude-sonnet-4-20250514"
    base_url: str = "https://api.anthropic.com/v1/messages"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "AnthropicClient":
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),  # allow-secret
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        )

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        result = _http_post(self.base_url, headers, body)

        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        usage = result.get("usage", {})
        return LLMResponse(
            text=text,
            model=result.get("model", self.model),
            provider="anthropic",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


@dataclass
class OpenAIClient:
    """OpenAI API client (also works with compatible endpoints)."""

    api_key: str = ""  # allow-secret
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "OpenAIClient":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),  # allow-secret
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        )

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        result = _http_post(self.base_url, headers, body)

        text = ""
        choices = result.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")

        usage = result.get("usage", {})
        return LLMResponse(
            text=text,
            model=result.get("model", self.model),
            provider="openai",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


@dataclass
class GeminiClient:
    """Google Gemini API client."""

    api_key: str = ""  # allow-secret
    model: str = "gemini-2.0-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "GeminiClient":
        return cls(
            api_key=os.environ.get("GEMINI_API_KEY", ""),  # allow-secret
            model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        )

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        result = _http_post(url, {}, body)

        text = ""
        candidates = result.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

        usage = result.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            model=self.model,
            provider="gemini",
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )


@dataclass
class PerplexityClient:
    """Perplexity API client (OpenAI-compatible endpoint)."""

    api_key: str = ""  # allow-secret
    model: str = "llama-3.1-sonar-large-128k-online"
    base_url: str = "https://api.perplexity.ai/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "PerplexityClient":
        return cls(
            api_key=os.environ.get("PERPLEXITY_API_KEY", ""),  # allow-secret
            model=os.environ.get("PERPLEXITY_MODEL", "llama-3.1-sonar-large-128k-online"),
        )

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        result = _http_post(self.base_url, headers, body)

        text = ""
        choices = result.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")

        usage = result.get("usage", {})
        return LLMResponse(
            text=text,
            model=result.get("model", self.model),
            provider="perplexity",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


@dataclass
class OllamaClient:
    """Ollama local inference client."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:70b"

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @classmethod
    def from_env(cls) -> "OllamaClient":
        return cls(
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "llama3.1:70b"),
        )

    def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        url = f"{self.base_url}/api/chat"
        body = {
            "model": self.model,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        result = _http_post(url, {}, body, timeout=300)

        text = result.get("message", {}).get("content", "")
        return LLMResponse(
            text=text,
            model=result.get("model", self.model),
            provider="ollama",
            input_tokens=result.get("prompt_eval_count", 0),
            output_tokens=result.get("eval_count", 0),
        )


# Provider registry: name → class
PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "gemini": GeminiClient,
    "perplexity": PerplexityClient,
    "ollama": OllamaClient,
}

# Priority order for auto-detection
PRIORITY_ORDER: list[str] = [
    "anthropic",
    "openai",
    "gemini",
    "perplexity",
    "ollama",
]


def create_client(provider: str | None = None) -> LLMClient:
    """Create an LLM client from environment configuration.

    If provider is specified, uses that provider directly.
    Otherwise reads LLM_PROVIDER env var, then falls through
    to the first configured provider in priority order.

    Raises:
        ValueError: If no provider is configured or provider name is unknown.
    """
    # Explicit provider
    name = provider or os.environ.get("LLM_PROVIDER", "")
    if name:
        name = name.lower().strip()
        if name not in PROVIDERS:
            raise ValueError(
                f"Unknown LLM provider: '{name}'. "
                f"Available: {', '.join(PROVIDERS)}"
            )
        client = PROVIDERS[name].from_env()
        if not client.configured:
            raise ValueError(
                f"LLM provider '{name}' is not configured — "
                f"check required environment variables"
            )
        return client

    # Auto-detect: try each in priority order
    for name in PRIORITY_ORDER:
        client = PROVIDERS[name].from_env()
        if client.configured:
            return client

    raise ValueError(
        "No LLM provider configured. Set LLM_PROVIDER and the corresponding "
        "API key environment variable. Supported: " + ", ".join(PROVIDERS)
    )
