# Changelog

## 0.2.1

- Keep inline HTML tags from splitting text: `<span>83</span><span>9271</span>` and keywords split across tags are now seen whole by redaction and the local-only gate.
- Check full-width letters and soft-hyphen/invisible-character variants of credential keywords (NFKC on the detection copy only).
- Scan the HTML alternative of `multipart/alternative` mail for the local-only gate, not just the plain-text part.
- Purge raw source of local-only results after classification even when mark-read is enabled; they are deliberately kept unread, so no receipt needs it.

## 0.2.0

- Preserve the revised privacy fixes: raw-retention safeguards, expanded private-context blocking, credential fingerprints and terminal delivery deduplication.
- Add three selectable HTML templates: reading, compact and print, shared across archives, email, Telegram and SSH delivery.
- Freeze presentation selection in version-2 report packets; keep version-1 packets readable using their original renderer.
- Add offline comparison samples and local-only reformatting of sanitized report JSON without reclassification or delivery.
- Restore the approved reading controls under the fixed script-hash CSP when served by the local dashboard.

## 0.1.0

- Extract a standalone, configurable project from a local Apple Mail prototype.
- Preserve local classification, conservative redaction, content deduplication and guarded optional mark-read receipts.
- Add local HTML/Markdown/JSON reports with expandable topics and configurable schedules.
- Make archive, TLS email, direct Telegram bot, personal Saved Messages and SSH relay interchangeable.
- Default to local-only behavior, with environment/keychain credential references and durable delivery receipts.
- Add synthetic regression tests, macOS native guard tests, CI and source/history/archive privacy auditing.
