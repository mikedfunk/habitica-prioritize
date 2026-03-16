#!/usr/bin/env python3
"""
Habitica Prioritization Matrix

Ranks Habitica todos using pairwise head-to-head comparisons.
Each pair is compared once; the item with more wins ranks higher.
Ties are broken by the direct head-to-head result.

Saves results to ~/.habitica-priority-rank.json so future runs
can do incremental re-ranking when new items are added.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from functools import cmp_to_key
from itertools import combinations
from pathlib import Path
from typing import Any, TypedDict

import requests

HABITICA_API_BASE_URL = "https://habitica.com/api/v3"
DEFAULT_RANKING_FILE = Path.home() / ".habitica-priority-rank.json"
MAX_COMFORTABLE_COMPARISONS = 100

# Type aliases
WinCounts = dict[str, int]
HeadToHeadResults = dict[tuple[str, str], str]
SavedRanking = dict[str, Any]
SaveCallback = Callable[[WinCounts, HeadToHeadResults], None]


class Todo(TypedDict):
    id: str
    text: str
    tags: list[str]
    completed: bool


def build_api_headers() -> dict[str, str]:
    user_id = os.environ.get("HABITICA_USER_ID")
    api_token = os.environ.get("HABITICA_API_TOKEN")
    if not user_id or not api_token:
        print(
            "Error: HABITICA_USER_ID and HABITICA_API_TOKEN environment variables are required."
        )
        sys.exit(1)
    return {
        "x-api-user": user_id,
        "x-api-key": api_token,
        "x-client": f"{user_id}-habitica-prioritize",
        "Content-Type": "application/json",
    }


def fetch_all_tags() -> dict[str, str]:
    """Return {tag_name: tag_id} for all tags in the user's Habitica account."""
    response = requests.get(
        f"{HABITICA_API_BASE_URL}/tags", headers=build_api_headers()
    )
    response.raise_for_status()
    return {tag["name"]: tag["id"] for tag in response.json()["data"]}


def find_tag_ids_by_name(
    tag_names: list[str], available_tags: dict[str, str]
) -> list[str]:
    """Look up tag IDs by name (case-insensitive), exiting if any name is not found."""
    resolved_ids: list[str] = []
    for tag_name in tag_names:
        matching_id = next(
            (
                tag_id
                for name, tag_id in available_tags.items()
                if name.lower() == tag_name.lower()
            ),
            None,
        )
        if not matching_id:
            print(f"Error: tag '{tag_name}' not found in Habitica.")
            sys.exit(1)
        resolved_ids.append(matching_id)
    return resolved_ids


def prompt_user_for_tag_filter(available_tags: dict[str, str]) -> list[str]:
    """Show available tags and ask the user which ones to filter by. Returns tag names."""
    print("\n🏷️  Available tags:")
    for tag_name in sorted(available_tags):
        print(f"  • {tag_name}")
    print()
    user_input = input(
        "Filter by tags (comma-separated, or press Enter for no filter): "
    ).strip()
    if not user_input:
        return []
    return [tag_name.strip() for tag_name in user_input.split(",") if tag_name.strip()]


def fetch_incomplete_todos(required_tag_ids: list[str]) -> list[Todo]:
    """Return incomplete todos that have ALL of the required tag IDs (or all todos if the list is empty)."""
    response = requests.get(
        f"{HABITICA_API_BASE_URL}/tasks/user?type=todos",
        headers=build_api_headers(),
    )
    response.raise_for_status()
    return [
        todo
        for todo in response.json()["data"]
        if not todo.get("completed")
        and all(
            required_tag_id in todo.get("tags", [])
            for required_tag_id in required_tag_ids
        )
    ]


def generate_comparison_labels(count: int) -> list[str]:
    """Return letter labels A–Z for up to 26 items."""
    return [chr(65 + index) for index in range(min(count, 26))]


def serialize_head_to_head(head_to_head_results: HeadToHeadResults) -> dict[str, str]:
    """Serialize HeadToHeadResults tuple keys to JSON-safe strings for storage."""
    return {
        f"{task_id_a}|||{task_id_b}": winner_task_id
        for (task_id_a, task_id_b), winner_task_id in head_to_head_results.items()
    }


def deserialize_head_to_head(
    serialized_head_to_head: dict[str, str],
) -> HeadToHeadResults:
    """Deserialize stored head-to-head strings back to tuple keys."""
    return {
        (serialized_key.split("|||")[0], serialized_key.split("|||")[1]): winner_task_id
        for serialized_key, winner_task_id in serialized_head_to_head.items()
    }


def display_comparison_progress(completed: int, total: int) -> None:
    """Print a progress bar showing how many comparisons are done vs. remaining."""
    remaining = total - completed
    percent_complete = int((completed / total) * 100) if total > 0 else 0
    filled_blocks = int(percent_complete / 5)  # 20-block bar (each block = 5%)
    empty_blocks = 20 - filled_blocks
    progress_bar = "█" * filled_blocks + "░" * empty_blocks
    print(
        f"  ⚔️  [{progress_bar}] {percent_complete}%  —  {completed} done, {remaining} to go 💪"
    )


def prompt_user_for_choice(prompt: str, valid_choices: set[str]) -> str:
    while True:
        choice = input(prompt).strip().upper()
        if choice in valid_choices:
            return choice
        print(f"  Please enter one of: {', '.join(sorted(valid_choices))}")


def run_full_pairwise_comparison(
    todos: list[Todo],
    existing_win_counts: WinCounts | None = None,
    existing_head_to_head: HeadToHeadResults | None = None,
    save_callback: SaveCallback | None = None,
) -> tuple[WinCounts, HeadToHeadResults]:
    """Compare every pair of todos head-to-head. Returns win counts and head-to-head results.

    If existing_win_counts/existing_head_to_head are provided, already-answered pairs are
    skipped and the run resumes from where it left off. save_callback is called after each answer.
    """
    todo_count = len(todos)
    win_counts: WinCounts = dict(existing_win_counts) if existing_win_counts else {todo["id"]: 0 for todo in todos}
    head_to_head_results: HeadToHeadResults = dict(existing_head_to_head) if existing_head_to_head else {}

    all_pairs = list(combinations(range(todo_count), 2))
    pending_pairs = [
        (i, j) for i, j in all_pairs
        if (todos[i]["id"], todos[j]["id"]) not in head_to_head_results
        and (todos[j]["id"], todos[i]["id"]) not in head_to_head_results
    ]
    skipped = len(all_pairs) - len(pending_pairs)

    if skipped > 0:
        print(f"\n⏭️  Skipped {skipped} already-answered battle(s). {len(pending_pairs)} remaining.")
    if not pending_pairs:
        return win_counts, head_to_head_results

    total = len(pending_pairs)
    print(f"\n⚔️  {total} head-to-head battles! Pick the higher-priority task.\n")

    for comparison_index, (index_a, index_b) in enumerate(pending_pairs):
        print(f"🥊 Battle [{comparison_index + 1}/{total}]")
        display_comparison_progress(comparison_index, total)
        print(f"  1: {todos[index_a]['text']}")
        print(f"  2: {todos[index_b]['text']}")
        choice = prompt_user_for_choice("  👑 Winner? (1/2): ", {"1", "2"})
        winner_task_id = todos[index_a]["id"] if choice == "1" else todos[index_b]["id"]
        win_counts[winner_task_id] += 1
        head_to_head_results[(todos[index_a]["id"], todos[index_b]["id"])] = winner_task_id
        if save_callback:
            save_callback(win_counts, head_to_head_results)
        print()

    return win_counts, head_to_head_results


def run_new_versus_existing_comparison(
    new_todos: list[Todo],
    existing_todos: list[Todo],
    previous_win_counts: WinCounts,
    previous_head_to_head: HeadToHeadResults,
    save_callback: SaveCallback | None = None,
) -> tuple[WinCounts, HeadToHeadResults]:
    """Compare each new todo against each existing todo only.

    Skips new-vs-new and existing-vs-existing comparisons, preserving the
    relative ordering already established among existing todos.
    Already-answered pairs are skipped when resuming a partial run.
    save_callback is called after each answer.
    """
    win_counts: WinCounts = dict(previous_win_counts)
    head_to_head_results: HeadToHeadResults = dict(previous_head_to_head)

    for new_todo in new_todos:
        if new_todo["id"] not in win_counts:
            win_counts[new_todo["id"]] = 0

    all_pairs = [
        (new_todo, existing_todo)
        for new_todo in new_todos
        for existing_todo in existing_todos
    ]
    pending_pairs = [
        (new_todo, existing_todo) for new_todo, existing_todo in all_pairs
        if (new_todo["id"], existing_todo["id"]) not in head_to_head_results
        and (existing_todo["id"], new_todo["id"]) not in head_to_head_results
    ]
    skipped = len(all_pairs) - len(pending_pairs)

    if skipped > 0:
        print(f"\n⏭️  Skipped {skipped} already-answered battle(s). {len(pending_pairs)} remaining.")
    if not pending_pairs:
        return win_counts, head_to_head_results

    total = len(pending_pairs)
    print(f"\n✨ {total} battles — new challengers vs. the established guard!\n")

    for comparison_index, (new_todo, existing_todo) in enumerate(pending_pairs):
        print(f"🥊 Battle [{comparison_index + 1}/{total}]")
        display_comparison_progress(comparison_index, total)
        print(f"  1: {new_todo['text']}  🆕")
        print(f"  2: {existing_todo['text']}")
        choice = prompt_user_for_choice("  👑 Winner? (1/2): ", {"1", "2"})
        winner_task_id = new_todo["id"] if choice == "1" else existing_todo["id"]
        win_counts[winner_task_id] = win_counts.get(winner_task_id, 0) + 1
        head_to_head_results[(new_todo["id"], existing_todo["id"])] = winner_task_id
        if save_callback:
            save_callback(win_counts, head_to_head_results)
        print()

    return win_counts, head_to_head_results


def rank_todos_by_win_count(
    todos: list[Todo],
    win_counts: WinCounts,
    head_to_head_results: HeadToHeadResults,
) -> list[Todo]:
    """Sort todos by win count descending, breaking ties using the direct head-to-head result."""

    def resolve_tie_by_head_to_head(todo_a: Todo, todo_b: Todo) -> int:
        forward_key: tuple[str, str] = (todo_a["id"], todo_b["id"])
        reverse_key: tuple[str, str] = (todo_b["id"], todo_a["id"])
        winner_task_id = head_to_head_results.get(
            forward_key
        ) or head_to_head_results.get(reverse_key)
        if winner_task_id == todo_a["id"]:
            return -1
        if winner_task_id == todo_b["id"]:
            return 1
        return 0

    def compare_by_win_count(todo_a: Todo, todo_b: Todo) -> int:
        win_count_difference = win_counts.get(todo_b["id"], 0) - win_counts.get(
            todo_a["id"], 0
        )
        return (
            win_count_difference
            if win_count_difference != 0
            else resolve_tie_by_head_to_head(todo_a, todo_b)
        )

    return sorted(todos, key=cmp_to_key(compare_by_win_count))


def load_saved_ranking(
    ranking_file: Path = DEFAULT_RANKING_FILE,
) -> SavedRanking | None:
    if ranking_file.exists():
        with open(ranking_file) as file:
            return json.load(file)
    return None


def save_ranking(
    tag_names: list[str],
    todos: list[Todo],
    win_counts: WinCounts,
    head_to_head_results: HeadToHeadResults,
    ranked_todos: list[Todo],
    ranking_file: Path = DEFAULT_RANKING_FILE,
) -> None:
    data: SavedRanking = {
        "tags": tag_names,
        "wins": win_counts,
        "head_to_head": serialize_head_to_head(head_to_head_results),
        "ranked_ids": [todo["id"] for todo in ranked_todos],
    }
    with open(ranking_file, "w") as file:
        json.dump(data, file, indent=2)
    print(f"\n💾 Results saved to {ranking_file}")


def apply_ranking_order_to_habitica(ranked_todos: list[Todo]) -> None:
    """Move each todo to its ranked position in the Habitica UI."""
    headers = build_api_headers()
    print(f"\n🚀 Sending your ranking to Habitica ({len(ranked_todos)} tasks)...")
    for position, todo in enumerate(ranked_todos):
        response = requests.post(
            f"{HABITICA_API_BASE_URL}/tasks/{todo['id']}/move/to/{position}",
            headers=headers,
        )
        response.raise_for_status()
        print(f"  #{position + 1}: {todo['text']}")
    print("✅ Done! Your tasks are ranked and ready to conquer. 🎮")


def display_ranking(ranked_todos: list[Todo], win_counts: WinCounts) -> None:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    print("\n" + "─" * 50)
    print("🏆  YOUR PRIORITY RANKING")
    print("─" * 50)
    for rank, todo in enumerate(ranked_todos, 1):
        win_count = win_counts.get(todo["id"], 0)
        medal = medals.get(rank, "  ")
        print(f"  {medal} #{rank:2d} ({win_count} wins)  {todo['text']}")
    print()


def compute_max_items_for_comparisons(max_comparisons: int) -> int:
    """Return the largest N such that N*(N-1)/2 <= max_comparisons."""
    n = 1
    while (n + 1) * n // 2 <= max_comparisons:
        n += 1
    return n


def warn_and_maybe_limit_for_full_pairwise(todos: list[Todo]) -> list[Todo]:
    """Warn if a full pairwise would exceed MAX_COMFORTABLE_COMPARISONS and offer to trim."""
    count = len(todos)
    comparisons = count * (count - 1) // 2
    if comparisons <= MAX_COMFORTABLE_COMPARISONS:
        return todos

    suggested = compute_max_items_for_comparisons(MAX_COMFORTABLE_COMPARISONS)
    suggested_comparisons = suggested * (suggested - 1) // 2
    print(
        f"\n🚨 Yikes! {count} items = {comparisons} comparisons — that's a marathon, not a sprint! 😅"
    )
    print(
        f"💡 Limiting to the top {suggested} items would give you a breezy {suggested_comparisons} comparisons instead."
    )
    choice = prompt_user_for_choice(
        f"✂️  Trim to top {suggested} items? (Y/N): ", {"Y", "N"}
    )
    if choice == "Y":
        print("✅ Trimmed! Let's keep it spicy but survivable. 🌶️")
        return todos[:suggested]
    print(
        f"🦁 Fearless! All {count} items it is — may the odds be ever in your favor! 💪"
    )
    return todos


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank Habitica todos by pairwise comparison (prioritization matrix)."
    )
    parser.add_argument(
        "--tags",
        nargs="*",
        default=None,
        metavar="TAG",
        help="Tag names to filter by (all must match). Prompts interactively if omitted.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max items to compare")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Automatically keep existing priorities and only compare new items against the already-ranked list (skips the K/R prompt)",
    )
    parser.add_argument(
        "--reorder",
        action="store_true",
        help="Automatically apply the ranking order to Habitica without prompting",
    )
    args = parser.parse_args()

    available_tags = fetch_all_tags()

    if args.tags is None:
        tag_names = prompt_user_for_tag_filter(available_tags)
    else:
        tag_names = args.tags

    required_tag_ids = find_tag_ids_by_name(tag_names, available_tags)
    tag_filter_description = ", ".join(tag_names) if tag_names else "(no filter)"

    todos = fetch_incomplete_todos(required_tag_ids)
    if not todos:
        print(
            f"😴 No incomplete todos matching tags: {tag_filter_description}. Nothing to rank!"
        )
        sys.exit(0)

    if args.limit:
        todos = todos[: args.limit]

    todo_count = len(todos)
    labels = generate_comparison_labels(todo_count)

    print(f"\n📋 Found {todo_count} todos ({tag_filter_description}):")
    for index, todo in enumerate(todos):
        print(f"  {labels[index] if index < 26 else '?'}: {todo['text']}")

    saved_ranking = load_saved_ranking()
    win_counts: WinCounts
    head_to_head_results: HeadToHeadResults

    def make_save_callback(current_todos: list[Todo]) -> SaveCallback:
        def save_progress(wc: WinCounts, h2h: HeadToHeadResults) -> None:
            ranked = rank_todos_by_win_count(current_todos, wc, h2h)
            save_ranking(tag_names, current_todos, wc, h2h, ranked)
        return save_progress

    if saved_ranking and saved_ranking.get("tags") == tag_names:
        previous_win_counts: WinCounts = saved_ranking.get("wins", {})
        previous_head_to_head: HeadToHeadResults = deserialize_head_to_head(
            saved_ranking.get("head_to_head", {})
        )
        new_todos = [todo for todo in todos if todo["id"] not in previous_win_counts]
        existing_todos = [todo for todo in todos if todo["id"] in previous_win_counts]

        if not new_todos:
            expected_pairs = len(todos) * (len(todos) - 1) // 2
            completed_pairs = len(previous_head_to_head)
            if completed_pairs < expected_pairs:
                print(
                    f"\n⏸️  Partial run detected! {completed_pairs}/{expected_pairs} battles completed. Resuming..."
                )
                input("\n🥊 Ready to resume? Press Enter to continue...")
                win_counts, head_to_head_results = run_full_pairwise_comparison(
                    todos, previous_win_counts, previous_head_to_head, make_save_callback(todos)
                )
            else:
                print(
                    "\n✨ No new items found — all todos are already ranked. Here's your current standing:"
                )
                ranked_todos = rank_todos_by_win_count(
                    todos, previous_win_counts, previous_head_to_head
                )
                display_ranking(ranked_todos, previous_win_counts)
                return

        else:
            print(
                f"\n🆕 {len(new_todos)} new challenger(s) detected! {len(existing_todos)} veterans already ranked."
            )

            keep_existing = False
            if existing_todos:
                if args.incremental:
                    keep_existing = True
                else:
                    keep_existing = (
                        prompt_user_for_choice(
                            "Keep existing priorities (K) or re-prioritize everything from scratch (R)? (K/R): ",
                            {"K", "R"},
                        )
                        == "K"
                    )

            if keep_existing:
                input("\n🥊 Ready to rumble? Press Enter to begin...")
                win_counts, head_to_head_results = run_new_versus_existing_comparison(
                    new_todos, existing_todos, previous_win_counts, previous_head_to_head,
                    make_save_callback(todos)
                )
            else:
                todos = warn_and_maybe_limit_for_full_pairwise(todos)
                input("\n🥊 Ready to rumble? Press Enter to begin...")
                win_counts, head_to_head_results = run_full_pairwise_comparison(
                    todos, save_callback=make_save_callback(todos)
                )

    else:
        todos = warn_and_maybe_limit_for_full_pairwise(todos)
        input("\n🥊 Ready to rumble? Press Enter to begin...")
        win_counts, head_to_head_results = run_full_pairwise_comparison(
            todos, save_callback=make_save_callback(todos)
        )

    ranked_todos = rank_todos_by_win_count(todos, win_counts, head_to_head_results)
    display_ranking(ranked_todos, win_counts)
    save_ranking(tag_names, todos, win_counts, head_to_head_results, ranked_todos)

    print(
        "💡 Tip: applying will reorder your To Do tasks under the Active tab in Habitica remotely."
    )
    if (
        args.reorder
        or prompt_user_for_choice(
            "🚀 Apply this order to Habitica? (Y/N): ", {"Y", "N"}
        )
        == "Y"
    ):
        apply_ranking_order_to_habitica(ranked_todos)


if __name__ == "__main__":
    main()
