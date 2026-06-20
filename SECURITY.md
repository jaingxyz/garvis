# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities.

Use GitHub's [private vulnerability reporting](https://github.com/jaingxyz/garvis/security/advisories/new) on this repository instead. That channel notifies the maintainer privately and creates a draft advisory.

You can expect an initial response within ~7 days. Fix timelines depend on severity and reachability — this is a personal project, not a service.

## Scope

In scope:

- Code in `garvis/` that gathers messages, classifies/prioritizes them, runs agentic
  actions (delete, send, calendar), drives the voice daemon, or talks to the local MCP
  servers over stdio.
- Prompt-injection paths: message content is untrusted input that reaches an LLM and can
  influence classification or proposed actions.
- Dependency vulnerabilities flagged by Dependabot or `pip-audit`.

Out of scope:

- Vulnerabilities in the upstream MCP servers (`personal-gmail-mcp`, `personal-outlook-mcp`,
  `google-messages-app`, `whatsapp-mcp`) — report those on their own repos.
- Vulnerabilities in Ollama, langchain, or the underlying models — report upstream.
- Risks inherent to giving an LLM agent access to your mailbox (see threat model).

## Threat model notes

- **Your config is personal and gitignored.** `config.yaml` and `config/profile.yaml`
  hold your real email addresses, the people you correspond with, and local paths. Only the
  `*.example.yaml` templates are committed. Never commit the real files.
- **Local data lives outside the repo.** The SQLite store (`state/garvis.db`), digests,
  and logs contain your message text in plaintext and are gitignored. Protect the host.
- **Agentic actions are gated.** Deletes are recoverable (~30 days) and `dry_run: true`
  is the default — nothing is deleted until you flip it off and trust the classification.
  Voice send is off by default (`allow_voice_send: false`); enabling it lets spoken input
  send email outward.
- **Untrusted content reaches the model.** Message bodies can attempt prompt injection.
  Deterministic guards (`garvis/guards.py`) protect sensitive categories regardless of how
  the LLM classifies them, and one-time codes are protected by a time rule. Treat any
  model-proposed action as advisory, not authoritative.
- **Edge-only by design.** Garvis runs against a local Ollama model; message content is not
  sent to a third-party LLM API.

## Supported versions

Only the latest commit on `main` is supported. There are no maintained release branches.
