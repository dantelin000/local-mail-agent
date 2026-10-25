# Security and privacy

## Release contents

This repository contains program source, synthetic tests and placeholder configuration only. Runtime data is stored outside the source tree. `.gitignore` and the source manifest exclude mail exports, report archives, databases, keys, Telegram sessions, private settings and logs. Do not rely on ignore rules alone: run `scripts/audit_release.py` on Git history and the actual archives before publishing.

The audit reports positions and categories without printing matched secrets. It catches common tokens, private keys, personal absolute paths, non-example email addresses, production host references and accidental runtime files. It is not an exhaustive PII or secret detector. Review configuration, sample material, commit authors and generated build metadata as well.

Source tarballs can inherit the build machine's user/group ownership names. Run `scripts/normalize_archives.py` on each tarball before auditing or distributing it. The audit rejects nonanonymous ownership and unexpected extended metadata. Git commit and annotated-tag metadata are checked too.

## Runtime boundaries

- Apple Mail owns mailbox authentication. This application does not request mailbox passwords. Live access is authorized through macOS automation consent to a separate native App.
- Default policy: no source-mail writes, no cloud summaries, no external delivery. Optional mark-read writes require successful persisted classification plus identity and full MIME body checks. No source-mail sending, deleting or moving is implemented.
- Mail, model output and report input are untrusted data. The local model receives redacted text, has no tools and cannot select delivery destinations. Invalid schema, uncertain output, attachments and sensitive content fail closed to local review.
- The Ollama endpoint is restricted to loopback and configured local tags. Remote-backed model metadata is rejected. This assumes the local Ollama service itself is trustworthy.
- The review dashboard binds loopback only, checks Host/Origin and CSRF on writes, and escapes displayed mail text. Other processes running as the same local user remain within the local trust boundary.
- HTML attachments escape untrusted text and contain no external scripts, images, fonts or network calls.
- Credentials are environment references or optional OS keychain entries. Scheduled Telegram personal mode requires a pre-existing session and sends only to Saved Messages. Bot destinations and SMTP recipients come from explicit configuration.
- Report forwarding discloses sanitized report text and, if enabled, the user's own receiving addresses to the chosen delivery provider and any configured relay. Bots and Saved Messages use Telegram cloud storage; this is not secret-chat encryption.
- Runtime directories use700 and files600 where supported. SQLite, raw mail and Telegram session contents are not encrypted by this program; use OS account/device protections for data at rest.
- A durable ledger prevents ordinary duplicate sends. Non-idempotent sends with uncertain outcomes stop for human review; the program does not promise exactly-once delivery, provider-side synchronization freshness or that a human has read the report.

## Reporting an issue

Do not attach real mail, sessions, tokens, passwords, private configuration or unredacted provider errors to a public issue. Share a minimal synthetic reproduction and the fixed reason code. If a future hosted repository supports private security advisories, use that private channel for sensitive reports.

## Validation limits

Tests use synthetic mail, local HTTP boundaries and mocked SMTP/Telegram/SSH interfaces. Native identity/body tests compile on macOS without accessing Mail. A new user's real mailbox, provider settings, Telegram login, macOS version and end-device reading experience still need their own acceptance test. Regex-based redaction is conservative and may hide useful public information; it does not prove perfect anonymization.
