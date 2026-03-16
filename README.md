# habitica-prioritize

A Claude Code skill that ranks your [Habitica](https://habitica.com) todos using a **pairwise prioritization matrix** — the same method described in [this video](https://www.youtube.com/watch?v=4eiZKX89U_s).

Instead of staring at a long list trying to figure out what matters most, you compare tasks two at a time. The item that wins the most head-to-head battles rises to the top.

## How it works

1. Fetches your incomplete Habitica todos filtered by a tag (default: `Work`)
2. Walks you through every pair: *"Which is higher priority — A or B?"*
3. Tallies wins and produces a definitive ranked list
4. Saves results so future runs can **insert new tasks** without re-doing all comparisons — or re-prioritize everything from scratch

## Installation

```bash
npx skills add mikefunk/habitica-prioritize -g
```

Or clone manually and symlink into your Claude skills directory.

## Requirements

Set these environment variables (e.g. in `~/.zshrc`):

```bash
export HABITICA_USER_ID=your-user-id
export HABITICA_API_TOKEN=your-api-token
```

Find these in Habitica under **Settings → API**.

## Usage

### Full ranking (first time, or periodic re-rank)

```bash
uv run scripts/prioritize.py
```

You'll be prompted to choose which tags to filter by:

```
Available tags:
  Personal
  Urgent
  Work
  ...

Filter by tags (comma-separated, or press Enter for no filter): Work
```

You can also pass tags directly as CLI arguments to skip the prompt:

```bash
# Single tag
uv run scripts/prioritize.py --tags Work

# Multiple tags (todos must match ALL specified tags)
uv run scripts/prioritize.py --tags Work Urgent

# No filter — rank all incomplete todos
uv run scripts/prioritize.py --tags
```

Limit to top N items (recommended ≤15 — N items = N×(N-1)/2 comparisons):

```bash
uv run scripts/prioritize.py --tags Work --limit 10
```

### When new tasks are found (K/R prompt)

When a saved ranking exists and new todos are detected, the script asks:

```
2 new item(s) found, 5 already ranked.
Keep existing priorities (K) or re-prioritize everything from scratch (R)? (K/R):
```

- **K (keep)** — compares each new item against every existing item only. Existing-vs-existing comparisons are skipped, preserving the order you already established.
- **R (re-rank)** — discards the saved ranking and runs a full pairwise comparison of all items from scratch.

For example, if existing tasks A and B are ranked and new tasks C and D appear, choosing K runs: C vs A, C vs B, D vs A, D vs B — but never C vs D or A vs B again.

### Incremental re-rank without prompting

To automatically choose K (keep existing priorities) without being asked:

```bash
uv run scripts/prioritize.py --incremental
```

### Applying the ranking to Habitica

After every ranking run, you'll be prompted:

```
Apply this order to Habitica? (Y/N):
```

Answering `Y` reorders your tasks in the Habitica UI (To Do's → Active tab) to match the ranking. To skip the prompt and always apply automatically:

```bash
uv run scripts/prioritize.py --tags Work --reorder
```

### Example output

```
Found 10 todos tagged 'Work':
  A: Fix production bug in auth service
  B: Write Q2 roadmap doc
  ...

[1/45]
  A: Fix production bug in auth service
  B: Write Q2 roadmap doc
  Higher priority? (A/B): A

...

==================================================
FINAL RANKING
==================================================
  # 1 (9 wins)  Fix production bug in auth service
  # 2 (8 wins)  Unblock design team on API spec
  # 3 (7 wins)  Write Q2 roadmap doc
  ...
```

Results are saved to `~/.habitica-priority-rank.json`.

## Running tests

```bash
uv run --with pytest --with requests pytest scripts/tests/ -v
```

## License

MIT
