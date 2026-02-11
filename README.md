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

## Search modes

The default keyword search matches your query against skill names and descriptions, sorted by stars. It's fast (~300ms) and supports pagination, but ranks by popularity rather than relevance.

`--ai` runs a vector similarity search powered by Cloudflare Vectorize. Each skill's full SKILL.md has been embedded, and your query is compared against those embeddings by cosine similarity. This returns fewer results (~10) with relevance scores, but finds semantically related skills that keyword search misses. No pagination — just the nearest neighbors. Slower (~4-5s) because the query must be embedded first.

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
