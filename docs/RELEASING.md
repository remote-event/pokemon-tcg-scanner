# Release procedure

Source preparation does not publish anything. The repository currently supports
installation from a source checkout; there is no hosted collector checkpoint or
public signed Android APK configured here.

1. Run the checks in CONTRIBUTING.md and verify a new installation using the
   documented supported Python/runtime versions.
2. Review `git status` before committing. Never add `data/`, `.venv/`, `.venvs/`,
   `artifacts/`, local configs, personal scans, weights or signing material.
3. Run `python3 scripts/package_source.py`. Inspect the allowlisted archive and
   test it in a fresh directory, without local settings or a private catalog.
4. If offering full OCR, publish the trained collector-number checkpoint as a
   separate release asset with a model card, provenance, license and SHA-256.
   Update docs/MODELS.md with the actual URL. Until then, describe visual-only
   installation and the requirement to supply a trained checkpoint accurately.
5. Before publishing an APK, prepare its ONNX asset, preserve third-party notices,
   resolve the AGPL distribution obligations, and configure a maintainer-owned
   release signing key. The source MIT license does not relicense bundled models.
   Keep the signing key out of the repository and CI logs.
6. Tag the reviewed version and upload only intended source/model/APK artifacts.
   Record checksums and supported platform requirements in the release notes.

Do not upload an entire workstation folder or a debug build produced by CI: CI
only checks compilation and deliberately lacks the phone detector. Keep the app
ID stable so future signed updates can preserve installed users' data.
