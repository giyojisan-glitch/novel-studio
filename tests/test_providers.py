"""LLM Provider tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from novel_studio.llm import (
    BaseProvider,
    HumanQueueProvider,
    StubProvider,
    AnthropicProvider,
    get_provider,
)


# ---------- HumanQueueProvider ----------


class TestHumanQueueProvider:
    def test_request_writes_prompt_file(self, tmp_path):
        (tmp_path / "queue").mkdir()
        (tmp_path / "responses").mkdir()
        p = HumanQueueProvider()
        p.request("L1", "prompt body", tmp_path)
        assert (tmp_path / "queue" / "L1.prompt.md").exists()

    def test_query_no_response_yet(self, tmp_path):
        (tmp_path / "queue").mkdir()
        (tmp_path / "responses").mkdir()
        p = HumanQueueProvider()
        result = p.query("L1", tmp_path)
        assert result.ready is False

    def test_query_with_response(self, tmp_path):
        (tmp_path / "queue").mkdir()
        (tmp_path / "responses").mkdir()
        resp = tmp_path / "responses" / "L1.response.json"
        resp.write_text('{"key": "value"}', encoding="utf-8")
        p = HumanQueueProvider()
        result = p.query("L1", tmp_path)
        assert result.ready
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_reset_removes_files(self, tmp_path):
        (tmp_path / "queue").mkdir()
        (tmp_path / "responses").mkdir()
        (tmp_path / "queue" / "L1.prompt.md").write_text("p")
        (tmp_path / "responses" / "L1.response.json").write_text('{"k":1}')
        p = HumanQueueProvider()
        p.reset("L1", tmp_path)
        assert not (tmp_path / "queue" / "L1.prompt.md").exists()
        assert not (tmp_path / "responses" / "L1.response.json").exists()

    def test_query_bad_json_returns_error(self, tmp_path):
        (tmp_path / "responses").mkdir()
        (tmp_path / "responses" / "L1.response.json").write_text("{not valid json}")
        p = HumanQueueProvider()
        result = p.query("L1", tmp_path)
        assert result.ready is True
        assert result.error is not None


# ---------- StubProvider ----------


class TestStubProvider:
    def test_l1_stub_returns_schema_valid(self):
        p = StubProvider()
        p.request("L1", "", Path("/tmp"))
        result = p.query("L1", Path("/tmp"))
        assert result.ready
        assert "title" in result.data
        assert "protagonist" in result.data
        assert "three_act" in result.data

    def test_l2_stub_has_correct_index(self):
        p = StubProvider()
        p.request("L2_2", "", Path("/tmp"))
        result = p.query("L2_2", Path("/tmp"))
        assert result.data["index"] == 2

    def test_l3_stub_has_content_and_word_count(self):
        p = StubProvider()
        p.request("L3_1", "", Path("/tmp"))
        result = p.query("L3_1", Path("/tmp"))
        assert "content" in result.data
        assert result.data["word_count"] > 0

    def test_audit_logic_stub_passes(self):
        p = StubProvider()
        p.request("L2_1_audit_logic", "", Path("/tmp"))
        result = p.query("L2_1_audit_logic", Path("/tmp"))
        assert result.data["head"] == "logic"
        assert result.data["passed"] is True

    def test_audit_pace_stub_passes(self):
        p = StubProvider()
        p.request("L2_1_audit_pace", "", Path("/tmp"))
        result = p.query("L2_1_audit_pace", Path("/tmp"))
        assert result.data["head"] == "pace"

    def test_final_audit_stub_usable(self):
        p = StubProvider()
        p.request("final_audit", "", Path("/tmp"))
        result = p.query("final_audit", Path("/tmp"))
        assert result.data["usable"] is True
        assert result.data["suspect_layer"] == "none"

    def test_adversarial_stub_is_a_list(self):
        p = StubProvider()
        p.request("L4_adversarial_1", "", Path("/tmp"))
        result = p.query("L4_adversarial_1", Path("/tmp"))
        assert isinstance(result.data, list)
        assert all("category" in c for c in result.data)

    def test_scrubber_stub_has_content(self):
        p = StubProvider()
        p.request("L4_scrubber_2", "", Path("/tmp"))
        result = p.query("L4_scrubber_2", Path("/tmp"))
        assert "content" in result.data
        assert result.data["index"] == 2

    def test_query_without_request_not_ready(self):
        p = StubProvider()
        result = p.query("L1", Path("/tmp"))
        assert result.ready is False

    def test_overrides_apply(self):
        custom = {"title": "override", "logline": "x", "theme": "y"}
        p = StubProvider(overrides={"L1": custom})
        p.request("L1", "", Path("/tmp"))
        result = p.query("L1", Path("/tmp"))
        assert result.data == custom


# ---------- AnthropicProvider (real implementation with mocked API) ----------


class _MockTextBlock:
    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _MockMessage:
    def __init__(self, text: str):
        self.content = [_MockTextBlock(text)]


class _MockAnthropicClient:
    """Counts calls, returns scripted responses in order."""
    def __init__(self, responses: list[str | Exception]):
        self.responses = list(responses)
        self.call_count = 0
        self.messages = self  # so .messages.create works

    def create(self, **kwargs):
        self.call_count += 1
        if not self.responses:
            raise RuntimeError("No more mock responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return _MockMessage(r)


class TestAnthropicProvider:
    """Mock anthropic client. Never hits real API."""

    def _setup_pdir(self, tmp_path):
        (tmp_path / "queue").mkdir()
        (tmp_path / "responses").mkdir()
        return tmp_path

    def _make_provider(self, tmp_path, mock_responses, monkeypatch=None):
        """Create provider with pre-injected mock client."""
        p = AnthropicProvider(api_key="fake-key", max_api_retries=3, max_json_retries=2)
        p._client = _MockAnthropicClient(mock_responses)
        return p

    # --- Instantiation ---

    def test_instantiates_without_key(self):
        p = AnthropicProvider()
        assert p.name == "anthropic"

    def test_default_model(self):
        p = AnthropicProvider()
        assert "claude" in p.model.lower()

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("NOVEL_STUDIO_MODEL", "claude-opus-4-0")
        p = AnthropicProvider()
        assert p.model == "claude-opus-4-0"

    def test_client_property_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        p = AnthropicProvider(api_key=None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            _ = p.client

    # --- JSON parsing ---

    def test_parse_json_plain(self):
        assert AnthropicProvider._parse_json('{"a": 1}') == {"a": 1}

    def test_parse_json_strips_markdown_fence(self):
        wrapped = '```json\n{"a": 1}\n```'
        assert AnthropicProvider._parse_json(wrapped) == {"a": 1}

    def test_parse_json_strips_plain_fence(self):
        wrapped = "```\n[1, 2, 3]\n```"
        assert AnthropicProvider._parse_json(wrapped) == [1, 2, 3]

    def test_parse_json_handles_whitespace(self):
        assert AnthropicProvider._parse_json('   {"k":"v"}   ') == {"k": "v"}

    def test_parse_json_list_response(self):
        """L4_adversarial returns a list."""
        text = '[{"category": "FAT", "quoted_text": "x", "reason": "y"}]'
        result = AnthropicProvider._parse_json(text)
        assert isinstance(result, list)
        assert result[0]["category"] == "FAT"

    # --- request + query end-to-end with mock ---

    def test_request_writes_response_file(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        p = self._make_provider(pdir, ['{"title": "T"}'])
        p.request("L1", "prompt", pdir)
        resp_file = pdir / "responses" / "L1.response.json"
        assert resp_file.exists()
        import json
        assert json.loads(resp_file.read_text()) == {"title": "T"}

    def test_request_also_writes_queue_file(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        p = self._make_provider(pdir, ['{"k": 1}'])
        p.request("L1", "the prompt body", pdir)
        queue_file = pdir / "queue" / "L1.prompt.md"
        assert queue_file.exists()
        assert "the prompt body" in queue_file.read_text()

    def test_query_after_request_returns_data(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        p = self._make_provider(pdir, ['{"title": "T", "logline": "l"}'])
        p.request("L1", "prompt", pdir)
        result = p.query("L1", pdir)
        assert result.ready
        assert result.data == {"title": "T", "logline": "l"}

    def test_resume_skips_existing_response(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        # Pre-populate response file
        (pdir / "responses" / "L1.response.json").write_text('{"pre": "existing"}')
        p = self._make_provider(pdir, [])  # no responses queued — should NOT call API
        p.request("L1", "prompt", pdir)
        assert p._client.call_count == 0

    # --- JSON retry logic ---

    def test_malformed_json_retries_with_hint(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        # First response is bad, second is good
        p = self._make_provider(pdir, ["not json at all", '{"ok": true}'])
        p.request("L1", "prompt", pdir)
        assert p._client.call_count == 2
        import json
        data = json.loads((pdir / "responses" / "L1.response.json").read_text())
        assert data == {"ok": True}

    def test_json_retries_exhausted_raises(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        # All bad — should raise after max_json_retries + 1 attempts
        p = self._make_provider(pdir, ["bad", "also bad", "still bad", "never valid"])
        with pytest.raises(RuntimeError, match="JSON 解析重试"):
            p.request("L1", "prompt", pdir)

    # --- API error retry logic ---

    def test_api_error_retries_with_backoff(self, tmp_path, monkeypatch):
        import anthropic
        pdir = self._setup_pdir(tmp_path)
        # Speed up: no real sleep
        monkeypatch.setattr("novel_studio.llm.anthropic.time.sleep", lambda s: None)
        # First call raises, second succeeds
        err = anthropic.APIConnectionError(request=None)
        p = self._make_provider(pdir, [err, '{"ok": true}'])
        p.request("L1", "prompt", pdir)
        assert p._client.call_count == 2

    def test_api_errors_exhausted_raises(self, tmp_path, monkeypatch):
        import anthropic
        pdir = self._setup_pdir(tmp_path)
        monkeypatch.setattr("novel_studio.llm.anthropic.time.sleep", lambda s: None)
        err = anthropic.APIConnectionError(request=None)
        p = self._make_provider(pdir, [err, err, err])
        with pytest.raises(RuntimeError, match="API 调用重试"):
            p.request("L1", "prompt", pdir)

    # --- Reset ---

    def test_reset_removes_files(self, tmp_path):
        pdir = self._setup_pdir(tmp_path)
        (pdir / "queue" / "L1.prompt.md").write_text("p")
        (pdir / "responses" / "L1.response.json").write_text('{"k":1}')
        p = AnthropicProvider(api_key="fake")
        p.reset("L1", pdir)
        assert not (pdir / "queue" / "L1.prompt.md").exists()
        assert not (pdir / "responses" / "L1.response.json").exists()


# ---------- Factory ----------


class TestGetProvider:
    def test_default_is_human_queue(self, monkeypatch):
        monkeypatch.delenv("NOVEL_STUDIO_PROVIDER", raising=False)
        assert get_provider().name == "human_queue"

    def test_stub_by_name(self):
        assert get_provider("stub").name == "stub"

    def test_anthropic_by_name(self):
        assert get_provider("anthropic").name == "anthropic"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("NOVEL_STUDIO_PROVIDER", "stub")
        assert get_provider().name == "stub"

    def test_explicit_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("NOVEL_STUDIO_PROVIDER", "stub")
        assert get_provider("human_queue").name == "human_queue"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="未知 provider"):
            get_provider("nonexistent")
