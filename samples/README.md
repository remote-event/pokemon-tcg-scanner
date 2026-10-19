# Local sample photos

This directory may contain personal replay photos (`cards/`) and labels
(`cards.json`). Both are ignored by Git and omitted from source bundles.
Benchmarks now require an explicit `--manifest`; see desktop/README.md for its
format. They never depend on this folder being present in a new checkout.

Real Android detector tests similarly use two local fixtures under
mobile/android/app/src/androidTest/assets/. Provide your own card-a.jpg and
card-b.jpg when running those optional physical-device tests.
