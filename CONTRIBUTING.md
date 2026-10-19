# Contributing

Use Python 3.11/3.12 and keep runtime dependencies separate from RF-DETR training
and PaddleOCR. Follow the root README to set up an installation. Workstation
settings belong in ignored `desktop/config.local.toml`, never shared defaults.

Before submitting a change:

```bash
.venv/bin/python -m pip install -r desktop/requirements/requirements-dev.txt
(cd desktop && ../.venv/bin/python -B -m unittest discover -s tests)
.venv/bin/python -m ruff check desktop scripts training
.venv/bin/python -B -m unittest discover -s training/collector_numbers -p 'test_*.py'
bash mobile/build.sh testDebugUnitTest assembleDebug  # for Android changes
```

Backend regression tests use temporary storage, fake model outputs and local
image fixtures generated in code. They must not download models or depend on a
maintainer's catalog. Real-model benchmarks are opt-in tools with explicit input
paths. Browser and phone collection integration tests require an isolated test
server; they change its active collection temporarily.

Preserve collection migrations, leading zeros in collector numbers, exact FAISS
scores, and the distinction between uncertain candidates and automatically saved
cards. Add regression coverage for changes to matching, storage and protocol
behavior. Avoid tests that merely restate an implementation or require a GPU in CI.

Do not commit scan databases/photos, model weights, local settings, signing keys,
training runs or logs. New public assets need provenance and license information.
Describe the user-visible problem, final behavior and relevant validation in PRs.
