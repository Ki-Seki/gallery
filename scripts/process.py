#!/usr/bin/env python3
"""Optimize photos in place, drop duplicates, build thumbnails, write photos.json.

Drop originals (JPEG / PNG / HEIC / WebP / TIFF / AVIF) into photos/ — or the
repo root, they get moved in. Every run:

  * auto-rotates, downsizes to MAX_EDGE and re-encodes as progressive JPEG,
    keeping the EXIF data (camera, exposure, date, location)
  * removes duplicates: a photo that looks the same as one already in the
    gallery (re-upload, other name, other format, recompressed) is deleted,
    and the copy that was there first wins
  * writes a WebP thumbnail per photo into thumbs/
  * rewrites photos.json, which the page reads

Processed files carry a JPEG comment marker and are skipped next time, so the
script is safe to run on every push.
"""

from __future__ import annotations

import hashlib
import json
import math
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
DUP_BITS = 12  # max differing bits (of 256) for two photos to count as the same
MARKER = b"kiseki-gallery"
SCHEMA = 2  # bump when manifest entries gain fields, to rebuild them

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".avif"}

TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_ORIENTATION = 0x0112
TAG_DATETIME = 0x0132
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_EXPOSURE = 0x829A
TAG_FNUMBER = 0x829D
TAG_ISO = 0x8827
TAG_DATETIME_ORIGINAL = 0x9003
TAG_BIAS = 0x9204
TAG_FOCAL = 0x920A
TAG_MAKERNOTE = 0x927C
TAG_PIXEL_X = 0xA002
TAG_PIXEL_Y = 0xA003
TAG_FOCAL_35 = 0xA405
TAG_LENS = 0xA434

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


# --- EXIF -------------------------------------------------------------------


def text(v) -> str:
    if isinstance(v, bytes):
        v = v.decode(errors="ignore")
    return "" if v is None else str(v).replace("\x00", "").strip()


def number(v, digits: int = 2) -> float | None:
    try:
        f = float(v[0] if isinstance(v, tuple) else v)
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return round(f, digits) if digits else round(f)


def degrees(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
    except Exception:
        return None
    v = d + m / 60 + s / 3600
    return -v if text(ref).upper() in ("S", "W") else v


def taken_at(exif: Image.Exif) -> str | None:
    raw = text(exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME))
    m = re.match(r"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", raw)
    if not m or m.group(1) == "0000":
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def summarize(exif: Image.Exif) -> dict:
    """The bits of EXIF the page shows in its info panel."""
    sub = exif.get_ifd(EXIF_IFD)
    gps = exif.get_ifd(GPS_IFD)
    make, model = text(exif.get(TAG_MAKE)), text(exif.get(TAG_MODEL))
    out = {
        "camera": (model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()) or None,
        "lens": text(sub.get(TAG_LENS)) or None,
        "focal": number(sub.get(TAG_FOCAL), 1),
        "focal35": number(sub.get(TAG_FOCAL_35), 0),
        "fnumber": number(sub.get(TAG_FNUMBER)),
        "exposure": number(sub.get(TAG_EXPOSURE), 6),
        "iso": number(sub.get(TAG_ISO), 0),
    }
    try:
        bias = round(float(sub.get(TAG_BIAS)), 2)
        if bias and math.isfinite(bias):
            out["bias"] = bias
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    lat, lon = degrees(gps.get(2), gps.get(1)), degrees(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and (lat or lon):
        out["gps"] = [round(lat, 6), round(lon, 6)]
    return {k: v for k, v in out.items() if v is not None}


def slim_exif(exif: Image.Exif) -> Image.Exif:
    out = Image.Exif()
    for tag in (TAG_MAKE, TAG_MODEL, TAG_DATETIME):
        if tag in exif:
            out[tag] = exif[tag]
    original = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL)
    if original:
        out.get_ifd(EXIF_IFD)[TAG_DATETIME_ORIGINAL] = original
    return out


def exif_bytes(exif: Image.Exif, size: tuple[int, int]) -> bytes:
    exif.pop(TAG_ORIENTATION, None)  # pixels are upright now
    sub = exif.get_ifd(EXIF_IFD)
    sub.pop(TAG_MAKERNOTE, None)  # vendor blob: large, and useless here
    if sub:
        sub[TAG_PIXEL_X], sub[TAG_PIXEL_Y] = size
    try:
        data = exif.tobytes()
        if len(data) < 60_000:  # must fit one JPEG APP1 segment
            return data
    except Exception:
        pass
    return slim_exif(exif).tobytes()


# --- images -----------------------------------------------------------------


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


def optimize(job: tuple[str, str]) -> tuple[str, int, int, int]:
    src, dst = Path(job[0]), Path(job[1])
    before = src.stat().st_size
    with Image.open(src) as im:
        exif = im.getexif()
        icc = im.info.get("icc_profile")
        pixels = im.width * im.height
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
            exif=exif_bytes(exif, im.size),
            comment=MARKER,
        )
    src.unlink()
    tmp.replace(dst)
    return dst.name, before, dst.stat().st_size, pixels


def dhash(im: Image.Image) -> str:
    """256-bit difference hash: survives resizing, recompression and format changes."""
    g = im.convert("L").resize((17, 16), Image.BOX)
    px = g.load()
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[x + 1, y] > px[x, y])
    return f"{bits:064x}"


def same_photo(a: dict, b: dict) -> bool:
    ra, rb = a["w"] / a["h"], b["w"] / b["h"]
    if abs(ra - rb) > 0.02 * rb:
        return False
    return (int(a["dhash"], 16) ^ int(b["dhash"], 16)).bit_count() <= DUP_BITS


def describe(name: str) -> dict:
    """Write the thumbnail and return the photo's manifest entry."""
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
        "id": photo.stem,
        "src": f"photos/{name}",
        "thumb": f"thumbs/{photo.stem}.webp",
        "w": w,
        "h": h,
        "tw": t.width,
        "th": t.height,
        "bytes": photo.stat().st_size,
        "color": f"#{r:02x}{g:02x}{b:02x}",
        "taken": taken_at(exif),
        "exif": summarize(exif),
        "dhash": dhash(t),
        "v": file_hash(photo),
        "schema": SCHEMA,
    }


def file_hash(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


# --- main -------------------------------------------------------------------


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
    fresh: dict[str, int] = {}  # newly optimized photo -> original pixel count
    if jobs:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            futures = {pool.submit(optimize, j): j for j in jobs}
            for fut, job in futures.items():
                try:
                    name, before, after, pixels = fut.result()
                    fresh[name] = pixels
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

    entries: dict[str, dict] = {}
    stale = []
    for p in sorted(p for p in PHOTOS.iterdir() if p.is_file() and is_done(p)):
        prev = old.get(f"photos/{p.name}")
        if (
            prev
            and prev.get("schema") == SCHEMA
            and prev.get("v") == file_hash(p)
            and (THUMBS / f"{p.stem}.webp").exists()
        ):
            entries[p.name] = prev
        else:
            stale.append(p.name)

    if stale:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
            for name, entry in zip(stale, pool.map(describe, stale)):
                entries[name] = entry
        print(f"thumbnails: {len(stale)} written")

    # Duplicates: photos already in the gallery win, then the biggest original.
    kept: list[dict] = []
    order = sorted(entries, key=lambda n: (f"photos/{n}" not in old, -fresh.get(n, 0), n))
    for name in order:
        e = entries[name]
        twin = next((k for k in kept if same_photo(e, k)), None)
        if twin is None:
            kept.append(e)
            continue
        (PHOTOS / name).unlink()
        del entries[name]
        print(f"::notice file=photos/{name}::duplicate of {twin['src']}, removed")

    keep = {f"{Path(n).stem}.webp" for n in entries}
    if THUMBS.exists():
        for t in THUMBS.iterdir():
            if t.suffix == ".webp" and t.name not in keep:
                t.unlink()
                print(f"removed thumbs/{t.name}")

    manifest = sorted(entries.values(), key=lambda e: (e.get("taken") or "", e["id"]), reverse=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    total = sum(e["bytes"] for e in manifest)
    print(f"{len(manifest)} photos, {mb(total)} total" + (f", {failed} failed" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
