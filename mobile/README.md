# Android client

Native Kotlin and Jetpack Compose UI, CameraX capture, and ONNX Runtime card
cropping. All remaining AI runs on the local backend. Android 8.0+ is supported;
JDK 17 and Android SDK 36 are needed to build.

## Prepare and build

From the repository root, after installing the backend dependencies:

```bash
python scanner.py pip install -r desktop/requirements/requirements-export.txt
python scanner.py models --android
python scanner.py android
adb install -r mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

The export uses the pinned upstream four-corner YOLO model at 480 pixels,
without training. Generated `card_pose_yolov8n.onnx` stays local; source archives
contain the model specification and notices. `mobile/build.sh` refuses to package
an app without the detector. CI compiles without it and does not publish that APK.

Set `JAVA_HOME` and `ANDROID_HOME` to your installed JDK/SDK.
`python scanner.py android` selects the native Windows or Unix Gradle wrapper. For WSL using
Windows Android Studio instead, put the Windows SDK in `android/local.properties`
(ignored by Git), ensure `cmd.exe` is on PATH, and run:

```bash
TCG_WINDOWS_JAVA_HOME='C:\Program Files\Android\Android Studio\jbr' bash mobile/build.sh
```

With Windows adb from WSL, pass its SDK path explicitly, and use a Windows APK
path (`wslpath -w` converts it). Build/install updates with the same signing key
to preserve app data. Do not uninstall as part of an update. Public signed APKs
require a release signing setup; never publish a private keystore or reuse a
shared development signing key.

## Scan

Start the backend and connect the phone to the same private network. Open
**pokemon-tcg-scanner**, grant camera permission and select a collection using
the bottom selector. The settings gear accepts a private desktop IP if mDNS
is unavailable. A confident result saves automatically. Uncertain cards retry,
then show a small tilt hint; optional candidate review is on the web.

The result panel shows the latest card and current collection destination. If
these differ, a separate “Saved in…” label identifies the saved card's collection.
Card information can be hidden without hiding the collection selector.

- **Scan**: first usable frame through the saved result being ready on the phone,
  including stabilization, retries, transport and history fetch.
- **Processing**: server processing of the deciding frame, excluding persistence
  and transport. It can include cached inference.

Timing is retained with the latest local result, not stored for the entire history.
Unavailable historical values are omitted. Pausing, disconnecting, removing a
card or exhausting uncertain retries ends an active timing attempt.

Sound and vibration are controlled by app preferences and Android's system
settings. The chime currently follows normal ringer mode and system volume;
notification vibration can be suppressed by Silent mode. They fire only for a
new saved scan, not every detection or a replayed result.

## Tests

```bash
bash mobile/build.sh testDebugUnitTest assembleDebug assembleDebugAndroidTest
bash mobile/test.sh emulator-5554 ui
bash mobile/test.sh <physical-device-serial> detector
# Explicit integration test; creates and deletes its own temporary collections:
bash mobile/test.sh <physical-device-serial> collections <private-ip>:8765
```

Set `ADB` to an adb executable if it is not on PATH. The test script supports
Windows `adb.exe` paths in WSL. Detector/integration tests need two real card
fixtures named `card-a.jpg` and `card-b.jpg` in
`android/app/src/androidTest/assets/`; these personal photos are not distributed.
UI tests use in-memory fixtures and require no backend.

Use an emulator for Compose UI tests. The current Espresso dependency has an
InputManager compatibility problem on Android 17; that is distinct from the app
runtime. Run native detector tests on a physical ARM64 phone as well: ONNX
Runtime 1.23.2 caused a SIGILL on Snapdragon hardware, fixed by the current
1.25.1 dependency. Do not regress that dependency.

## Source

`ScannerUi.kt` and `ScanMediaUi.kt` render the UI. `ScannerController.kt` owns
state, preferences and collection requests; `LanConnection.kt` handles LAN
transport. `CameraPreview.kt` and `CardDetector.kt` own capture/cropping.
`LibraryStore.kt` supports migration of older installations; the desktop is the
current authoritative collection store. The application ID remains
`dev.tcg.scanner` for update compatibility; the display name is pokemon-tcg-scanner.

See [third-party notices](android/THIRD_PARTY_NOTICES.md) before distributing an APK.
