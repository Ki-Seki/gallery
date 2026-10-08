#!/usr/bin/env python3
"""Optimize photos in place, build thumbnails, and rewrite photos.json.

Drop originals (JPEG / PNG / HEIC / WebP / TIFF / AVIF) into photos/ — or the
repo root, they get moved in. Every run:

  * auto-rotates, downsizes to MAX_EDGE and re-encodes as progressive JPEG,
    keeping only capture date and camera (GPS and everything else is dropped)
  * writes a WebP thumbnail per photo into thumbs/
  * rewrites photos.json, which the page reads

Processed files carry a JPEG comment marker and are skipped next time, so the
script is safe to run on every push.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # HEIC support is optional locally
    pass

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "photos"
THUMBS = ROOT / "thumbs"
MANIFEST = ROOT / "photos.json"

MAX_EDGE = 2400
QUALITY = 82
THUMB_EDGE = 960
THUMB_QUALITY = 74
MARKER = b"kiseki-gallery"

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".avif"}

EXIF_IFD = 0x8769
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003

Image.MAX_IMAGE_PIXELS = None  # our own photos, not untrusted uploads


def is_image(p: Path) -> bool:
    return p.is_file() and not p.name.startswith(".") and p.suffix.lower() in EXTS


def is_done(p: Path) -> bool:
    if p.suffix != ".jpg":
        return False
    try:
        with Image.open(p) as im:
            return im.info.get("comment") == MARKER
    except Exception:
        return False


def slug(stem: str) -> str:
    return re.sub(r"[\s#?%&+/\\]+", "-", stem).strip("-.") or "photo"


def taken_at(exif: Image.Exif) -> str | None:
    raw = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME)
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    m = re.match(r"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(raw or ""))
    if not m or m.group(1) == "0000":
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def slim_exif(exif: Image.Exif) -> Image.Exif:
    out = Image.Exif()
    for tag in (TAG_MAKE, TAG_MODEL, TAG_DATETIME):
        if tag in exif:
            out[tag] = exif[tag]
    original = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL)
    if original:
        out.get_ifd(EXIF_IFD)[TAG_DATETIME_ORIGINAL] = original
    return out


def to_rgb(im: Image.Image) -> tuple[Image.Image, bool]:
    """Flatten transparency onto white. Returns (image, icc_still_valid)."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        return bg, True
    if im.mode == "CMYK":
        return im.convert("RGB"), False
    if im.mode != "RGB":
        return im.convert("RGB"), im.mode in ("L", "P")
    return im, True


def optimize(job: tuple[str, str]) -> tuple[str, int, int]:
    src, dst = Path(job[0]), Path(job[1])
    before = src.stat().st_size
    with Image.open(src) as im:
        exif = im.getexif()
        icc = im.info.get("icc_profile")
        if im.format == "JPEG":
            im.draft("RGB", (MAX_EDGE, MAX_EDGE))  # fast DCT downscale for huge JPEGs
        im = ImageOps.exif_transpose(im)
        im, icc_ok = to_rgb(im)
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        tmp = dst.with_name(f".{dst.name}.tmp")
        im.save(
            tmp,
            "JPEG",
            quality=QUALITY,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
            icc_profile=icc if icc_ok else None,
            exif=slim_exif(exif).tobytes(),
            comment=MARKER,
        )
    src.unlink()
    tmp.replace(dst)
    return dst.name, before, dst.stat().st_size


def make_thumb(name: str) -> dict:
    photo = PHOTOS / name
    with Image.open(photo) as im:
        exif = im.getexif()
        icc = im.info.get("icc_profile")
        w, h = im.size
        t = im.convert("RGB")
        t.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
    THUMBS.mkdir(exist_ok=True)
    t.save(THUMBS / f"{photo.stem}.webp", "WEBP", quality=THUMB_QUALITY, method=6, icc_profile=icc)
    r, g, b = t.resize((1, 1), Image.BOX).getpixel((0, 0))
    return {
        "w": w,
        "h": h,
        "tw": t.width,
        "th": t.height,
        "color": f"#{r:02x}{g:02x}{b:02x}",
        "taken": taken_at(exif),
    }


def file_hash(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def main() -> int:
    PHOTOS.mkdir(exist_ok=True)

    # Photos uploaded to the repo root by mistake go into photos/.
    for p in sorted(ROOT.iterdir()):
        if is_image(p):
            target = PHOTOS / p.name
            if not target.exists():
                p.rename(target)
                print(f"moved {p.name} -> photos/")

    files = sorted(p for p in PHOTOS.iterdir() if is_image(p))
    todo = [p for p in files if not is_done(p)]

    # Pick destination names up front so parallel workers never collide.
    names = Counter(p.name.lower() for p in files)
    jobs = []
    for src in todo:
        base, n = slug(src.stem), 1
        cand = f"{base}.jpg"
        while cand.lower() in names and not (
            cand.lower() == src.name.lower() and names[cand.lower()] == 1
        ):
            n += 1
            cand = f"{base}-{n}.jpg"
        names[cand.lower()] += cand.lower() != src.name.lower()
        jobs.append((str(src), str(PHOTOS / cand)))

    failed = 0
    if jobs:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            futures = {pool.submit(optimize, j): j for j in jobs}
            for fut, job in futures.items():
                try:
                    name, before, after = fut.result()
                    print(f"optimized {Path(job[0]).name} -> {name}  {mb(before)} -> {mb(after)}")
                except Exception as e:  # keep going; one bad file shouldn't block the site
                    failed += 1
                    print(f"::warning file=photos/{Path(job[0]).name}::could not process: {e}")

    old = {}
    if MANIFEST.exists():
        try:
            old = {e["src"]: e for e in json.loads(MANIFEST.read_text())}
        except Exception:
            pass

    photos = sorted(p for p in PHOTOS.iterdir() if p.is_file() and is_done(p))
    stale = []
    entries = {}
    for p in photos:
        src = f"photos/{p.name}"
        v = file_hash(p)
        prev = old.get(src)
        if prev and prev.get("v") == v and (THUMBS / f"{p.stem}.webp").exists():
            entries[p.name] = prev
        else:
            stale.append(p.name)

    if stale:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            for name, meta in zip(stale, pool.map(make_thumb, stale)):
                p = PHOTOS / name
                entries[name] = {
                    "id": p.stem,
                    "src": f"photos/{name}",
                    "thumb": f"thumbs/{p.stem}.webp",
                    **meta,
                    "v": file_hash(p),
                }
        print(f"thumbnails: {len(stale)} written")

    keep = {f"{Path(n).stem}.webp" for n in entries}
    if THUMBS.exists():
        for t in THUMBS.iterdir():
            if t.suffix == ".webp" and t.name not in keep:
                t.unlink()
                print(f"removed orphan thumbs/{t.name}")

    manifest = sorted(entries.values(), key=lambda e: (e.get("taken") or "", e["id"]), reverse=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    total = sum((PHOTOS / Path(e["src"]).name).stat().st_size for e in manifest)
    print(f"{len(manifest)} photos, {mb(total)} total" + (f", {failed} failed" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
