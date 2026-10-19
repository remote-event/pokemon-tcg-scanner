# Android third-party notices

The Android app uses Kotlin, AndroidX/Jetpack Compose/CameraX, Kotlin coroutines,
OkHttp and ONNX Runtime Android. Their licenses and notices apply to distribution.

A working APK **does contain** the generated card-corner ONNX model. It is exported
from duclvQ/tcg-card-detector, whose model card declares AGPL-3.0; the project MIT
license does not replace that license. The ONNX export does not change the model's
license. Source archives omit the generated weights and provide the export script.

See [repository notices](../../THIRD_PARTY_NOTICES.md),
`app/src/main/assets/MODEL_NOTICES.txt`, and
`app/src/main/assets/card_pose_yolov8n.json` for attribution and hashes.
