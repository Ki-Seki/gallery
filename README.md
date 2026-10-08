# The Kiseki Gallery

A shuffled pile of photo prints. Drag the top one away to see the next, click it to look closer.

https://ki-seki.github.io/gallery/

## Adding photos

1. On GitHub, open [`photos/`](photos) → **Add file → Upload files**, drop the photos in, commit.
2. That's it. The workflow then:
   - turns each photo upright, scales the long edge down to 2400 px and re-encodes it as a progressive JPEG, keeping its EXIF (camera, lens, exposure, date, GPS)
   - removes duplicates: a photo that looks the same as one already in the gallery (re-upload, other name, HEIC vs JPEG, recompressed export, light edit) is deleted, and the copy that was there first stays
   - writes a 960 px WebP thumbnail to `thumbs/`
   - rebuilds `photos.json`, commits the result back, and deploys to GitHub Pages

JPEG, PNG, HEIC, WebP, TIFF and AVIF all work. Photos uploaded to the repo root by mistake get moved into `photos/`.

**Removing a photo**: delete it from `photos/`; its thumbnail and manifest entry go with it.

> The web uploader takes files up to 25 MB. For bigger ones (a 40 MB edit export, say), use `git push` (100 MB limit), or run the script locally first so the full-size original never enters git history.

## In the viewer

- `prev` / `next`, arrow keys, swipe, or click the left / right half of the photo
- zoom with the scroll wheel, a trackpad or touch pinch, a double click / double tap, `+` / `-` / `0`, or the `－ ＋` buttons; drag to pan
- `info` (or `i`) shows the EXIF details and a map link when the photo has a location

## Local preview

```bash
pip install -r scripts/requirements.txt
python scripts/process.py
python -m http.server 4173
```

Then open http://localhost:4173.

## Layout

```
index.html, assets/   the page (static, no build step)
photos/               photos (upload here; optimized in place)
thumbs/               generated thumbnails
photos.json           generated manifest
scripts/process.py    optimize, dedupe, thumbnail, manifest
.github/workflows/    run the script, commit, deploy
```

Tunables (`MAX_EDGE`, `QUALITY`, `THUMB_EDGE`, `DUP_BITS`, …) sit at the top of `scripts/process.py`.
