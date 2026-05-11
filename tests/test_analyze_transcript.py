"""Tests for transcript analysis module."""

import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# Add the skill directory to sys.path so we can import the module
_SKILL_DIR = Path(__file__).resolve().parent.parent / "templates" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(_SKILL_DIR))

from analyze_transcript import (
    Message,
    Turn,
    TimingStats,
    extract_messages,
    group_into_turns,
    calculate_timing,
    format_markdown,
)


# --- Helpers ---


def make_jsonl_line(
    ts: str,
    role: str,
    content: str | list,
    *,
    source_tool_uuid: str | None = None,
    tool_use_result: dict | None = None,
) -> str:
    """Build a single JSONL line matching Claude Code transcript format."""
    record: dict = {
        "timestamp": ts,
        "message": {"role": role, "content": content},
    }
    if source_tool_uuid:
        record["sourceToolAssistantUUID"] = source_tool_uuid
    if tool_use_result:
        record["toolUseResult"] = tool_use_result
    return json.dumps(record)


def parse_lines(*lines: str) -> list[Message]:
    """Parse JSONL lines into Messages via extract_messages."""
    return extract_messages("\n".join(lines))


# --- extract_messages ---


class TestExtractMessages:
    def test_simple_string_content(self):
        lines = [
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "hello"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "Hi there how can I help"),
        ]
        msgs = parse_lines(*lines)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].is_human is True
        assert msgs[0].is_system is False
        assert msgs[0].word_count == 1
        assert msgs[1].role == "assistant"
        assert msgs[1].word_count == 6

    def test_array_content_with_tool_use(self):
        content = [
            {"type": "text", "text": "Let me check that"},
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        ]
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "assistant", content)]
        msgs = parse_lines(*lines)
        assert len(msgs) == 1
        assert msgs[0].tools == ["Read"]
        assert msgs[0].word_count == 4  # "Let me check that"

    def test_tool_result_not_counted_as_human(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "tool output here",
                source_tool_uuid="abc-123",
            ),
        ]
        msgs = parse_lines(*lines)
        assert len(msgs) == 1
        assert msgs[0].is_human is False
        assert msgs[0].is_tool_result is True

    def test_system_message_skill_injection(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "Base directory for this skill: /path/to/skill\nDo stuff",
            ),
        ]
        msgs = parse_lines(*lines)
        assert msgs[0].is_system is True
        assert msgs[0].word_count == 0

    def test_system_message_system_reminder(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "<system-reminder>Some injected context</system-reminder>",
            ),
        ]
        msgs = parse_lines(*lines)
        assert msgs[0].is_system is True
        assert msgs[0].word_count == 0

    def test_system_message_session_continuation(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion...",
            ),
        ]
        msgs = parse_lines(*lines)
        assert msgs[0].is_system is True
        assert msgs[0].word_count == 0

    def test_system_message_command_name(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "<command-name>retro</command-name>",
            ),
        ]
        msgs = parse_lines(*lines)
        assert msgs[0].is_system is True

    def test_system_message_local_command(self):
        lines = [
            make_jsonl_line(
                "2026-02-25T10:00:00.000Z",
                "user",
                "<local-command-caveat>some caveat</local-command-caveat>",
            ),
        ]
        msgs = parse_lines(*lines)
        assert msgs[0].is_system is True

    def test_thinking_blocks_excluded_from_word_count(self):
        content = [
            {"type": "thinking", "thinking": "hmm let me think about this carefully"},
            {"type": "text", "text": "Here is my answer"},
        ]
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "assistant", content)]
        msgs = parse_lines(*lines)
        assert msgs[0].word_count == 4  # only "Here is my answer"

    def test_error_detection_in_tool_result(self):
        content = [
            {"type": "tool_result", "is_error": True, "content": "Command failed"},
        ]
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "assistant", content)]
        msgs = parse_lines(*lines)
        assert msgs[0].has_error is True

    def test_skips_lines_without_timestamp_or_message(self):
        lines = [
            '{"type": "system", "data": "init"}',
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "real message"),
        ]
        msgs = parse_lines(*lines)
        assert len(msgs) == 1

    def test_human_preview_full_text_up_to_2000(self):
        text = "a " * 500  # 1000 chars, 500 words
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "user", text.strip())]
        msgs = parse_lines(*lines)
        assert msgs[0].preview == text.strip()

    def test_human_preview_truncated_over_2000(self):
        text = "a " * 1500  # 3000 chars
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "user", text.strip())]
        msgs = parse_lines(*lines)
        assert msgs[0].preview.endswith("[TRUNCATED]")
        assert len(msgs[0].preview) < 2100

    def test_assistant_preview_tools(self):
        content = [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        ]
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "assistant", content)]
        msgs = parse_lines(*lines)
        assert msgs[0].preview.startswith("tools: ")

    def test_assistant_preview_text_truncated_at_150(self):
        text = "word " * 100  # 500 chars
        lines = [make_jsonl_line("2026-02-25T10:00:00.000Z", "assistant", text.strip())]
        msgs = parse_lines(*lines)
        assert len(msgs[0].preview) <= 150


# --- group_into_turns ---


class TestGroupIntoTurns:
    def test_single_turn(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "hello"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "hi there friend"),
        )
        turns = group_into_turns(msgs)
        assert len(turns) == 1
        assert turns[0].user_words == 1
        assert turns[0].asst_words == 3

    def test_multiple_turns(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "first"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "response one"),
            make_jsonl_line("2026-02-25T10:01:00.000Z", "user", "second"),
            make_jsonl_line("2026-02-25T10:01:30.000Z", "assistant", "response two here"),
        )
        turns = group_into_turns(msgs)
        assert len(turns) == 2
        assert turns[0].user_words == 1
        assert turns[0].asst_words == 2
        assert turns[1].user_words == 1
        assert turns[1].asst_words == 3

    def test_system_messages_dont_create_turns(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "real question"),
            make_jsonl_line(
                "2026-02-25T10:00:10.000Z",
                "user",
                "Base directory for this skill: /foo",
            ),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "response to real question"),
        )
        turns = group_into_turns(msgs)
        assert len(turns) == 1
        assert turns[0].user_words == 2  # "real question"
        assert turns[0].asst_words == 4  # "response to real question"

    def test_tool_results_dont_create_turns(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "check this"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", "let me read"),
            make_jsonl_line(
                "2026-02-25T10:00:20.000Z",
                "user",
                "file contents here",
                source_tool_uuid="abc",
            ),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "I see the file"),
        )
        turns = group_into_turns(msgs)
        assert len(turns) == 1
        assert turns[0].asst_words == 7  # "let me read" + "I see the file"

    def test_tool_deduplication(self):
        content1 = [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        ]
        content2 = [
            {"type": "text", "text": "more checking"},
            {"type": "tool_use", "name": "Read", "id": "t2", "input": {}},
            {"type": "tool_use", "name": "Edit", "id": "t3", "input": {}},
        ]
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "do stuff"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", content1),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", content2),
        )
        turns = group_into_turns(msgs)
        assert len(turns) == 1
        # Read should appear once, Edit once
        assert "Read" in turns[0].tools
        assert "Edit" in turns[0].tools
        assert len(turns[0].tools) == 2

    def test_error_count(self):
        error_content = [
            {"type": "tool_result", "is_error": True, "content": "failed"},
        ]
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "try this"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", error_content),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", error_content),
        )
        turns = group_into_turns(msgs)
        assert turns[0].errors == 2

    def test_timestamps_captured(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "start"),
            make_jsonl_line("2026-02-25T10:05:00.000Z", "assistant", "end response"),
        )
        turns = group_into_turns(msgs)
        assert turns[0].timestamp == datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)

    def test_assistant_preview_captures_first_text(self):
        content1 = [
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        ]
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "do stuff"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", content1),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "actual text response"),
        )
        turns = group_into_turns(msgs)
        assert "actual text response" in turns[0].asst_preview


# --- calculate_timing ---


class TestCalculateTiming:
    def test_single_turn_timing(self):
        turns = [
            Turn(
                number=1,
                timestamp=datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc),
                user_text="hello world",
                user_words=2,
                asst_words=150,  # 1 min reading
                tools=[],
                errors=0,
                asst_preview="response",
            ),
        ]
        stats = calculate_timing(turns)
        assert stats.total_turns == 1
        assert stats.total_read_min == 1.0  # 150/150
        assert abs(stats.total_type_min - 2 / 60) < 0.01  # 2/60
        assert stats.total_buffer_min == 1.0
        # adjusted == raw for single turn
        expected_raw = 1.0 + 2 / 60 + 1.0
        assert abs(stats.raw_handson_min - expected_raw) < 0.01
        assert abs(stats.adjusted_handson_min - expected_raw) < 0.01

    def test_no_merge_distant_turns(self):
        """Two turns far apart should NOT be merged."""
        turns = [
            Turn(
                number=1,
                timestamp=datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc),
                user_text="first",
                user_words=1,
                asst_words=30,  # 0.2 min reading
                tools=[],
                errors=0,
                asst_preview="",
            ),
            Turn(
                number=2,
                timestamp=datetime(2026, 2, 25, 10, 30, 0, tzinfo=timezone.utc),
                user_text="second",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
        ]
        stats = calculate_timing(turns)
        # Each turn: read(0.2) + type(1/60) + buffer(1.0) = ~1.217
        # Not merged, so adjusted == raw
        assert abs(stats.adjusted_handson_min - stats.raw_handson_min) < 0.01
        assert stats.per_turn[0].merged is False
        assert stats.per_turn[1].merged is False

    def test_merge_close_turns(self):
        """Two turns within buffer overlap should be merged."""
        turns = [
            Turn(
                number=1,
                timestamp=datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc),
                user_text="first",
                user_words=1,
                asst_words=30,  # 0.2 min reading
                tools=[],
                errors=0,
                asst_preview="",
            ),
            Turn(
                number=2,
                # Turn 1 buffered time: 0.2 + 1/60 + 1.0 ≈ 1.217 min
                # So turn 2 at t+1min is within the buffered window
                timestamp=datetime(2026, 2, 25, 10, 1, 0, tzinfo=timezone.utc),
                user_text="second",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
        ]
        stats = calculate_timing(turns)
        # Merged: adjusted < raw because only 1 buffer for the group
        assert stats.adjusted_handson_min < stats.raw_handson_min
        assert stats.per_turn[1].merged is True

    def test_three_turn_chain_merge(self):
        """Three rapid turns should merge into one group with single buffer."""
        turns = [
            Turn(
                number=i + 1,
                timestamp=datetime(2026, 2, 25, 10, i, 0, tzinfo=timezone.utc),
                user_text=f"turn {i+1}",
                user_words=2,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            )
            for i in range(3)
        ]
        stats = calculate_timing(turns)
        # Raw: 3 turns * (0.2 + 2/60 + 1.0) = ~3.65
        # Adjusted: sum of raw (3 * 0.233) + 1 buffer = ~1.7
        assert stats.adjusted_handson_min < stats.raw_handson_min
        # Turn 2 and 3 should be marked merged
        assert stats.per_turn[0].merged is False  # group start
        assert stats.per_turn[1].merged is True
        assert stats.per_turn[2].merged is True

    def test_midnight_crossing_not_merged(self):
        """Timestamps crossing midnight should use correct datetime math.
        2 min gap > 1.217 min buffered time, so these should NOT merge."""
        turns = [
            Turn(
                number=1,
                timestamp=datetime(2026, 2, 25, 23, 59, 0, tzinfo=timezone.utc),
                user_text="late",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
            Turn(
                number=2,
                timestamp=datetime(2026, 2, 26, 0, 1, 0, tzinfo=timezone.utc),
                user_text="early",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
        ]
        stats = calculate_timing(turns)
        # 2 min gap > buffered time (~1.217), so not merged
        assert stats.per_turn[1].merged is False

    def test_midnight_crossing_merged(self):
        """Turns close together across midnight should merge correctly."""
        turns = [
            Turn(
                number=1,
                timestamp=datetime(2026, 2, 25, 23, 59, 30, tzinfo=timezone.utc),
                user_text="late",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
            Turn(
                number=2,
                timestamp=datetime(2026, 2, 26, 0, 0, 30, tzinfo=timezone.utc),
                user_text="early",
                user_words=1,
                asst_words=30,
                tools=[],
                errors=0,
                asst_preview="",
            ),
        ]
        stats = calculate_timing(turns)
        # 1 min gap < buffered time (~1.217), so merged
        assert stats.per_turn[1].merged is True


# --- format_markdown ---


class TestFormatMarkdown:
    def test_output_contains_header(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "hello"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "hi"),
        )
        turns = group_into_turns(msgs)
        stats = calculate_timing(turns)
        output = format_markdown(turns, stats)
        assert "# Transcript Analysis" in output
        assert "## Turns" in output
        assert "## Timing Stats" in output

    def test_output_contains_user_text(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "my specific question here"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "answer"),
        )
        turns = group_into_turns(msgs)
        stats = calculate_timing(turns)
        output = format_markdown(turns, stats)
        assert "my specific question here" in output

    def test_output_contains_timing_table(self):
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "test"),
            make_jsonl_line("2026-02-25T10:00:30.000Z", "assistant", "response here"),
        )
        turns = group_into_turns(msgs)
        stats = calculate_timing(turns)
        output = format_markdown(turns, stats)
        assert "| Turn |" in output
        assert "Raw hands-on:" in output
        assert "Adjusted hands-on:" in output

    def test_output_shows_tools(self):
        content = [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        ]
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "check file"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", content),
        )
        turns = group_into_turns(msgs)
        stats = calculate_timing(turns)
        output = format_markdown(turns, stats)
        assert "Read" in output

    def test_output_shows_errors(self):
        error_content = [
            {"type": "tool_result", "is_error": True, "content": "boom"},
        ]
        msgs = parse_lines(
            make_jsonl_line("2026-02-25T10:00:00.000Z", "user", "try"),
            make_jsonl_line("2026-02-25T10:00:15.000Z", "assistant", error_content),
        )
        turns = group_into_turns(msgs)
        stats = calculate_timing(turns)
        output = format_markdown(turns, stats)
        assert "ERRORS:" in output or "errors:" in output.lower()
