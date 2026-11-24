"""Bounded structural JPEG validation. No pixel decoding or recompression."""
from dataclasses import dataclass
import struct

MAX_PAGE_BYTES = 32 * 1024 * 1024
REEXPORT = ' Re-export as an upright 2550 x 3300, 8-bit grayscale or RGB JPEG at 300 DPI.'

class JpegImageError(Exception):
    """An image cannot be used as a form page."""

@dataclass(frozen=True)
class JpegImage:
    data: bytes
    width_px: int
    height_px: int
    components: int
    progressive: bool


def _orientation(payload):
    if not payload.startswith(b'Exif\0\0'):
        return
    t = payload[6:]
    def fail():
        raise JpegImageError('Malformed EXIF orientation metadata.' + REEXPORT)
    if len(t) < 8 or t[:2] not in (b'II', b'MM'):
        fail()
    order = '<' if t[:2] == b'II' else '>'
    def read(fmt, pos):
        size = struct.calcsize(fmt)
        if pos < 0 or pos + size > len(t):
            fail()
        return struct.unpack_from(order + fmt, t, pos)
    if read('H', 2)[0] != 42:
        fail()
    pos = read('I', 4)[0]
    if pos < 8:
        fail()
    count = read('H', pos)[0]
    if pos + 2 + 12 * count + 4 > len(t):
        fail()
    for i in range(count):
        p = pos + 2 + 12 * i
        tag, kind, n = read('HHI', p)
        if tag == 274:
            if kind != 3 or n != 1:
                fail()
            if read('H', p + 8)[0] != 1:
                raise JpegImageError('Image pixels must already be upright (EXIF orientation 1).' + REEXPORT)


def parse_jpeg(data: bytes) -> JpegImage:
    """Validate marker/scan structure; entropy correctness needs SAMPLE review."""
    def fail(message):
        raise JpegImageError(message + REEXPORT)
    if len(data) > MAX_PAGE_BYTES:
        fail('JPEG page exceeds the 32 MiB limit.')
    if not data.startswith(b'\xff\xd8'):
        fail('The file is not a JPEG image.')
    pos = 2
    frame = None
    scans = 0
    while pos < len(data):
        if data[pos] != 255:
            fail('Malformed JPEG marker boundary.')
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            fail('Truncated JPEG marker.')
        marker = data[pos]; pos += 1
        if marker == 0xd9:
            if not frame or not scans:
                fail('JPEG has no complete frame and scan.')
            if pos != len(data):
                fail('Unexpected bytes after JPEG end marker.')
            w,h,c,progressive,ids = frame
            return JpegImage(data,w,h,c,progressive)
        if marker in (0,0xd8,0x01) or 0xd0 <= marker <= 0xd7:
            fail('Unexpected standalone JPEG marker.')
        if pos + 2 > len(data):
            fail('Truncated JPEG segment length.')
        size = int.from_bytes(data[pos:pos+2],'big')
        if size < 2 or pos + size > len(data):
            fail('Invalid or truncated JPEG segment.')
        payload = data[pos+2:pos+size]; pos += size
        if marker == 0xe1:
            _orientation(payload)
        if marker == 0xee and payload.startswith(b'Adobe'):
            if len(payload) < 12 or payload[11] == 2:
                fail('CMYK/YCCK images are unsupported.')
        if 0xc0 <= marker <= 0xcf and marker not in (0xc4,0xc8,0xcc):
            if marker not in (0xc0,0xc2) or frame is not None or len(payload)<6:
                fail('Unsupported JPEG frame coding.')
            precision,h,w,c = struct.unpack('>BHHB',payload[:6])
            if precision != 8 or c not in (1,3) or len(payload)!=6+3*c:
                fail('Only 8-bit grayscale or RGB JPEGs are supported.')
            if (w,h)!=(2550,3300):
                fail('JPEG must be portrait letter size, exactly 2550 x 3300 pixels.')
            ids = set(payload[6::3])
            if len(ids)!=c:
                fail('Duplicate JPEG frame components.')
            frame=(w,h,c,marker==0xc2,ids)
        if marker == 0xda:
            if frame is None or not payload:
                fail('JPEG scan has no frame.')
            n=payload[0]
            if n<1 or n>frame[2] or len(payload)!=1+2*n+3:
                fail('Malformed JPEG scan header.')
            selectors=payload[1:1+2*n:2]
            if len(set(selectors))!=n or not set(selectors)<=frame[4]:
                fail('Invalid JPEG scan components.')
            scans+=1
            start=pos
            while True:
                at=data.find(b'\xff',pos)
                if at<0:
                    fail('JPEG scan is missing its end marker.')
                end=at+1
                while end<len(data) and data[end]==255:
                    end+=1
                if end>=len(data):
                    fail('Truncated JPEG scan marker.')
                code=data[end]
                if code==0 or 0xd0<=code<=0xd7:
                    pos=end+1
                    continue
                if at==start:
                    fail('JPEG scan contains no image data.')
                pos=at
                break
    fail('JPEG is missing its end-of-image marker.')
