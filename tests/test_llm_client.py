"""Tests for the LLM client abstraction."""

import json
import os
from unittest.mock import MagicMock, patch

from src.llm_client import (
    AnthropicClient,
    GeminiClient,
    LLMResponse,
    OllamaClient,
    OpenAIClient,
    PerplexityClient,
    PRIORITY_ORDER,
    PROVIDERS,
    _http_post,
    create_client,
)


class TestLLMResponse:
    def test_dataclass_fields(self):
        r = LLMResponse(
            text="hello",
            model="test-model",
            provider="test",
            input_tokens=10,
            output_tokens=5,
        )
        assert r.text == "hello"
        assert r.model == "test-model"
        assert r.provider == "test"
        assert r.input_tokens == 10
        assert r.output_tokens == 5


class TestAnthropicClient:
    def test_configured_with_key(self):
        client = AnthropicClient(api_key="sk-test")  # allow-secret
        assert client.configured is True

    def test_not_configured_without_key(self):
        client = AnthropicClient()
        assert client.configured is False

    def test_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}):  # allow-secret
            client = AnthropicClient.from_env()
            assert client.api_key == "sk-test-123"  # allow-secret
            assert client.configured is True

    def test_from_env_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            client = AnthropicClient.from_env()
            assert client.configured is False

    def test_default_model(self):
        client = AnthropicClient()
        assert "claude" in client.model

    @patch("src.llm_client._http_post")
    def test_generate_parses_response(self, mock_post):
        mock_post.return_value = {
            "content": [{"type": "text", "text": "Hello world"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }
        client = AnthropicClient(api_key="sk-test")  # allow-secret
        response = client.generate("system", "user")
        assert response.text == "Hello world"
        assert response.provider == "anthropic"
        assert response.input_tokens == 20
        assert response.output_tokens == 5


class TestOpenAIClient:
    def test_configured_with_key(self):
        client = OpenAIClient(api_key="sk-test")  # allow-secret
        assert client.configured is True

    def test_not_configured_without_key(self):
        client = OpenAIClient()
        assert client.configured is False

    @patch("src.llm_client._http_post")
    def test_generate_parses_response(self, mock_post):
        mock_post.return_value = {
            "choices": [{"message": {"content": "Hi there"}}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 15, "completion_tokens": 3},
        }
        client = OpenAIClient(api_key="sk-test")  # allow-secret
        response = client.generate("system", "user")
        assert response.text == "Hi there"
        assert response.provider == "openai"
        assert response.input_tokens == 15


class TestGeminiClient:
    def test_configured_with_key(self):
        client = GeminiClient(api_key="AIza-test")  # allow-secret
        assert client.configured is True

    @patch("src.llm_client._http_post")
    def test_generate_parses_response(self, mock_post):
        mock_post.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "Gemini says hello"}]}}
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8},
        }
        client = GeminiClient(api_key="AIza-test")  # allow-secret
        response = client.generate("system", "user")
        assert response.text == "Gemini says hello"
        assert response.provider == "gemini"
        assert response.input_tokens == 10


class TestPerplexityClient:
    def test_configured_with_key(self):
        client = PerplexityClient(api_key="pplx-test")  # allow-secret
        assert client.configured is True

    @patch("src.llm_client._http_post")
    def test_generate_parses_response(self, mock_post):
        mock_post.return_value = {
            "choices": [{"message": {"content": "Research result"}}],
            "model": "llama-3.1-sonar-large-128k-online",
            "usage": {"prompt_tokens": 25, "completion_tokens": 12},
        }
        client = PerplexityClient(api_key="pplx-test")  # allow-secret
        response = client.generate("system", "user")
        assert response.text == "Research result"
        assert response.provider == "perplexity"


class TestOllamaClient:
    def test_always_configured_with_default_url(self):
        client = OllamaClient()
        assert client.configured is True

    def test_not_configured_with_empty_url(self):
        client = OllamaClient(base_url="")
        assert client.configured is False

    @patch("src.llm_client._http_post")
    def test_generate_parses_response(self, mock_post):
        mock_post.return_value = {
            "message": {"content": "Local model output"},
            "model": "llama3.1:70b",
            "prompt_eval_count": 30,
            "eval_count": 15,
        }
        client = OllamaClient()
        response = client.generate("system", "user")
        assert response.text == "Local model output"
        assert response.provider == "ollama"
        assert response.input_tokens == 30
        assert response.output_tokens == 15


class TestCreateClient:
    def test_explicit_provider(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            client = create_client("anthropic")
            assert isinstance(client, AnthropicClient)

    def test_explicit_provider_case_insensitive(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            client = create_client("OpenAI")
            assert isinstance(client, OpenAIClient)

    def test_unknown_provider_raises(self):
        try:
            create_client("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown LLM provider" in str(e)

    def test_unconfigured_explicit_provider_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            try:
                create_client("anthropic")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "not configured" in str(e)

    def test_env_var_provider(self):
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
        }):
            client = create_client()
            assert isinstance(client, OpenAIClient)

    def test_auto_detect_priority(self):
        # Only Gemini configured → should pick Gemini
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-test"}, clear=True):
            client = create_client()
            assert isinstance(client, GeminiClient)

    def test_auto_detect_prefers_anthropic(self):
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk-oai",
        }, clear=True):
            client = create_client()
            assert isinstance(client, AnthropicClient)

    def test_no_provider_configured_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ollama is always "configured" with default URL, so we
            # need to also disable that
            with patch.object(OllamaClient, "configured", new_callable=lambda: property(lambda self: False)):
                try:
                    create_client()
                    assert False, "Should have raised ValueError"
                except ValueError as e:
                    assert "No LLM provider configured" in str(e)


class TestProviderRegistry:
    def test_all_priority_providers_in_registry(self):
        for name in PRIORITY_ORDER:
            assert name in PROVIDERS

    def test_all_providers_have_from_env(self):
        for name, cls in PROVIDERS.items():
            assert hasattr(cls, "from_env"), f"{name} missing from_env"

    def test_all_providers_have_configured(self):
        for name, cls in PROVIDERS.items():
            instance = cls()
            # Should not raise
            _ = instance.configured
