# Release procedure

The repository supports installation from a Git clone with Git LFS. It includes
the collector-number checkpoint and labeled dataset; no public signed Android
APK is configured here.

1. Run the checks in CONTRIBUTING.md and verify a new installation using the
   documented supported Python/runtime versions.
2. Review `git status`. Keep personal scans, `data/`, environments, `artifacts/`,
   local settings and signing material out of commits. Only the explicitly
   published model and dataset belong in Git LFS.
3. Run `git lfs fsck` and verify the model SHA-256 against its metadata. For dataset
   updates, validate image/annotation references and keep related cards together
   across train/validation/test splits. Update model and dataset documentation.
4. Push the commit and LFS objects, then test `git lfs pull` from a fresh clone.
   GitHub archives can contain LFS pointers, so the installation guide uses a clone.
   `python3 scripts/package_source.py` produces a source-only archive, excluding
   binary weights and dataset photos; it is not a complete model distribution.
5. Before publishing an APK, prepare its ONNX asset, preserve third-party notices,
   resolve the AGPL distribution obligations, and configure a maintainer-owned
   release signing key. Keep the signing key out of the repository and CI logs.
6. Tag the reviewed version and upload only intended artifacts. Record checksums
   and supported platform requirements in the release notes.

Do not upload an entire workstation folder or a debug build produced by CI: CI
only checks compilation and deliberately lacks the phone detector. Keep the app
ID stable so future signed updates can preserve installed users' data.
