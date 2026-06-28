# Security Policy

## Supported versions

Wikify is in an early research-preview stage. Security fixes are applied
to the latest release on the `master` branch only.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

Please do not report security issues through public GitHub issues.

Use GitHub's private vulnerability reporting: open the repository's
**Security** tab and click **Report a vulnerability**. This opens a
private advisory visible only to the maintainers.

We aim to acknowledge a report within 72 hours and to provide a
remediation timeline after triage.

## Scope and handling notes

Wikify drives LLMs and an MCP server over a local document corpus. When
reporting, keep these tool-specific concerns in mind:

- **Secrets.** API keys are read from the environment via `litellm` and
  must never be committed or pasted into issues, logs, or reproduction
  steps. Scrub keys from any attached output.
- **Prompt injection.** Corpus documents are untrusted input. Report any
  path by which corpus or web content can exfiltrate secrets or execute
  unintended tool calls.
- **Supply chain.** Dependencies are pinned and the lockfile is committed.
  Report compromised or typosquatted dependencies through the same private
  channel.
- **Local filesystem.** The MCP server reads a user-supplied corpus path.
  Report any traversal that escapes the configured corpus directory.
