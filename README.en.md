# Local Mail Agent

Local-first email triage, private HTML digests, and interchangeable delivery channels.

[中文说明](README.md) · [Security](SECURITY.md) · [Configuration](docs/configuration.md)

Apple Mail handles account login and mail synchronization. An installed local Ollama model classifies and redacts cached inbox messages. The default report arranges those summaries locally, with no cloud summarization. EML/mbox import works on macOS and Linux; live Apple Mail collection requires macOS.

Delivery choices: local Markdown/HTML/JSON archives, SMTP over TLS, a Telegram bot running directly on your computer, personal Telegram Saved Messages through an optional Telethon login, or an optional SSH relay. No particular server, bot, vendor or Hermes installation is required.

## Quick start

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
local-mail-agent init --source eml
local-mail-agent demo
```

The offline demo uses synthetic data and never opens a mailbox, invokes a model, or sends anything. The command prints a self-contained HTML path.

Choose one of three report templates with `[report] template = "reading"`: colorful, expandable `reading` (default), dense `compact`, or fully expanded `print` for archiving and printing. Run `local-mail-agent demo --all-templates` or open the [included synthetic samples](examples/reports/index.html). `init --template compact` selects a default for a new configuration.

Each generated packet freezes its template for every delivery channel. Changing configuration affects future reports and never automatically resends completed deliveries. Reformat an existing sanitized JSON locally with `local-mail-agent render report.json --template print --output report-print.html`; this does not read mail, invoke a model, alter the packet, enqueue or send. Legacy version-1 packets keep their original presentation; new version-2 packets require upgrading both ends of an SSH relay. See [template documentation](docs/report-templates.md).

Defaults: UTC, 24-hour collection window, 15-minute collection intervals, digests at 09:00/21:00, local archives only. Marking mail read, cloud summaries, and external delivery are disabled. All are configurable. Use `--config FILE` before the command to select a configuration; see the [full example](src/local_mail_agent/assets/config.example.toml).

For Apple Mail, install Xcode Command Line Tools and Ollama, prepare your chosen local model, then run `apple-mail build` and `apple-mail start`. macOS requests consent for the native App. Default dashboard: `http://127.0.0.1:8766/`. The worker must be running; sleep delays processing. No login item is installed automatically.

## Optional integrations

- Enable a delivery table and `privacy.allow_external_delivery` explicitly before sending reports.
- Credentials are environment-variable references, optionally resolved from the OS keychain; inline secrets are rejected.
- Install `.[telegram]` for personal Saved Messages and run `telegram-login`. Scheduled sends require an existing local session and send only to `me`; they never prompt for login in the background. The session grants account access and must remain private.
- Codex CLI cloud refinement requires explicit opt-in, your own CLI login, and macOS for the additional filesystem boundary. Local mode requires no OpenAI account.
- The tool never replies to, deletes or moves source emails. Optional mark-read writes require persisted clean results and message identity/body verification.

Each report/channel has a durable receipt. Uncertain SMTP/Telegram outcomes stop automatic retries, since the remote service may already have accepted the message. This is duplicate suppression, not a claim of exactly-once delivery or that a human read the report.

## Development

```sh
python -m pip install -e '.[dev,telegram]'
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m build
python scripts/normalize_archives.py dist/*.tar.gz
python scripts/audit_release.py --history dist/*.whl dist/*.tar.gz
```

Tests use synthetic messages and mocked external accounts. Native guard tests run only on macOS and do not access Mail. No real credentials, mailboxes, reports, databases, sessions, model weights, or private Git history are shipped. MIT licensed; alpha software.
