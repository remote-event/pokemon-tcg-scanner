# Web collections

Static HTML, CSS and JavaScript served by the desktop server at
`http://<desktop-ip>:8765/`. No frontend build step is required.

- `index.html`: collection navigation, forms and scan details.
- `gallery.js`: API requests, live updates and collection management.
- `gallery.css`: responsive layout and styling.
- `tests/collections.mjs`: Playwright collection workflow checks.

Start from the repository root with `bash start-server.sh`.
Create/select a collection before scanning. The interface supports editing,
moving and deleting scans, searching, bulk actions and **Export ZIP**.
Ambiguous live scans appear in a confirmation panel with the original crop and
printing candidates. Choose a candidate to save it, or skip without saving.

## Export format

Each download contains `collection.json` and `images/`. The JSON's `cards`
array contains one entry per scan, including `name`, `set`, `number` and
`image_path` relative to the extracted ZIP. Card numbers remain strings.
The manifest also includes collection metadata, export time, scan/card IDs,
set code/size, scan timestamp and verification status.

The export contains every scan in the collection regardless of pagination,
search or selection. Shared photos are stored once. Missing photos are
represented by `image_path: null` and counted by `missing_image_count`.
Photos are saved scan crops, not thumbnails or catalog reference images.
There is currently no ZIP import action.

## Browser validation

Install Playwright in your test environment, then run:

```bash
node web/tests/collections.mjs http://<desktop-ip>:8765
node web/tests/confirmation.mjs http://<desktop-ip>:8765
```

If Playwright is installed elsewhere, set `PLAYWRIGHT_MODULE` to its module
path. `CHROMIUM_PATH` can select an existing Chromium executable.
The test uses temporary collections and cleans them up. A running desktop
server is required; the test does not start inference itself.
