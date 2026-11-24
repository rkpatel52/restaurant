"""Offline QR encoding for TableFlow's ASCII URLs (versions 3-L, 5-L and 6-L)."""

import struct
import zlib


def multiply(left, right):
    result = 0
    while right:
        if right & 1:
            result ^= left
        left <<= 1
        if left & 0x100:
            left ^= 0x11D
        right >>= 1
    return result


def codewords(message, count, parity, blocks=1):
    payload = message.encode("ascii")
    if len(payload) > count - 2:
        raise ValueError("URL is too long for the table QR code")
    bits = "0100" + f"{len(payload):08b}" + "".join(f"{byte:08b}" for byte in payload)
    bits += "0000"
    data = [int(bits[index:index + 8], 2) for index in range(0, len(bits), 8)]
    data.extend(0xEC if index % 2 == 0 else 0x11 for index in range(count - len(data)))

    divisor = [1]
    root = 1
    for _ in range(parity):
        divisor = [a ^ multiply(b, root) for a, b in zip(divisor + [0], [0] + divisor)]
        root = multiply(root, 2)
    block_size = count // blocks
    chunks = [data[index:index + block_size] for index in range(0, count, block_size)]
    remainders = []
    for chunk in chunks:
        remainder = [0] * parity
        for byte in chunk:
            factor = byte ^ remainder[0]
            remainder = [value ^ multiply(coefficient, factor)
                         for value, coefficient in zip(remainder[1:] + [0], divisor[1:])]
        remainders.append(remainder)
    return [byte for column in zip(*chunks) for byte in column] + [
        byte for column in zip(*remainders) for byte in column
    ]


def matrix(message):
    if len(message) <= 53:
        size, count, parity, blocks = 29, 55, 15, 1
    elif len(message) <= 106:
        size, count, parity, blocks = 37, 108, 26, 1
    else:
        size, count, parity, blocks = 41, 136, 18, 2
    grid = [[None] * size for _ in range(size)]

    def mark(x, y, dark):
        if 0 <= x < size and 0 <= y < size:
            grid[y][x] = int(dark)

    for index in range(size):
        mark(6, index, index % 2 == 0)
        mark(index, 6, index % 2 == 0)
    for x, y in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                mark(x + dx, y + dy, max(abs(dx), abs(dy)) not in (2, 4))
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            mark(size - 7 + dx, size - 7 + dy, max(abs(dx), abs(dy)) != 1)

    # BCH-protected format value for error correction L and mask pattern 0.
    format_bits = 0x77C4
    first = [(8, y) for y in range(6)] + [(8, 7), (8, 8), (7, 8)]
    first += [(14 - i, 8) for i in range(9, 15)]
    second = [(size - 1 - i, 8) for i in range(8)]
    second += [(8, size - 15 + i) for i in range(8, 15)]
    for index, (position, copy) in enumerate(zip(first, second)):
        dark = (format_bits >> index) & 1
        mark(*position, dark)
        mark(*copy, dark)
    mark(8, size - 8, True)

    bits = iter(int(bit) for byte in codewords(message, count, parity, blocks) for bit in f"{byte:08b}")
    for column in range(size - 1, 0, -2):
        right = column if column > 6 else column - 1
        upwards = ((right + 1) & 2) == 0
        rows = range(size - 1, -1, -1) if upwards else range(size)
        for y in rows:
            for x in (right, right - 1):
                if grid[y][x] is None:
                    mark(x, y, next(bits, 0) ^ ((x + y) % 2 == 0))
    return grid


def png(message):
    scale = 8
    grid = matrix(message)
    width = (len(grid) + 8) * scale
    white = bytes([255]) * width
    rows = [white] * (scale * 4)
    quiet_zone = bytes([255]) * (scale * 4)
    for row in grid:
        pixels = b"".join(bytes([0 if dark else 255]) * scale for dark in row)
        rows.extend([quiet_zone + pixels + quiet_zone] * scale)
    rows.extend([white] * (scale * 4))

    def chunk(kind, content):
        checksum = zlib.crc32(kind + content)
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", checksum)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">2I5B", width, width, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + row for row in rows)))
            + chunk(b"IEND", b""))
