# CLI Output Contract

Default output should be terse text optimized for agent inspection:

- `list`: handles and compact metadata.
- `find`: ranked results with score, handle, source, and preview.
- `show`: compact object view.
- `show --full`: full text for one selected handle.

Use `--format json` when a deterministic script needs stable parsing.
Large payloads should be written to files and surfaced by path.
