# Third-party notices

The root MIT license applies to original project code. It does not relicense
models, dependencies, datasets, card artwork or trademarks. Installing or
redistributing the combined application requires respecting their licenses too;
a source checkout with an MIT license is not a claim that every dependency or
packaged APK is MIT-only.

| Component | Upstream / license |
| --- | --- |
| DINOv2 Small | [Meta model card](https://huggingface.co/facebook/dinov2-small), Apache-2.0 |
| Card-corner YOLO weights | [duclvQ/tcg-card-detector](https://huggingface.co/duclvQ/tcg-card-detector), AGPL-3.0 |
| Ultralytics | [source and license](https://github.com/ultralytics/ultralytics), AGPL-3.0 or upstream commercial terms |
| RF-DETR Small | [Roboflow](https://github.com/roboflow/rf-detr), Apache-2.0 for this model family |
| PaddleOCR / PaddlePaddle | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache-2.0 |
| FAISS | [Meta](https://github.com/facebookresearch/faiss), MIT |
| ONNX Runtime Android | [Microsoft](https://github.com/microsoft/onnxruntime), MIT |
| TCGdex | [project](https://github.com/tcgdex/cards-database); upstream data/API terms apply |

Android also depends on Kotlin, AndroidX/Compose/CameraX and OkHttp; the backend
uses PyTorch, Transformers, Pillow, NumPy, FastAPI and other packages. Preserve
upstream notices when distributing their binaries. Gradle's wrapper is distributed
under the upstream Gradle license, Apache-2.0.

Model provenance, immutable upstream revisions and exported-model hashes are
recorded in [model setup](docs/MODELS.md) and
`mobile/android/app/src/main/assets/card_pose_yolov8n.json`.
The project's fine-tuned collector checkpoint is a separate local artifact;
its training-data provenance and distribution terms must accompany a future
model release. It is not included in the MIT source archive.

Pokémon card names, artwork and trademarks belong to their respective owners.
This project is not affiliated with or endorsed by The Pokémon Company,
Nintendo, Game Freak or Creatures. Personal scan photos are excluded from source
bundles. Review the AGPL dependencies and model notices before publishing a
combined executable or APK; copying only this MIT license is insufficient.
