from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from yuketang_helper.ocr import OcrError, recognize_image


FIXTURE = Path(__file__).parent / 'fixtures/ocr/short-option-liuzongyuan.png'


def blank_png(width=400, height=90, shade=255):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = (b'\x00' + bytes([shade, shade, shade]) * width) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class WindowsOcrTests(unittest.TestCase):
    def test_real_failed_short_option_is_recovered(self):
        # The unmodified OCR script returned no lines on this actual option row.
        self.assertIn('柳宗元', recognize_image(FIXTURE))

    def test_empty_images_still_stop_instead_of_inventing_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blank.png'
            for shade in (255, 220):
                with self.subTest(background=shade):
                    path.write_bytes(blank_png(shade=shade))
                    with self.assertRaises(OcrError):
                        recognize_image(path)
