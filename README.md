# skillsmp

Search the [SkillsMP marketplace](https://skillsmp.com) for agent skills from the command line.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
uv tool install git+https://github.com/masonc15/skillsmp
```

Set your API key in `~/.env` or export it directly:

```
SKILLSMP_API_KEY=your-key-here
```

## Usage

```
skillsmp terraform
skillsmp --limit 5 --sort recent react testing
skillsmp --ai "how to optimize database queries"
```

Use `--json` for structured output or `--plain` for tab-separated lines that pipe to `grep` and `awk`:

```
skillsmp --json deployment
skillsmp --plain react | grep facebook
```

## Flags

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
