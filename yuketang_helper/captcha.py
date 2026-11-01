"""Pure-Python slider-captcha image analysis and humanised drag trajectories.

The launcher ships without numpy/Pillow, so PNG decoding, gap detection and the
drag curve are implemented here with the standard library only. Everything in
this module is deterministic given a seed, which keeps the tests reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
LUMINANCE = (299, 587, 114)


class ImageError(ValueError):
    """Raised when a screenshot cannot be decoded or contains no usable gap."""


@dataclass(frozen=True)
class GrayImage:
    width: int
    height: int
    gray: bytearray
    alpha: bytearray | None = None

    def at(self, x: int, y: int) -> int:
        return self.gray[y * self.width + x]


@dataclass(frozen=True)
class ColorImage:
    width: int
    height: int
    rgb: bytearray

    def at(self, x: int, y: int):
        index = (y * self.width + x) * 3
        return self.rgb[index], self.rgb[index + 1], self.rgb[index + 2]


def decode_png(data: bytes, color: bool = False):
    """Decode an 8-bit non-interlaced PNG into grayscale (or RGB when asked)."""
    if not data.startswith(PNG_SIGNATURE):
        raise ImageError("not a PNG image")
    pos = len(PNG_SIGNATURE)
    width = height = depth = color_type = None
    palette = None
    interlace = 0
    idat = []
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if not width or not height or color_type is None:
        raise ImageError("PNG header is missing")
    if interlace:
        raise ImageError("interlaced PNG is not supported")
    if color_type not in CHANNELS:
        raise ImageError(f"unsupported PNG colour type {color_type}")
    if depth not in (8, 16):
        raise ImageError(f"unsupported PNG bit depth {depth}")
    if not idat:
        raise ImageError("PNG has no image data")
    channels = CHANNELS[color_type]
    raw = zlib.decompress(b"".join(idat))
    sample = depth // 8
    stride = width * channels * sample
    step = channels * sample
    previous = bytearray(stride)
    pixels = bytearray()
    offset = 0
    for _ in range(height):
        if offset >= len(raw):
            raise ImageError("PNG image data is truncated")
        method = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if len(line) != stride:
            raise ImageError("PNG scanline is truncated")
        if method == 1:
            for i in range(step, stride):
                line[i] = (line[i] + line[i - step]) & 0xFF
        elif method == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif method == 3:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif method == 4:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                up = previous[i]
                corner = previous[i - step] if i >= step else 0
                estimate = left + up - corner
                to_left = abs(estimate - left)
                to_up = abs(estimate - up)
                to_corner = abs(estimate - corner)
                if to_left <= to_up and to_left <= to_corner:
                    predictor = left
                elif to_up <= to_corner:
                    predictor = up
                else:
                    predictor = corner
                line[i] = (line[i] + predictor) & 0xFF
        elif method != 0:
            raise ImageError(f"unknown PNG filter {method}")
        pixels += line if sample == 1 else line[0::2]
        previous = line
    if sample == 2:
        stride //= 2
        step //= 2
    if color:
        rgb = bytearray(width * height * 3)
        for y in range(height):
            base = y * stride
            row = y * width * 3
            for x in range(width):
                index = base + x * channels
                slot = row + x * 3
                if color_type == 3:
                    entry = pixels[index] * 3
                    if not palette or entry + 2 >= len(palette):
                        raise ImageError("PNG palette index is out of range")
                    rgb[slot], rgb[slot + 1], rgb[slot + 2] = palette[entry], palette[entry + 1], palette[entry + 2]
                elif color_type in (0, 4):
                    value = pixels[index]
                    rgb[slot] = rgb[slot + 1] = rgb[slot + 2] = value
                else:
                    rgb[slot], rgb[slot + 1], rgb[slot + 2] = pixels[index], pixels[index + 1], pixels[index + 2]
        return ColorImage(width, height, rgb)
    gray = bytearray(width * height)
    alpha = bytearray(width * height) if channels in (2, 4) else None
    for y in range(height):
        row = y * width
        base = y * stride
        for x in range(width):
            index = base + x * channels
            if color_type in (0, 4):
                value = pixels[index]
                if alpha is not None:
                    alpha[row + x] = pixels[index + 1]
            elif color_type == 3:
                slot = pixels[index] * 3
                if not palette or slot + 2 >= len(palette):
                    raise ImageError("PNG palette index is out of range")
                value = (palette[slot] * LUMINANCE[0] + palette[slot + 1] * LUMINANCE[1]
                         + palette[slot + 2] * LUMINANCE[2]) // 1000
            else:
                value = (pixels[index] * LUMINANCE[0] + pixels[index + 1] * LUMINANCE[1]
                         + pixels[index + 2] * LUMINANCE[2]) // 1000
                if alpha is not None:
                    alpha[row + x] = pixels[index + 3]
            gray[row + x] = value
    return GrayImage(width, height, gray, alpha)


def blobs(image: ColorImage, min_area: int = 120, max_area: int = 9000,
          saturation: int = 55, fill: float = 0.45, limit: int = 12) -> list[dict]:
    """Find small saturated, roughly round patches (captcha dots) in a screenshot."""
    width, height = image.width, image.height
    total = width * height
    rgb = image.rgb
    mask = bytearray(total)
    for index in range(total):
        slot = index * 3
        red, green, blue = rgb[slot], rgb[slot + 1], rgb[slot + 2]
        high = max(red, green, blue)
        if high >= 70 and high - min(red, green, blue) >= saturation:
            mask[index] = 1
    seen = bytearray(total)
    found = []
    for start in range(total):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        count = total_x = total_y = 0
        left, top, right, bottom = width, height, -1, -1
        while stack:
            index = stack.pop()
            y, x = divmod(index, width)
            count += 1
            total_x += x
            total_y += y
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for neighbour, allowed in ((index - 1, x > 0), (index + 1, x < width - 1),
                                       (index - width, y > 0), (index + width, y < height - 1)):
                if allowed and mask[neighbour] and not seen[neighbour]:
                    seen[neighbour] = 1
                    stack.append(neighbour)
        if not min_area <= count <= max_area:
            continue
        box_width, box_height = right - left + 1, bottom - top + 1
        if box_height and not 0.45 <= box_width / box_height <= 2.2:
            continue
        if box_width * box_height and count / (box_width * box_height) < fill:
            continue
        found.append({
            "x": left, "y": top, "width": box_width, "height": box_height,
            "cx": total_x / count, "cy": total_y / count, "area": count,
        })
    found.sort(key=lambda item: item["area"], reverse=True)
    return found[:limit]


def encode_png(image: GrayImage) -> bytes:
    """Encode a grayscale PNG; used by the tests and by debug dumps."""
    rows = bytearray()
    for y in range(image.height):
        rows.append(0)
        rows += image.gray[y * image.width:(y + 1) * image.width]
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 0, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")


def column_energy(image: GrayImage, top: int, bottom: int, stride: int = 1) -> list[float]:
    """Vertical edge energy per column inside a horizontal band."""
    top = max(1, min(top, image.height - 2))
    bottom = max(top + 1, min(bottom, image.height - 1))
    energy = [0.0] * image.width
    for x in range(1, image.width - 1):
        total = 0
        for y in range(top, bottom, stride):
            row = y * image.width
            total += abs(image.gray[row + x + 1] - image.gray[row + x - 1])
        energy[x] = total / ((bottom - top) // stride or 1)
    return energy


def gap_candidates(image: GrayImage, band: tuple[int, int], piece_width: int, limit: int = 3) -> list[int]:
    """Rank candidate left edges of the puzzle cut-out inside the background."""
    if piece_width <= 0 or image.width < piece_width + 12:
        raise ImageError("background is too small for the puzzle piece")
    energy = column_energy(image, band[0], band[1])
    peak = max(energy[1:-1] or [0.0]) or 1.0
    scored = []
    lowest = max(4, piece_width // 5)
    highest = image.width - piece_width - 4
    for x in range(lowest, max(lowest + 1, highest)):
        if energy[x] < 0.35 * peak:
            continue
        slope = max(energy[x - 1], energy[x + 1]) if 1 <= x < image.width - 1 else energy[x]
        if energy[x] < slope:
            continue
        right = min(image.width - 2, x + piece_width)
        edge = energy[x] + energy[right]
        inside = _mean(image, x + 4, right - 4, band)
        outside = (_mean(image, max(1, x - 16), max(2, x - 3), band)
                   + _mean(image, min(image.width - 2, right + 3), min(image.width - 1, right + 16), band)) / 2
        contrast = abs(inside - outside)
        scored.append((edge + contrast * 1.6, x))
    scored.sort(reverse=True)
    return [x for _, x in scored[:max(1, limit)]]


def _mean(image: GrayImage, left: int, right: int, band: tuple[int, int]) -> float:
    left = max(0, left)
    right = min(image.width, right)
    if right <= left:
        return 0.0
    total = 0
    count = 0
    for y in range(max(0, band[0]), min(image.height, band[1])):
        row = y * image.width
        total += sum(image.gray[row + left:row + right])
        count += right - left
    return total / count if count else 0.0


def trajectory(distance: float, rng: random.Random | None = None, points: int = 46) -> list[tuple[float, float, int]]:
    """Human-like drag curve: accelerate, small overshoot, correction, jitter."""
    if distance <= 0:
        raise ImageError("drag distance must be positive")
    rng = rng or random.Random()
    overshoot = min(9.0, max(2.0, distance * 0.04)) * rng.uniform(0.6, 1.0)
    total = distance + overshoot
    steps = []
    x = 0.0
    for index in range(1, points + 1):
        ratio = index / points
        eased = 1 - (1 - ratio) ** 2.4
        target = total * eased
        delta = target - x
        x = target
        steps.append(delta)
    # Walk back the overshoot in a few shuffling moves before releasing.
    correction = overshoot
    back = []
    while correction > 0.5:
        piece = min(correction, rng.uniform(0.6, 2.2))
        back.append(-piece)
        correction -= piece
    timeline = []
    y = 0.0
    for delta in steps + back:
        y += rng.uniform(-1.1, 1.1)
        y = max(-3.0, min(3.0, y))
        delay = int(rng.uniform(9, 20))
        timeline.append((delta, y, delay))
    if timeline:
        pause = timeline[-1]
        timeline[-1] = (pause[0], pause[1], int(rng.uniform(120, 220)))
    return timeline


def release_hold(rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    return int(rng.uniform(60, 130))
