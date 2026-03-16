"""Unit tests for prioritize.py"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

# Add scripts dir to path so we can import prioritize
sys.path.insert(0, str(Path(__file__).parent.parent))
import prioritize
from prioritize import (
    MAX_COMFORTABLE_COMPARISONS,
    HeadToHeadResults,
    SavedRanking,
    Todo,
    WinCounts,
    apply_ranking_order_to_habitica,
    build_api_headers,
    compute_max_items_for_comparisons,
    deserialize_head_to_head,
    display_comparison_progress,
    display_ranking,
    fetch_all_tags,
    fetch_incomplete_todos,
    find_tag_ids_by_name,
    generate_comparison_labels,
    load_saved_ranking,
    prompt_user_for_choice,
    prompt_user_for_tag_filter,
    rank_todos_by_win_count,
    run_full_pairwise_comparison,
    run_new_versus_existing_comparison,
    save_ranking,
    serialize_head_to_head,
    warn_and_maybe_limit_for_full_pairwise,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_todo(id: str, text: str) -> Todo:
    return {"id": id, "text": text, "tags": [], "completed": False}


@pytest.fixture()
def sample_todos() -> list[Todo]:
    return [
        make_todo("a", "Task A"),
        make_todo("b", "Task B"),
        make_todo("c", "Task C"),
    ]


# ---------------------------------------------------------------------------
# generate_comparison_labels()
# ---------------------------------------------------------------------------


class TestGenerateComparisonLabels:
    def test_single_item(self) -> None:
        assert generate_comparison_labels(1) == ["A"]

    def test_three_items(self) -> None:
        assert generate_comparison_labels(3) == ["A", "B", "C"]

    def test_caps_at_26(self) -> None:
        assert len(generate_comparison_labels(30)) == 26

    def test_zero_items(self) -> None:
        assert generate_comparison_labels(0) == []

    def test_exactly_26(self) -> None:
        labels = generate_comparison_labels(26)
        assert labels[0] == "A"
        assert labels[-1] == "Z"


# ---------------------------------------------------------------------------
# serialize_head_to_head() / deserialize_head_to_head()
# ---------------------------------------------------------------------------


class TestHeadToHeadSerialization:
    def test_serialize_produces_string_keys(self) -> None:
        head_to_head_results: HeadToHeadResults = {("a", "b"): "a"}
        serialized = serialize_head_to_head(head_to_head_results)
        assert "a|||b" in serialized
        assert serialized["a|||b"] == "a"

    def test_deserialize_restores_tuple_keys(self) -> None:
        serialized_head_to_head = {"a|||b": "a", "b|||c": "b"}
        result = deserialize_head_to_head(serialized_head_to_head)
        assert result[("a", "b")] == "a"
        assert result[("b", "c")] == "b"

    def test_round_trip(self) -> None:
        head_to_head_results: HeadToHeadResults = {
            ("a", "b"): "a",
            ("a", "c"): "a",
            ("b", "c"): "b",
        }
        assert (
            deserialize_head_to_head(serialize_head_to_head(head_to_head_results))
            == head_to_head_results
        )

    def test_empty_results(self) -> None:
        assert serialize_head_to_head({}) == {}
        assert deserialize_head_to_head({}) == {}


# ---------------------------------------------------------------------------
# find_tag_ids_by_name()
# ---------------------------------------------------------------------------


class TestFindTagIdsByName:
    AVAILABLE_TAGS = {
        "Work": "id-work",
        "Personal": "id-personal",
        "Urgent": "id-urgent",
    }

    def test_resolves_single_tag(self) -> None:
        result = find_tag_ids_by_name(["Work"], self.AVAILABLE_TAGS)
        assert result == ["id-work"]

    def test_resolves_multiple_tags(self) -> None:
        result = find_tag_ids_by_name(["Work", "Urgent"], self.AVAILABLE_TAGS)
        assert result == ["id-work", "id-urgent"]

    def test_case_insensitive(self) -> None:
        result = find_tag_ids_by_name(["work"], self.AVAILABLE_TAGS)
        assert result == ["id-work"]

    def test_empty_list_returns_empty(self) -> None:
        result = find_tag_ids_by_name([], self.AVAILABLE_TAGS)
        assert result == []

    def test_unknown_tag_exits(self) -> None:
        with pytest.raises(SystemExit):
            find_tag_ids_by_name(["Nonexistent"], self.AVAILABLE_TAGS)


# ---------------------------------------------------------------------------
# prompt_user_for_tag_filter()
# ---------------------------------------------------------------------------


class TestPromptUserForTagFilter:
    AVAILABLE_TAGS = {"Work": "id-work", "Personal": "id-personal"}

    def test_empty_input_returns_no_filter(self) -> None:
        with patch("builtins.input", return_value=""):
            result = prompt_user_for_tag_filter(self.AVAILABLE_TAGS)
        assert result == []

    def test_single_tag(self) -> None:
        with patch("builtins.input", return_value="Work"):
            result = prompt_user_for_tag_filter(self.AVAILABLE_TAGS)
        assert result == ["Work"]

    def test_multiple_tags_comma_separated(self) -> None:
        with patch("builtins.input", return_value="Work, Personal"):
            result = prompt_user_for_tag_filter(self.AVAILABLE_TAGS)
        assert result == ["Work", "Personal"]

    def test_strips_whitespace_around_tags(self) -> None:
        with patch("builtins.input", return_value="  Work ,  Personal  "):
            result = prompt_user_for_tag_filter(self.AVAILABLE_TAGS)
        assert result == ["Work", "Personal"]


# ---------------------------------------------------------------------------
# prompt_user_for_choice()
# ---------------------------------------------------------------------------


class TestPromptUserForChoice:
    def test_returns_valid_choice(self) -> None:
        with patch("builtins.input", return_value="a"):
            result = prompt_user_for_choice("Pick: ", {"A", "B"})
        assert result == "A"

    def test_case_insensitive(self) -> None:
        with patch("builtins.input", return_value="b"):
            result = prompt_user_for_choice("Pick: ", {"A", "B"})
        assert result == "B"

    def test_retries_on_invalid_then_accepts(self) -> None:
        with patch("builtins.input", side_effect=["x", "y", "A"]):
            result = prompt_user_for_choice("Pick: ", {"A", "B"})
        assert result == "A"

    def test_strips_whitespace(self) -> None:
        with patch("builtins.input", return_value="  B  "):
            result = prompt_user_for_choice("Pick: ", {"A", "B"})
        assert result == "B"


# ---------------------------------------------------------------------------
# rank_todos_by_win_count()
# ---------------------------------------------------------------------------


class TestRankTodosByWinCount:
    def test_clear_winner(self, sample_todos: list[Todo]) -> None:
        win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}
        head_to_head_results: HeadToHeadResults = {
            ("a", "b"): "a",
            ("a", "c"): "a",
            ("b", "c"): "b",
        }
        ranked_todos = rank_todos_by_win_count(
            sample_todos, win_counts, head_to_head_results
        )
        assert [todo["id"] for todo in ranked_todos] == ["a", "b", "c"]

    def test_reverse_wins(self, sample_todos: list[Todo]) -> None:
        win_counts: WinCounts = {"a": 0, "b": 1, "c": 2}
        head_to_head_results: HeadToHeadResults = {
            ("a", "b"): "b",
            ("a", "c"): "c",
            ("b", "c"): "c",
        }
        ranked_todos = rank_todos_by_win_count(
            sample_todos, win_counts, head_to_head_results
        )
        assert [todo["id"] for todo in ranked_todos] == ["c", "b", "a"]

    def test_tie_broken_by_head_to_head(self, sample_todos: list[Todo]) -> None:
        # a and b both have 1 win; b beat a directly
        win_counts: WinCounts = {"a": 1, "b": 1, "c": 0}
        head_to_head_results: HeadToHeadResults = {
            ("a", "b"): "b",
            ("a", "c"): "a",
            ("b", "c"): "b",
        }
        ranked_todos = rank_todos_by_win_count(
            sample_todos, win_counts, head_to_head_results
        )
        assert ranked_todos[0]["id"] == "b"
        assert ranked_todos[1]["id"] == "a"

    def test_tie_broken_by_reversed_head_to_head_key(
        self, sample_todos: list[Todo]
    ) -> None:
        # head-to-head stored with reversed key order — should still resolve correctly
        win_counts: WinCounts = {"a": 1, "b": 1, "c": 0}
        head_to_head_results: HeadToHeadResults = {
            ("b", "a"): "b",  # reversed key
            ("a", "c"): "a",
            ("b", "c"): "b",
        }
        ranked_todos = rank_todos_by_win_count(
            sample_todos, win_counts, head_to_head_results
        )
        assert ranked_todos[0]["id"] == "b"

    def test_missing_win_counts_default_to_zero(self) -> None:
        todos = [make_todo("x", "X"), make_todo("y", "Y")]
        ranked_todos = rank_todos_by_win_count(todos, {}, {})
        assert len(ranked_todos) == 2

    def test_single_item(self) -> None:
        todos = [make_todo("only", "Only task")]
        ranked_todos = rank_todos_by_win_count(todos, {"only": 0}, {})
        assert ranked_todos[0]["id"] == "only"


# ---------------------------------------------------------------------------
# run_full_pairwise_comparison()
# ---------------------------------------------------------------------------


class TestRunFullPairwiseComparison:
    def test_first_item_wins_most_comparisons(self, sample_todos: list[Todo]) -> None:
        # Pairs for 3 items: (a,b), (a,c), (b,c) — pick 1 (first) each time
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            win_counts, head_to_head_results = run_full_pairwise_comparison(
                sample_todos
            )

        assert win_counts["a"] == 2
        assert win_counts["b"] == 1
        assert win_counts["c"] == 0

    def test_correct_number_of_comparisons(self) -> None:
        # 4 items: 6 pairs — pick 1 (first) each time
        todos = [make_todo(str(index), f"Task {index}") for index in range(4)]
        user_choices = ["1", "1", "1", "1", "1", "1"]  # first of each pair wins

        with patch("builtins.input", side_effect=user_choices):
            win_counts, head_to_head_results = run_full_pairwise_comparison(todos)

        assert len(head_to_head_results) == 6  # C(4,2) = 6

    def test_last_item_wins_all(self, sample_todos: list[Todo]) -> None:
        # Pairs: (a,b) → 2=b, (a,c) → 2=c, (b,c) → 2=c
        with patch("builtins.input", side_effect=["2", "2", "2"]):
            win_counts, head_to_head_results = run_full_pairwise_comparison(
                sample_todos
            )

        assert win_counts["a"] == 0
        assert win_counts["b"] == 1
        assert win_counts["c"] == 2

    def test_head_to_head_records_winner(self, sample_todos: list[Todo]) -> None:
        # (a,b) → 1=a, (a,c) → 1=a, (b,c) → 1=b
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            win_counts, head_to_head_results = run_full_pairwise_comparison(
                sample_todos
            )

        assert head_to_head_results[("a", "b")] == "a"
        assert head_to_head_results[("a", "c")] == "a"
        assert head_to_head_results[("b", "c")] == "b"

    def test_skips_already_answered_pairs(self, sample_todos: list[Todo]) -> None:
        # (a,b) already answered — only (a,c) and (b,c) remain
        existing_h2h: HeadToHeadResults = {("a", "b"): "a"}
        existing_wins: WinCounts = {"a": 1, "b": 0, "c": 0}
        with patch("builtins.input", side_effect=["1", "1"]):  # only 2 battles
            win_counts, h2h = run_full_pairwise_comparison(
                sample_todos, existing_wins, existing_h2h
            )
        assert len(h2h) == 3
        assert h2h[("a", "b")] == "a"  # preserved from existing

    def test_skipped_count_shown_in_battle_header(self, sample_todos: list[Todo], capsys: Any) -> None:
        existing_h2h: HeadToHeadResults = {("a", "b"): "a"}
        existing_wins: WinCounts = {"a": 1, "b": 0, "c": 0}
        with patch("builtins.input", side_effect=["1", "1"]):
            run_full_pairwise_comparison(sample_todos, existing_wins, existing_h2h)
        output = capsys.readouterr().out
        assert "(1 skipped)" in output

    def test_no_skipped_suffix_when_none_skipped(self, sample_todos: list[Todo], capsys: Any) -> None:
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            run_full_pairwise_comparison(sample_todos)
        output = capsys.readouterr().out
        assert "skipped" not in output

    def test_returns_immediately_when_all_pairs_answered(self, sample_todos: list[Todo]) -> None:
        existing_h2h: HeadToHeadResults = {
            ("a", "b"): "a", ("a", "c"): "a", ("b", "c"): "b"
        }
        existing_wins: WinCounts = {"a": 2, "b": 1, "c": 0}
        with patch("builtins.input", side_effect=[]) as mock_input:
            win_counts, h2h = run_full_pairwise_comparison(
                sample_todos, existing_wins, existing_h2h
            )
        mock_input.assert_not_called()
        assert win_counts == existing_wins

    def test_save_callback_called_after_each_answer(self, sample_todos: list[Todo]) -> None:
        callback = MagicMock()
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            run_full_pairwise_comparison(sample_todos, save_callback=callback)
        assert callback.call_count == 3

    def test_save_callback_receives_updated_state(self, sample_todos: list[Todo]) -> None:
        saved_states: list[WinCounts] = []
        def callback(wc: WinCounts, h2h: HeadToHeadResults) -> None:
            saved_states.append(dict(wc))
        with patch("builtins.input", side_effect=["1", "1", "1"]):
            run_full_pairwise_comparison(sample_todos, save_callback=callback)
        assert saved_states[0]["a"] == 1  # a won battle 1
        assert saved_states[1]["a"] == 2  # a won battle 2
        assert saved_states[2]["b"] == 1  # b won battle 3


# ---------------------------------------------------------------------------
# run_new_versus_existing_comparison()
# ---------------------------------------------------------------------------


class TestRunNewVersusExistingComparison:
    def test_single_new_todo_wins_all_comparisons(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        previous_win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}

        with patch("builtins.input", return_value="1"):
            win_counts, _ = run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_win_counts, {}
            )

        assert win_counts["new"] == 3
        assert win_counts["a"] == 2  # existing wins unchanged
        assert win_counts["b"] == 1  # existing wins unchanged

    def test_single_new_todo_loses_all_comparisons(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        previous_win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}

        with patch("builtins.input", return_value="2"):
            win_counts, _ = run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_win_counts, {}
            )

        assert win_counts["new"] == 0
        assert win_counts["a"] == 3
        assert win_counts["b"] == 2
        assert win_counts["c"] == 1

    def test_multiple_new_todos_not_compared_against_each_other(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new1", "New Task 1"), make_todo("new2", "New Task 2")]
        previous_win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}

        with patch("builtins.input", return_value="1"):
            win_counts, head_to_head_results = run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_win_counts, {}
            )

        assert win_counts["new1"] == 3
        assert win_counts["new2"] == 3
        # new items are NOT compared against each other
        assert ("new1", "new2") not in head_to_head_results
        assert ("new2", "new1") not in head_to_head_results

    def test_correct_number_of_comparisons_new_times_existing(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new1", "New Task 1"), make_todo("new2", "New Task 2")]
        previous_win_counts: WinCounts = {"a": 0, "b": 0, "c": 0}

        with patch("builtins.input", return_value="1"):
            _, head_to_head_results = run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_win_counts, {}
            )

        # 2 new × 3 existing = 6 comparisons
        assert len(head_to_head_results) == 6

    def test_head_to_head_entries_created_for_all_new_vs_existing_pairs(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]

        with patch("builtins.input", return_value="1"):
            _, head_to_head_results = run_new_versus_existing_comparison(
                new_todos, sample_todos, {"a": 0, "b": 0, "c": 0}, {}
            )

        assert ("new", "a") in head_to_head_results
        assert ("new", "b") in head_to_head_results
        assert ("new", "c") in head_to_head_results

    def test_does_not_mutate_previous_win_counts(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        previous_win_counts: WinCounts = {"a": 1, "b": 0, "c": 0}
        original_win_counts = dict(previous_win_counts)

        with patch("builtins.input", return_value="1"):
            run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_win_counts, {}
            )

        assert previous_win_counts == original_win_counts

    def test_skips_already_answered_new_vs_existing_pairs(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        previous_wins: WinCounts = {"a": 2, "b": 1, "c": 0, "new": 0}
        existing_h2h: HeadToHeadResults = {("new", "a"): "new"}  # already answered
        with patch("builtins.input", side_effect=["1", "1"]):  # only 2 remaining
            win_counts, h2h = run_new_versus_existing_comparison(
                new_todos, sample_todos, previous_wins, existing_h2h
            )
        assert len([k for k in h2h if "new" in k[0]]) == 3
        assert h2h[("new", "a")] == "new"  # preserved

    def test_skipped_count_shown_in_new_vs_existing_battle_header(
        self, sample_todos: list[Todo], capsys: Any
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        existing_h2h: HeadToHeadResults = {("new", "a"): "new"}
        with patch("builtins.input", side_effect=["1", "1"]):
            run_new_versus_existing_comparison(
                new_todos, sample_todos, {"a": 1, "b": 0, "c": 0, "new": 1}, existing_h2h
            )
        output = capsys.readouterr().out
        assert "(1 skipped)" in output

    def test_save_callback_called_after_each_new_vs_existing_answer(
        self, sample_todos: list[Todo]
    ) -> None:
        new_todos = [make_todo("new", "New Task")]
        callback = MagicMock()
        with patch("builtins.input", return_value="1"):
            run_new_versus_existing_comparison(
                new_todos, sample_todos, {"a": 0, "b": 0, "c": 0}, {}, callback
            )
        assert callback.call_count == 3  # 1 new × 3 existing


# ---------------------------------------------------------------------------
# load_saved_ranking() / save_ranking()
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = load_saved_ranking(tmp_path / "nonexistent.json")
        assert result is None

    def test_save_and_load_round_trip(
        self, tmp_path: Path, sample_todos: list[Todo]
    ) -> None:
        ranking_file = tmp_path / "ranking.json"
        win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}
        head_to_head_results: HeadToHeadResults = {
            ("a", "b"): "a",
            ("a", "c"): "a",
            ("b", "c"): "b",
        }
        ranked_todos = rank_todos_by_win_count(
            sample_todos, win_counts, head_to_head_results
        )

        save_ranking(
            ["Work"],
            sample_todos,
            win_counts,
            head_to_head_results,
            ranked_todos,
            ranking_file=ranking_file,
        )
        loaded_ranking = load_saved_ranking(ranking_file)

        assert loaded_ranking is not None
        assert loaded_ranking["tags"] == ["Work"]
        assert loaded_ranking["wins"] == win_counts
        assert loaded_ranking["ranked_ids"] == ["a", "b", "c"]

    def test_save_serialises_head_to_head_keys(
        self, tmp_path: Path, sample_todos: list[Todo]
    ) -> None:
        ranking_file = tmp_path / "ranking.json"
        head_to_head_results: HeadToHeadResults = {("a", "b"): "a"}
        save_ranking(
            ["Work"],
            sample_todos,
            {"a": 1, "b": 0, "c": 0},
            head_to_head_results,
            sample_todos,
            ranking_file=ranking_file,
        )

        file_contents = json.loads(ranking_file.read_text())
        assert "a|||b" in file_contents["head_to_head"]

    def test_load_deserialises_head_to_head_keys(
        self, tmp_path: Path, sample_todos: list[Todo]
    ) -> None:
        ranking_file = tmp_path / "ranking.json"
        head_to_head_results: HeadToHeadResults = {("a", "b"): "a", ("b", "c"): "b"}
        win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}
        save_ranking(
            ["Work"],
            sample_todos,
            win_counts,
            head_to_head_results,
            sample_todos,
            ranking_file=ranking_file,
        )

        loaded_ranking = load_saved_ranking(ranking_file)
        assert loaded_ranking is not None
        parsed_head_to_head = deserialize_head_to_head(loaded_ranking["head_to_head"])
        assert parsed_head_to_head[("a", "b")] == "a"
        assert parsed_head_to_head[("b", "c")] == "b"


# ---------------------------------------------------------------------------
# apply_ranking_order_to_habitica()
# ---------------------------------------------------------------------------


class TestApplyRankingOrderToHabitica:
    def test_calls_api_for_each_todo(self, sample_todos: list[Todo]) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.post", return_value=mock_response) as mock_post:
                apply_ranking_order_to_habitica(sample_todos)

        assert mock_post.call_count == 3

    def test_uses_correct_positions(self, sample_todos: list[Todo]) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.post", return_value=mock_response) as mock_post:
                apply_ranking_order_to_habitica(sample_todos)

        api_calls = mock_post.call_args_list
        assert "/move/to/0" in api_calls[0][0][0]
        assert "/move/to/1" in api_calls[1][0][0]
        assert "/move/to/2" in api_calls[2][0][0]

    def test_uses_correct_task_ids(self, sample_todos: list[Todo]) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.post", return_value=mock_response) as mock_post:
                apply_ranking_order_to_habitica(sample_todos)

        api_calls = mock_post.call_args_list
        assert "/tasks/a/move" in api_calls[0][0][0]
        assert "/tasks/b/move" in api_calls[1][0][0]
        assert "/tasks/c/move" in api_calls[2][0][0]

    def test_empty_list_makes_no_api_calls(self) -> None:
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.post") as mock_post:
                apply_ranking_order_to_habitica([])

        mock_post.assert_not_called()

    def test_includes_x_client_header(self, sample_todos: list[Todo]) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.post", return_value=mock_response) as mock_post:
                apply_ranking_order_to_habitica(sample_todos[:1])

        headers = mock_post.call_args[1]["headers"]
        assert "x-client" in headers
        assert "uid" in headers["x-client"]


# ---------------------------------------------------------------------------
# display_comparison_progress()
# ---------------------------------------------------------------------------


class TestDisplayComparisonProgress:
    def test_shows_zero_percent_at_start(self, capsys: Any) -> None:
        display_comparison_progress(completed=0, total=10)
        output = capsys.readouterr().out
        assert "0%" in output
        assert "0 done" in output
        assert "10 to go" in output

    def test_shows_100_percent_when_complete(self, capsys: Any) -> None:
        display_comparison_progress(completed=10, total=10)
        output = capsys.readouterr().out
        assert "100%" in output
        assert "10 done" in output
        assert "0 to go" in output

    def test_shows_50_percent_at_halfway(self, capsys: Any) -> None:
        display_comparison_progress(completed=5, total=10)
        output = capsys.readouterr().out
        assert "50%" in output
        assert "5 done" in output
        assert "5 to go" in output

    def test_progress_bar_fills_with_completed_blocks(self, capsys: Any) -> None:
        display_comparison_progress(completed=10, total=10)
        output = capsys.readouterr().out
        assert "█" * 20 in output  # fully filled bar

    def test_progress_bar_empty_at_start(self, capsys: Any) -> None:
        display_comparison_progress(completed=0, total=10)
        output = capsys.readouterr().out
        assert "░" * 20 in output  # fully empty bar

    def test_handles_zero_total_without_error(self, capsys: Any) -> None:
        display_comparison_progress(completed=0, total=0)
        output = capsys.readouterr().out
        assert "0%" in output


# ---------------------------------------------------------------------------
# display_ranking() — smoke test (just ensure no exceptions)
# ---------------------------------------------------------------------------


class TestDisplayRanking:
    def test_prints_without_error(self, sample_todos: list[Todo], capsys: Any) -> None:
        win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}
        display_ranking(sample_todos, win_counts)
        output = capsys.readouterr().out
        assert "# 1" in output
        assert "Task A" in output

    def test_shows_win_counts(self, sample_todos: list[Todo], capsys: Any) -> None:
        win_counts: WinCounts = {"a": 2, "b": 1, "c": 0}
        display_ranking(sample_todos, win_counts)
        output = capsys.readouterr().out
        assert "2 wins" in output
        assert "1 wins" in output
        assert "0 wins" in output


# ---------------------------------------------------------------------------
# build_api_headers()
# ---------------------------------------------------------------------------


class TestBuildApiHeaders:
    def test_exits_when_env_vars_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit):
                build_api_headers()

    def test_exits_when_only_user_id_set(self) -> None:
        with patch.dict(os.environ, {"HABITICA_USER_ID": "uid"}, clear=True):
            with pytest.raises(SystemExit):
                build_api_headers()

    def test_returns_correct_headers_when_env_vars_set(self) -> None:
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            headers = build_api_headers()
        assert headers["x-api-user"] == "uid"
        assert headers["x-api-key"] == "token"
        assert "uid" in headers["x-client"]


# ---------------------------------------------------------------------------
# fetch_all_tags()
# ---------------------------------------------------------------------------


class TestFetchAllTags:
    def test_returns_tag_name_to_id_mapping(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"name": "Work", "id": "id-work"},
                {"name": "Personal", "id": "id-personal"},
            ]
        }
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                result = fetch_all_tags()
        assert result == {"Work": "id-work", "Personal": "id-personal"}

    def test_raises_on_http_error(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404")
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                with pytest.raises(requests.HTTPError):
                    fetch_all_tags()


# ---------------------------------------------------------------------------
# fetch_incomplete_todos()
# ---------------------------------------------------------------------------


class TestFetchIncompleteTodos:
    def test_returns_only_incomplete_todos(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "a", "text": "A", "tags": [], "completed": False},
                {"id": "b", "text": "B", "tags": [], "completed": True},
            ]
        }
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                result = fetch_incomplete_todos([])
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_filters_by_required_tag_ids(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "a", "text": "A", "tags": ["tag-work"], "completed": False},
                {"id": "b", "text": "B", "tags": [], "completed": False},
            ]
        }
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                result = fetch_incomplete_todos(["tag-work"])
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_returns_all_todos_when_no_tag_filter(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "a", "text": "A", "tags": [], "completed": False},
                {"id": "b", "text": "B", "tags": ["tag-work"], "completed": False},
            ]
        }
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                result = fetch_incomplete_todos([])
        assert len(result) == 2

    def test_todo_must_have_all_required_tags(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "a",
                    "text": "A",
                    "tags": ["tag-work", "tag-urgent"],
                    "completed": False,
                },
                {"id": "b", "text": "B", "tags": ["tag-work"], "completed": False},
            ]
        }
        with patch.dict(
            os.environ, {"HABITICA_USER_ID": "uid", "HABITICA_API_TOKEN": "token"}
        ):
            with patch("requests.get", return_value=mock_response):
                result = fetch_incomplete_todos(["tag-work", "tag-urgent"])
        assert len(result) == 1
        assert result[0]["id"] == "a"


# ---------------------------------------------------------------------------
# compute_max_items_for_comparisons()
# ---------------------------------------------------------------------------


class TestComputeMaxItemsForComparisons:
    def test_returns_14_for_100_comparisons(self) -> None:
        # 14*(14-1)/2 = 91 <= 100, 15*(15-1)/2 = 105 > 100
        assert compute_max_items_for_comparisons(100) == 14

    def test_returns_4_for_6_comparisons(self) -> None:
        # 4*3/2 = 6 <= 6, 5*4/2 = 10 > 6
        assert compute_max_items_for_comparisons(6) == 4

    def test_suggested_comparisons_within_limit(self) -> None:
        for max_c in [10, 50, 100, 200]:
            n = compute_max_items_for_comparisons(max_c)
            assert n * (n - 1) // 2 <= max_c

    def test_one_more_item_exceeds_limit(self) -> None:
        for max_c in [10, 50, 100, 200]:
            n = compute_max_items_for_comparisons(max_c)
            assert (n + 1) * n // 2 > max_c


# ---------------------------------------------------------------------------
# warn_and_maybe_limit_for_full_pairwise()
# ---------------------------------------------------------------------------


class TestWarnAndMaybeLimitForFullPairwise:
    def _make_todos(self, count: int) -> list[Todo]:
        return [make_todo(str(i), f"Task {i}") for i in range(count)]

    def test_returns_unchanged_when_within_limit(self) -> None:
        todos = self._make_todos(5)  # 5*4/2 = 10 comparisons — well within 100
        result = warn_and_maybe_limit_for_full_pairwise(todos)
        assert result == todos

    def test_returns_unchanged_when_exactly_at_limit(self) -> None:
        # Find N where N*(N-1)/2 == MAX_COMFORTABLE_COMPARISONS exactly, or just under
        n = compute_max_items_for_comparisons(MAX_COMFORTABLE_COMPARISONS)
        todos = self._make_todos(n)
        result = warn_and_maybe_limit_for_full_pairwise(todos)
        assert result == todos

    def test_warns_and_trims_when_user_says_yes(self, capsys: Any) -> None:
        todos = self._make_todos(20)  # 20*19/2 = 190 > 100
        with patch("builtins.input", return_value="Y"):
            result = warn_and_maybe_limit_for_full_pairwise(todos)
        suggested = compute_max_items_for_comparisons(MAX_COMFORTABLE_COMPARISONS)
        assert len(result) == suggested
        output = capsys.readouterr().out
        assert "🚨" in output
        assert "💡" in output
        assert "✅" in output

    def test_returns_full_list_when_user_says_no(self, capsys: Any) -> None:
        todos = self._make_todos(20)
        with patch("builtins.input", return_value="N"):
            result = warn_and_maybe_limit_for_full_pairwise(todos)
        assert len(result) == 20
        output = capsys.readouterr().out
        assert "🦁" in output

    def test_warning_includes_comparison_counts(self, capsys: Any) -> None:
        todos = self._make_todos(20)
        with patch("builtins.input", return_value="N"):
            warn_and_maybe_limit_for_full_pairwise(todos)
        output = capsys.readouterr().out
        assert "190" in output  # 20*19/2

    def test_trimmed_list_keeps_original_order(self) -> None:
        todos = self._make_todos(20)
        with patch("builtins.input", return_value="Y"):
            result = warn_and_maybe_limit_for_full_pairwise(todos)
        suggested = compute_max_items_for_comparisons(MAX_COMFORTABLE_COMPARISONS)
        assert result == todos[:suggested]


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

MAIN_AVAILABLE_TAGS = {"Work": "id-work"}
MAIN_SAMPLE_TODOS = [
    make_todo("a", "Task A"),
    make_todo("b", "Task B"),
    make_todo("c", "Task C"),
]
MAIN_WIN_COUNTS: WinCounts = {"a": 2, "b": 1, "c": 0}
MAIN_H2H: HeadToHeadResults = {("a", "b"): "a", ("a", "c"): "a", ("b", "c"): "b"}


class TestMain:
    def test_exits_when_no_todos_found(self) -> None:
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch("prioritize.fetch_incomplete_todos", return_value=[]):
                    with pytest.raises(SystemExit) as exc_info:
                        prioritize.main()
        assert exc_info.value.code == 0

    def test_no_saved_ranking_runs_full_pairwise(self) -> None:
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                with patch("builtins.input", side_effect=["", "N"]):
                                    prioritize.main()
        mock_full.assert_called_once()

    def test_partial_run_resumes_full_pairwise(self) -> None:
        # Only 1 of 3 pairs answered — should resume full pairwise
        partial_h2h: HeadToHeadResults = {("a", "b"): "a"}  # 1/3 pairs done
        saved: SavedRanking = {
            "tags": ["Work"],
            "wins": {"a": 1, "b": 0, "c": 0},
            "head_to_head": serialize_head_to_head(partial_h2h),
            "ranked_ids": ["a", "b", "c"],
        }
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                with patch("builtins.input", side_effect=["", "N"]):
                                    prioritize.main()
        mock_full.assert_called_once()
        # Should pass existing win counts as second positional arg for resume
        assert mock_full.call_args[0][1] is not None

    def test_saved_ranking_no_new_todos_returns_early_without_saving(self) -> None:
        saved: SavedRanking = {
            "tags": ["Work"],
            "wins": {"a": 2, "b": 1, "c": 0},
            "head_to_head": serialize_head_to_head(MAIN_H2H),
            "ranked_ids": ["a", "b", "c"],
        }
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch("prioritize.save_ranking") as mock_save:
                            prioritize.main()
        mock_save.assert_not_called()

    def test_saved_ranking_with_new_todos_user_chooses_keep_existing(self) -> None:
        saved: SavedRanking = {
            "tags": ["Work"],
            "wins": {"a": 2, "b": 1},
            "head_to_head": serialize_head_to_head({("a", "b"): "a"}),
            "ranked_ids": ["a", "b"],
        }
        todos_with_new = [
            make_todo("a", "Task A"),
            make_todo("b", "Task B"),
            make_todo("new", "New Task"),
        ]
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=todos_with_new
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch(
                            "prioritize.run_new_versus_existing_comparison",
                            return_value=({"a": 2, "b": 1, "new": 0}, {}),
                        ) as mock_keep:
                            with patch("prioritize.save_ranking"):
                                # K/R prompt → "K", Enter to begin, Y/N prompt → "N"
                                with patch(
                                    "builtins.input", side_effect=["K", "", "N"]
                                ):
                                    prioritize.main()
        mock_keep.assert_called_once()

    def test_saved_ranking_with_new_todos_user_chooses_reprioritize(self) -> None:
        saved: SavedRanking = {
            "tags": ["Work"],
            "wins": {"a": 2, "b": 1},
            "head_to_head": serialize_head_to_head({("a", "b"): "a"}),
            "ranked_ids": ["a", "b"],
        }
        todos_with_new = [
            make_todo("a", "Task A"),
            make_todo("b", "Task B"),
            make_todo("new", "New Task"),
        ]
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=todos_with_new
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                # K/R prompt → "R", Enter to begin, Y/N prompt → "N"
                                with patch(
                                    "builtins.input", side_effect=["R", "", "N"]
                                ):
                                    prioritize.main()
        mock_full.assert_called_once()

    def test_incremental_flag_auto_selects_keep_existing_without_prompting(
        self,
    ) -> None:
        saved: SavedRanking = {
            "tags": ["Work"],
            "wins": {"a": 2, "b": 1},
            "head_to_head": serialize_head_to_head({("a", "b"): "a"}),
            "ranked_ids": ["a", "b"],
        }
        todos_with_new = [
            make_todo("a", "Task A"),
            make_todo("b", "Task B"),
            make_todo("new", "New Task"),
        ]
        with patch("sys.argv", ["prioritize.py", "--tags", "Work", "--incremental"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=todos_with_new
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch(
                            "prioritize.run_new_versus_existing_comparison",
                            return_value=({"a": 2, "b": 1, "new": 0}, {}),
                        ) as mock_keep:
                            with patch("prioritize.save_ranking"):
                                # No K/R prompt — just Enter to begin, Y/N → "N"
                                with patch("builtins.input", side_effect=["", "N"]):
                                    prioritize.main()
        mock_keep.assert_called_once()

    def test_mismatched_saved_ranking_tags_runs_full_pairwise(self) -> None:
        saved: SavedRanking = {
            "tags": ["Personal"],  # different tag than --tags Work
            "wins": {"a": 2, "b": 1, "c": 0},
            "head_to_head": serialize_head_to_head(MAIN_H2H),
            "ranked_ids": ["a", "b", "c"],
        }
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=saved):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                with patch("builtins.input", side_effect=["", "N"]):
                                    prioritize.main()
        mock_full.assert_called_once()

    def test_reorder_flag_applies_ranking_without_prompting(self) -> None:
        with patch("sys.argv", ["prioritize.py", "--tags", "Work", "--reorder"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ):
                            with patch("prioritize.save_ranking"):
                                with patch(
                                    "prioritize.apply_ranking_order_to_habitica"
                                ) as mock_apply:
                                    with patch("builtins.input", return_value=""):
                                        prioritize.main()
        mock_apply.assert_called_once()

    def test_user_chooses_yes_to_apply_reorder(self) -> None:
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(MAIN_WIN_COUNTS, MAIN_H2H),
                        ):
                            with patch("prioritize.save_ranking"):
                                with patch(
                                    "prioritize.apply_ranking_order_to_habitica"
                                ) as mock_apply:
                                    # Enter to begin, Y/N → "Y"
                                    with patch("builtins.input", side_effect=["", "Y"]):
                                        prioritize.main()
        mock_apply.assert_called_once()

    def test_warning_shown_and_user_trims_when_comparisons_exceed_100(self) -> None:
        many_todos = [
            make_todo(str(i), f"Task {i}") for i in range(20)
        ]  # 190 comparisons
        suggested = compute_max_items_for_comparisons(MAX_COMFORTABLE_COMPARISONS)
        trimmed_win_counts = {str(i): 0 for i in range(suggested)}
        trimmed_h2h: HeadToHeadResults = {}
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=many_todos
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(trimmed_win_counts, trimmed_h2h),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                # Y = trim, Enter = ready to rumble, N = don't apply
                                with patch(
                                    "builtins.input", side_effect=["Y", "", "N"]
                                ):
                                    prioritize.main()
        called_todos = mock_full.call_args[0][0]
        assert len(called_todos) == suggested

    def test_warning_shown_and_user_proceeds_with_all_when_comparisons_exceed_100(
        self,
    ) -> None:
        many_todos = [
            make_todo(str(i), f"Task {i}") for i in range(20)
        ]  # 190 comparisons
        win_counts = {str(i): 0 for i in range(20)}
        with patch("sys.argv", ["prioritize.py", "--tags", "Work"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=many_todos
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=(win_counts, {}),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                # N = keep all, Enter = ready to rumble, N = don't apply
                                with patch(
                                    "builtins.input", side_effect=["N", "", "N"]
                                ):
                                    prioritize.main()
        called_todos = mock_full.call_args[0][0]
        assert len(called_todos) == 20

    def test_limit_flag_trims_todos(self) -> None:
        with patch("sys.argv", ["prioritize.py", "--tags", "Work", "--limit", "2"]):
            with patch("prioritize.fetch_all_tags", return_value=MAIN_AVAILABLE_TAGS):
                with patch(
                    "prioritize.fetch_incomplete_todos", return_value=MAIN_SAMPLE_TODOS
                ):
                    with patch("prioritize.load_saved_ranking", return_value=None):
                        with patch(
                            "prioritize.run_full_pairwise_comparison",
                            return_value=({"a": 1, "b": 0}, {("a", "b"): "a"}),
                        ) as mock_full:
                            with patch("prioritize.save_ranking"):
                                with patch("builtins.input", side_effect=["", "N"]):
                                    prioritize.main()
        called_with_todos = mock_full.call_args[0][0]
        assert len(called_with_todos) == 2
