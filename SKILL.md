---
name: habitica-prioritize
description: Rank Habitica todos using a pairwise prioritization matrix. Use when the user wants to prioritize, rank, or re-prioritize their Habitica tasks (especially Work todos) by comparing them head-to-head. Also use when user adds a new task and wants to insert it into an existing ranking.
---

# Habitica Prioritization Matrix

Ranks Habitica todos using the pairwise comparison method: every item battles every other item
head-to-head, the item with the most wins ranks highest. Ties are broken by the direct
head-to-head result. Results are saved so future runs can do incremental re-ranking.

## Usage

### Full ranking (first time, or full re-rank)

```bash
uv run scripts/prioritize.py
```

If `--tags` is omitted, the script prompts interactively with a list of available tags.
Pass tags directly to skip the prompt:

```bash
# Single tag
uv run scripts/prioritize.py --tags Work

# Multiple tags (todos must match ALL specified tags)
uv run scripts/prioritize.py --tags Work Urgent

# No filter — rank all incomplete todos
uv run scripts/prioritize.py --tags
```

Limit to top N items (recommended ≤15 to keep comparisons manageable):

```bash
uv run scripts/prioritize.py --tags Work --limit 10
```

### When new tasks are found (K/R prompt)

When a saved ranking exists and new todos are detected, the script prompts:

```
Keep existing priorities (K) or re-prioritize everything from scratch (R)? (K/R):
```

- **K** — compares each new item against every existing item only (new-vs-new and existing-vs-existing skipped)
- **R** — full pairwise comparison of all items from scratch

### Incremental re-rank without prompting

Automatically selects K (keep existing priorities) without prompting:

```bash
uv run scripts/prioritize.py --incremental
```

### Applying the ranking to Habitica

After ranking, the script prompts: `Apply this order to Habitica? (Y/N)`.
Answering Y reorders your tasks in the Habitica UI to match the ranking.

To skip the prompt and always apply automatically:

```bash
uv run scripts/prioritize.py --tags Work --reorder
```

## Requirements

Environment variables must be set:
- `HABITICA_USER_ID`
- `HABITICA_API_TOKEN`

## How it works

1. Fetches all incomplete todos matching the given tags (AND logic — todos must have all specified tags)
2. Walks you through every pair: "Which is higher priority? (A/B)"
3. Tallies wins per item
4. Produces a ranked list (#1 = most wins)
5. Saves results to `~/.habitica-priority-rank.json` for incremental use

## Running tests

```bash
uv run --with pytest --with requests pytest scripts/tests/ -v
```

## Notes

- N items = N*(N-1)/2 comparisons (10 items = 45, 15 items = 105)
- When new tasks are found and a saved ranking exists, choose K to compare only new vs. existing (skips new-vs-new and existing-vs-existing)
- Use `--incremental` to auto-select K without being prompted
- Re-run without `--incremental` and choose R periodically for a fresh full re-rank
