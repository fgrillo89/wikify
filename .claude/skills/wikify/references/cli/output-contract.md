# CLI Output Contract

Default output should be terse text optimized for agent inspection:

- `list`: one handle per line.
- `find`: ranked results with score, citation count, handle, and title.
- `show`: compact object view.
- `show --full`: full text for one selected handle.
- `traverse`: handles only (or handle + minimal columns in compact
  mode), so the output pipes directly into another `show` or
  `traverse` call.

## Format Selection

Read commands accept `--format auto|quiet|compact|json`:

- `quiet`    one short handle per line; nothing else.
- `compact`  tab-separated columns, default when stdout is a TTY.
- `json`     existing JSON shape; use when a deterministic script
              needs stable parsing.
- `auto`     compact when stdout is a TTY, quiet when piped.

`auto` is the default. The TTY auto-detect is what makes
`wikify corpus find ... | wikify corpus traverse ...` work without
explicit `--format` flags on either side.

## Handle Format

- Corpus: `doc:<short-or-full>`, `chunk:<short-or-full>`. Short forms
  are the 12-hex doc suffix or the trailing 8-hex chunk segment. Any
  unique suffix also resolves; ambiguous matches return an error
  listing candidates.
- Wiki: `page:<slug>` or just `<slug>`. Slugs are natural Wikipedia-
  style titles. Case-insensitive unique prefixes resolve.

Every handle the CLI **emits** is valid input to `show` and
`traverse`. This round-trip invariant lets the agent compose commands
with shell pipes without parsing.

## Tab-Separated Discipline

`compact` rows are always tab-separated, never space-padded. `cut -f<n>`
works deterministically:

```bash
# col 3 = handle in `corpus find` compact output
wikify corpus find "<q>" --by paper --rank citation_count \
    --top-k 3 --format compact --corpus <c> \
  | cut -f4
```

(Use `--format quiet` when you want handles only — that is what `auto`
falls back to when piping.)

Large payloads should be written to files and surfaced by path.
