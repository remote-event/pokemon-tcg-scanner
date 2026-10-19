# Release procedure

The repository supports installation from a Git clone with Git LFS. It includes
the collector-number checkpoint and labeled dataset. The **Android APK** workflow
builds signed APKs with the phone detector included.

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
6. Push a version tag as described below. The workflow publishes the verified APK,
   corresponding source bundle, checksums, and installation notes to GitHub Releases.

Do not upload an entire workstation folder or the compile-only APK from **Checks**.
The separate **Android APK** workflow prepares and verifies the model before
publishing its artifact. Keep the application ID and signing key stable.


## Android APK workflow

The workflow runs on relevant pushes to `main`, version tags, and through
**Actions → Android APK → Run workflow**. It exports only the phone detector, runs Android unit tests, builds
an unsigned release, aligns and signs it, verifies its signature and packaged
model hash, and uploads a 90-day artifact with source and notices. It does not
start a scanner server, install anything on a phone, or publish a Play Store app.
Version tag pushes also publish these downloads to GitHub Releases without an
expiration date. The APK build job has read-only repository permissions; only
the separate release job can write a release.
Pull requests never receive signing secrets or publish signed APKs.

For your own fork, create a dedicated release key with JDK `keytool`. Run this
outside the repository and choose a strong password when prompted:

```text
keytool -genkeypair -keystore scanner-release.p12 -storetype PKCS12 -alias scanner -keyalg RSA -keysize 4096 -validity 10000
```

Keep the same key password as the keystore password. Back up the keystore and its
password securely; GitHub secrets cannot be downloaded to recover a lost key.
Never commit the key, use the shared Android debug key, or rotate it between builds.

Add these repository Actions secrets through GitHub Settings or `gh secret set`:

| Secret | Value |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | Base64 encoding of the complete PKCS12 keystore |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore/key password |

The alias must be `scanner`. The workflow exposes secrets only to its configuration
check and signing step, removes the temporary key afterward, and uploads only
`dist/release/`. A fork without secrets fails early with a configuration message.

Workflow version codes are `1000 + github.run_number`; local debug builds retain
their development version code. Preserve the workflow's run sequence, or raise
that offset when replacing the workflow, so published version codes keep increasing.
The public signing fingerprint is in `signing-certificate.txt` alongside the APK.
The APK and source bundle include the model's AGPL license and provenance;
original code remains MIT. Source assets and the export implementation are linked
from `SOURCES.md` in the download.


## Publish a version

After the commit passes **Checks**, tag that exact commit using `vMAJOR.MINOR.PATCH`:

```bash
git tag -a v1.1.5 -m "Release 1.1.5"
git push origin v1.1.5
```

Use a new version for each release; do not move existing release tags. The tag's
version becomes the APK version name (`1.1.5` in this example). Its version code
still follows the workflow run number. Only stable numeric version tags are
supported; other `v*` tags fail validation before building.

The **Android APK** workflow tests, builds, signs, and verifies the APK, then
creates a draft release, uploads all assets, and publishes it as the latest release.
Main-branch and manual runs produce Actions artifacts without publishing a release.
If uploading fails after draft creation, remove the incomplete draft before
rerunning the failed release job. Published releases are never overwritten by a rerun.

Release assets:

- `pokemon-tcg-scanner.apk`: directly installable Android app.
- `pokemon-tcg-scanner-sources.zip`: matching source archive, original phone
  checkpoint, provenance, and license notices.
- `SHA256SUMS`: SHA-256 checksums for all three downloadable files.
- `signing-certificate.txt`: APK signature verification and public fingerprint.

The README links to `releases/latest/download/pokemon-tcg-scanner.apk`, so it
always downloads the latest stable APK without requiring a GitHub account.
Server installation still uses Git LFS to obtain its model and training images.
