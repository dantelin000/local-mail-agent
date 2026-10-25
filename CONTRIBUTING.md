# Contributing

Use Python 3.11+ and a virtual environment. Install `.[dev,telegram]`, then run the unit tests and release audit from the README. CI must not require personal credentials, live mailboxes or public-network test calls.

Keep changes to collection, privacy checks, reporting and delivery separated. New delivery drivers must preserve explicit opt-in, fixed configured destinations, immutable payloads, safe reason codes and uncertainty handling. Tests should cover meaningful failure boundaries rather than mirror implementation.

Use reserved `.test`/`.invalid` domains and synthetic data. Never paste real report content, account names, API responses or session files. Before submitting, inspect the staged diff, Git history and built archives. A masked secret that remains in Git history is still a leak.

The native bridge is built locally with Xcode Command Line Tools and ad-hoc signing. Do not submit your built `.app`, system consent databases or personal launch manifest.

Pull requests should describe the behavior change, validation and any relevant privacy boundary. MIT-licensed contributions are welcome; third-party dependencies must remain optional when their feature is optional.
