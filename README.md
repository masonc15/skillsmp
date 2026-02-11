# skillsmp

Search the [SkillsMP marketplace](https://skillsmp.com) for agent skills from the command line.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
uv tool install git+https://github.com/colinmason/skillsmp
```

Or install from a local clone:

```
git clone https://github.com/colinmason/skillsmp
cd skillsmp
uv tool install .
```

To upgrade later: `uv tool upgrade skillsmp`.

To uninstall: `uv tool uninstall skillsmp`.

## Setup

Set your API key. The easiest way is to add it to `~/.env`:

```
SKILLSMP_API_KEY=your-key-here
```

Or export it in your shell profile. `skillsmp` reads from the environment first, then falls back to `~/.env`.

## Usage

Keyword search is the default — fast, supports pagination and sorting:

```
skillsmp terraform
skillsmp --limit 5 --sort recent react testing
```

AI semantic search takes a natural language question and returns relevance-scored results:

```
skillsmp --ai "how to optimize database queries"
```

### Output modes

Human-readable output is the default. For scripts, use `--json` for structured data or `--plain` for one-line-per-result tab-separated output that works with `grep` and `awk`:

```
skillsmp --json deployment
skillsmp --plain react | grep facebook
skillsmp --plain terraform | awk -F'\t' '$2 > 1000'
```

### All flags

```
-a, --ai        AI semantic search
-n, --limit N   Results per page (1-100, default: 10)
-p, --page N    Page number (default: 1)
-s, --sort KEY  Sort by: stars, recent (default: stars)
-j, --json      JSON output
    --plain     Tab-separated, one line per result
-h, --help      Show help
    --version   Show version
```

`--limit`, `--page`, and `--sort` apply to keyword search only.

## Uninstall

```
uv tool uninstall skillsmp
```
