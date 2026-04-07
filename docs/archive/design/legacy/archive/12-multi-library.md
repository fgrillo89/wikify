# Multi-Library Support

## Problem
A researcher works across multiple domains (e.g., ALD thin films and memristor devices). Mixing them in one corpus creates noisy graph signals.

## Solution
`--library` flag scopes all data paths:

```
scholarforge --library ald ingest ./ald-papers/
scholarforge --library memristors ingest ./memristor-papers/
```

## How it works
- `config.py` has a `library` property. Default is `"default"`.
- All paths are computed as properties: `data_dir`, `db_path`, `chromadb_dir`, `cache_dir`, `figures_dir`.
- When library != "default", paths scope to `data/libraries/<name>/`.
- CLI callback sets `settings.library` before any command runs.

## Data layout
```
data/                          # default library
data/libraries/ald/            # ald library
data/libraries/memristors/     # memristors library
```

Each library gets its own SQLite DB, ChromaDB collection, vault, and cache.

## MCP server
The MCP server accepts `--library` too: `scholarforge mcp --library ald`

## Where the code lives
- `config.py` — Settings with library-scoped properties
- `cli.py` — `--library` callback
