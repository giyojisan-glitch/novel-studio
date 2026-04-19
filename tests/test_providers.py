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


# ---------- AnthropicProvider skeleton ----------


class TestAnthropicProviderSkeleton:
    def test_instantiates_without_key(self):
        p = AnthropicProvider()
        assert p.name == "anthropic"

    def test_request_raises_not_implemented(self, tmp_path):
        p = AnthropicProvider(api_key=None)  # 明确没 key
        with pytest.raises(NotImplementedError):
            p.request("L1", "prompt", tmp_path)

    def test_query_without_cache_not_ready(self, tmp_path):
        p = AnthropicProvider(api_key="fake")
        result = p.query("L1", tmp_path)
        assert result.ready is False


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
