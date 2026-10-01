"""
generate_icons.py
Generates PNG icons (16, 32, 48, 128px) for the rounded abstract logo.
Falls back to writing minimal valid PNG files if no SVG renderer is available.
"""

import struct
import zlib
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__))
SIZES = [16, 32, 48, 128]

# Cyan brand color matching #0ea5e9
ACCENT  = (14, 165, 233, 255)
DARK    = (2, 132, 199, 255)
WHITE   = (255, 255, 255, 255)


def make_png(width: int, height: int, pixels) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr_data = struct.pack('>II', width, height) + bytes([8, 6, 0, 0, 0])

    raw_rows = b''
    for y in range(height):
        row = b'\x00'
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            row += bytes([r, g, b, a])
        raw_rows += row

    compressed = zlib.compress(raw_rows, 9)

    png  = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr_data)
    png += chunk(b'IDAT', compressed)
    png += chunk(b'IEND', b'')
    return png


def lerp(a, b, t):
    return a + (b - a) * t


def generate_logo_pixels(size: int):
    pixels = []
    
    def logo_contains(px, py):
        nx = (px / size) * 2 - 1
        ny = (py / size) * 2 - 1
        
        dist = (nx**2 + ny**2)**0.5
        
        # Outer circle
        if dist > 0.9:
            return False
            
        # Inner 'S' curve cutout
        curve = nx**3
        thickness = 0.15 + (0.05 if size < 32 else 0)
        if abs(ny - curve) < thickness:
            return False
            
        return True

    for y in range(size):
        for x in range(size):
            if logo_contains(x, y):
                t = y / size
                r = int(lerp(ACCENT[0], DARK[0], t))
                g = int(lerp(ACCENT[1], DARK[1], t))
                b = int(lerp(ACCENT[2], DARK[2], t))
                pixels.append((r, g, b, 255))
            else:
                pixels.append((0, 0, 0, 0))

    return pixels


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for size in SIZES:
        print(f'Generating {size}x{size} icon…')
        pixels = generate_logo_pixels(size)
        png_data = make_png(size, size, pixels)

        for name in [f'icon{size}.png']:
            path = os.path.join(OUTPUT_DIR, name)
            with open(path, 'wb') as f:
                f.write(png_data)
            print(f'  Written: {path}')

    print('\nAll icons generated successfully.')


if __name__ == '__main__':
    main()
