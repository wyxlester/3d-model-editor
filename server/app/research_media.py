"""Research photographs and close-ups (contract §12): fetch a real image safely, crop it exactly.

Lester, 29 Oct: research "doesn't render photos and close up for me. the standard … should be like"
the PCG 3-Series dossier, whose close-ups are crops "with no generated detail or image
reconstruction". So:

- `download` fetches a public image the AI found. The AI chooses the URL, and the URL may come from
  a web page, so the fetch is treated as untrusted: http/https only, every host (including each
  redirect) must resolve to public addresses only, at most 3 redirects, at most 15 MB read, and the
  bytes must decode as PNG, JPEG, WebP or GIF.
- `crop` cuts a box out of a stored photo and nothing else: no resize, no sharpening, no filter.

Errors are `MediaError` with a plain sentence the AI can act on. Pillow is imported lazily so a
venv that predates it still starts the server; only these tools fail, saying how to fix it.

Known limit: the host is resolved and checked, then httpx resolves it again to connect. A DNS
server that answers differently the second time (DNS rebinding) could slip past the check; closing
that needs connecting to the checked address with the original SNI, which httpx does not offer
simply. The exposure is a GET whose body must still decode as an image.
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Protocol
from urllib.parse import urljoin, urlsplit

MB = 1024 * 1024
MAX_BYTES = 15 * MB
MAX_REDIRECTS = 3
MIN_CROP_PX = 16
# A decoded image bigger than this is refused rather than decoded (a decompression bomb is a small
# file that expands to gigabytes). 80 MP is far above any news or museum photograph.
MAX_PIXELS = 80_000_000
TIMEOUT_S = 20.0
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36 me3d-research/1")
# Some hosts refuse a browser-like agent from a non-browser and want an honest one with a contact:
# Wikimedia (upload.wikimedia.org) answers 403 "Please respect our robot policy" unless the agent
# names the tool and a URL or email (checked live, 29 Oct 2026). So a 403 is retried once with this.
# ME3D_FETCH_CONTACT overrides the contact; set it empty to never send the second agent.
FETCH_CONTACT = os.environ.get("ME3D_FETCH_CONTACT", "https://github.com/wyxlester/3d-model-editor")
TOOL_USER_AGENT = f"me3d-research/1 (3D model reference research; {FETCH_CONTACT})" if FETCH_CONTACT else None
FORMATS = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"),
           "WEBP": ("image/webp", ".webp"), "GIF": ("image/gif", ".gif")}
EXT_FOR_MIME = {m: e for m, e in FORMATS.values()}
REDIRECTS = {301, 302, 303, 307, 308}

# Keys of the research record that the photo tools own; save_research keeps them as stored.
MEDIA_KEYS = ("photos", "closeups", "coverage", "photo_count", "count_method")
VIEWS = ("port", "starboard", "bow", "stern", "top", "details", "interior")
COVERAGE_VIEWS = ("port", "starboard", "bow", "stern", "top", "details")  # always present (contract §12)
PHOTO_STATUSES = ("baseline", "comparison", "excluded")
MAX_PHOTOS = 60
COUNT_METHOD = ("Distinct downloaded files, de-duplicated by SHA-256. Close-ups are crops and do not count; "
                "republished copies of a photo already added are not added.")


class MediaError(ValueError):
    """The image cannot be used; the message is a sentence telling the AI what to do instead."""


@dataclass
class Image:
    data: bytes
    mime: str
    width: int   # as displayed (after any EXIF rotation), which is how browsers show the file
    height: int
    sha256: str

    @property
    def ext(self) -> str:
        return EXT_FOR_MIME[self.mime]


class Fetched(Protocol):
    status: int
    headers: dict

    def iter_bytes(self) -> Iterable[bytes]: ...

    def close(self) -> None: ...


Fetch = Callable[..., Fetched]  # fetch(url, user_agent=USER_AGENT)
Resolve = Callable[[str], list[str]]


def _pil():
    try:
        from PIL import Image as PILImage, ImageOps
    except ImportError:  # pragma: no cover - depends on the venv
        raise MediaError("The server cannot read images yet: Pillow is not installed. Ask Lester to run "
                         "pip install -r server/requirements.txt in the server's venv.")
    PILImage.MAX_IMAGE_PIXELS = MAX_PIXELS
    return PILImage, ImageOps


# ---- address safety ---------------------------------------------------------------------------

def resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise MediaError(f"The host {host!r} does not exist (DNS lookup failed). Check the image URL.")
    return sorted({info[4][0] for info in infos})


def is_public(addr: str) -> bool:
    """True only for a globally routable unicast address. Private, loopback, link-local (cloud
    metadata at 169.254.169.254), shared CGNAT, reserved, multicast and unspecified all fail, and an
    IPv4 address wrapped in IPv6 is judged as the IPv4 address it wraps."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None:
            ip = inner
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolve: Resolve = resolve_host) -> None:
    """Refuse anything but an http(s) URL whose host resolves to public addresses only."""
    if not isinstance(url, str) or not url.strip():
        raise MediaError("image_url is empty. Give the direct URL of the image file.")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise MediaError(f"Only http and https image URLs can be fetched, not {parts.scheme or 'no scheme'!r}. "
                         "Give the image's web address.")
    host = parts.hostname
    if not host:
        raise MediaError("That URL has no host. Give the full image URL, starting with https://.")
    if parts.username or parts.password:
        raise MediaError("Image URLs with a user name or password are not fetched. Find a public URL.")
    try:
        literal = [str(ipaddress.ip_address(host))]
    except ValueError:
        literal = None
    addrs = literal or resolve(host)
    if not addrs or not all(is_public(a) for a in addrs):
        raise MediaError(f"{host} is not a public internet address (it points at a private, local or reserved "
                         "network), so it is not fetched. Use an image on a public website.")


# ---- fetching ---------------------------------------------------------------------------------

class _HttpxFetched:
    def __init__(self, url: str, user_agent: str = USER_AGENT):
        import httpx
        self._client = httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, trust_env=False, headers={
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5"})
        try:
            self._resp = self._client.send(self._client.build_request("GET", url), stream=True)
        except httpx.HTTPError as e:
            self._client.close()
            raise MediaError(f"Could not fetch the image ({type(e).__name__}: {e}). Check the URL or try another "
                             "copy of the image.")
        self.status = self._resp.status_code
        self.headers = {k.lower(): v for k, v in self._resp.headers.items()}

    def iter_bytes(self) -> Iterator[bytes]:
        import httpx
        try:
            yield from self._resp.iter_bytes()
        except httpx.HTTPError as e:
            raise MediaError(f"The download broke off ({type(e).__name__}). Try again or use another copy.")

    def close(self) -> None:
        self._resp.close()
        self._client.close()


def http_fetch(url: str, user_agent: str = USER_AGENT) -> Fetched:
    return _HttpxFetched(url, user_agent)


def _read_capped(resp: Fetched, max_bytes: int) -> bytes:
    length = resp.headers.get("content-length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise MediaError(f"The image is {int(length) / MB:.1f} MB; the limit is {max_bytes // MB} MB. "
                         "Use a smaller copy of the same photo.")
    buf = bytearray()
    for chunk in resp.iter_bytes():
        buf += chunk
        if len(buf) > max_bytes:
            raise MediaError(f"The image is over {max_bytes // MB} MB; the limit is {max_bytes // MB} MB. "
                             "Use a smaller copy of the same photo.")
    return bytes(buf)


def download(url: str, *, fetch: Fetch = http_fetch, resolve: Resolve = resolve_host,
             max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS) -> Image:
    """Fetch one public image. Every hop is checked before it is requested."""
    current = url.strip() if isinstance(url, str) else url
    for hop in range(max_redirects + 1):
        check_url(current, resolve)
        resp = fetch(current)
        if resp.status == 403 and TOOL_USER_AGENT:
            resp.close()
            resp = fetch(current, user_agent=TOOL_USER_AGENT)
        try:
            if resp.status in REDIRECTS:
                location = resp.headers.get("location")
                if not location:
                    raise MediaError("The image URL redirected without saying where. Find the image's own URL.")
                current = urljoin(current, location)
                continue
            if resp.status != 200:
                raise MediaError(f"The image URL answered HTTP {resp.status}, not the image. Open the source page "
                                 "and copy the image's own URL, or use another copy.")
            data = _read_capped(resp, max_bytes)
            content_type = resp.headers.get("content-type", "")
        finally:
            resp.close()
        return identify(data, content_type)
    raise MediaError(f"The image URL redirected more than {max_redirects} times. Use the final image URL.")


def identify(data: bytes, content_type: str = "") -> Image:
    """Measure and hash bytes that must be a PNG, JPEG, WebP or GIF image."""
    if not data:
        raise MediaError("The URL returned nothing. Find the image's own URL.")
    PILImage, ImageOps = _pil()
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            fmt = im.format
            im.verify()
        with PILImage.open(io.BytesIO(data)) as im:
            im.load()
            shown = ImageOps.exif_transpose(im) if _orientation(im) not in (None, 1) else im
            width, height = shown.size
    except PILImage.DecompressionBombError:
        raise MediaError(f"That image is too large to open (over {MAX_PIXELS // 1_000_000} megapixels). "
                         "Use a smaller copy.")
    except Exception:
        head = data[:512].lstrip().lower()
        if "html" in content_type.lower() or head.startswith((b"<!doctype", b"<html", b"<")):
            raise MediaError("That page is not an image; find the image's own URL (open the page and copy the "
                             "address of the picture itself, often ending .jpg, .png or .webp).")
        raise MediaError("Those bytes are not a readable image. Only PNG, JPEG, WebP and GIF photographs can be "
                         "added; find another copy of the photo.")
    if fmt not in FORMATS:
        raise MediaError(f"That image is {fmt}; only PNG, JPEG, WebP and GIF can be added. Find a JPEG or PNG copy.")
    return Image(data=data, mime=FORMATS[fmt][0], width=width, height=height,
                 sha256=hashlib.sha256(data).hexdigest())


def _orientation(im) -> int | None:
    try:
        return im.getexif().get(0x0112)
    except Exception:
        return None


def _shown(data: bytes):
    """The image as a browser displays it: first frame, EXIF rotation applied (a pixel
    transposition, not a resample), in a mode PNG can store."""
    PILImage, ImageOps = _pil()
    im = PILImage.open(io.BytesIO(data))
    im.seek(0)
    im.load()
    if _orientation(im) not in (None, 1):
        im = ImageOps.exif_transpose(im)
    if im.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"):
        im = im.convert("RGB")  # CMYK and YCbCr JPEGs: a colour-space change, no pixel moves
    return im


# ---- cropping ---------------------------------------------------------------------------------

def check_box(box, width: int, height: int) -> tuple[int, int, int, int]:
    """[x, y, w, h] in whole pixels of the photo as displayed, inside it, at least 16 px each way."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise MediaError("box is [x, y, w, h] in pixels of the photo: left, top, width, height.")
    vals = []
    for v in box:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            raise MediaError(f"box values are whole pixels; got {box!r}.")
        vals.append(int(v))
    x, y, w, h = vals
    if w < MIN_CROP_PX or h < MIN_CROP_PX:
        raise MediaError(f"The box is {w}x{h} px; a close-up must be at least {MIN_CROP_PX} px each way.")
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise MediaError(f"The box {[x, y, w, h]} goes outside the photo, which is {width}x{height} px "
                         f"(x + w must be at most {width}, y + h at most {height}).")
    return x, y, w, h


def crop(image_bytes: bytes, box) -> bytes:
    """PNG bytes of exactly that region of the photo: the source pixels, nothing added or resampled."""
    im = _shown(image_bytes)
    x, y, w, h = check_box(box, *im.size)
    out = io.BytesIO()
    piece = im.crop((x, y, x + w, y + h))
    piece.save(out, format="PNG", optimize=False)
    return out.getvalue()


def next_id(prefix: str, existing: Iterable[str], seq: int = 0) -> tuple[str, int]:
    """P01, P02 … never reusing a number, even one removed (a claim may still cite it)."""
    nums = [int(i[len(prefix):]) for i in existing
            if isinstance(i, str) and i.startswith(prefix) and i[len(prefix):].isdigit()]
    n = max(nums + [seq]) + 1
    return f"{prefix}{n:02d}", n


def refresh(research: dict) -> None:
    """Recompute what the server owns from the photos themselves: the count, how it was counted,
    and which views have a photo (excluded photos cover nothing). The coverage note is kept."""
    photos = research.get("photos") or []
    research["photo_count"] = len(photos)
    research["count_method"] = COUNT_METHOD
    note = (research.get("coverage") or {}).get("note")
    used = [p for p in photos if p.get("status") != "excluded"]
    views = list(COVERAGE_VIEWS) + (["interior"] if any("interior" in p.get("views", []) for p in used) else [])
    coverage = {v: (", ".join(p["id"] for p in used if v in p.get("views", [])) or None) for v in views}
    coverage["note"] = note
    research["coverage"] = coverage


PREVIEW_MAX_BYTES = 350_000


def preview(image_bytes: bytes, max_side: int = 1024) -> tuple[bytes, str, float]:
    """A copy for the AI to look at (not stored, not shown to Lester): a JPEG scaled down so the
    longest side is at most max_side and the file at most PREVIEW_MAX_BYTES (lower quality first,
    then smaller). Returns (bytes, mime, scale), where scale = displayed pixels per preview pixel,
    so the AI can turn what it sees into a box.

    The cap matters: the first live sampan run sent a 4032 x 3024 photo's preview inside a tool
    result, overflowed the SDK's 1 MB message buffer and killed the research turn."""
    im = _shown(image_bytes)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    side = min(max_side, max(w, h))
    while True:
        scale = max(w, h) / side if max(w, h) > side else 1.0
        small = im.resize((round(w / scale), round(h / scale))) if scale > 1.0 else im
        for quality in (85, 70, 55):
            out = io.BytesIO()
            small.save(out, format="JPEG", quality=quality)
            if out.tell() <= PREVIEW_MAX_BYTES:
                return out.getvalue(), "image/jpeg", scale
        if side <= 256:
            return out.getvalue(), "image/jpeg", scale
        side = int(side * 0.75)
