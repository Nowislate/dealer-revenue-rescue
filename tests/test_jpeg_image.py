import sys,struct,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dealerpaperwork.jpeg_image import parse_jpeg,JpegImageError,MAX_PAGE_BYTES
FIX=Path(__file__).parent/'fixtures/form_images'
def segment(marker,payload): return b'\xff'+bytes([marker])+struct.pack('>H',len(payload)+2)+payload
def exif(value=1): return segment(0xe1,b'Exif\0\0II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',value)+b'\0\0'+b'\0'*4)
class TestJpeg(unittest.TestCase):
 def check(self,name,c,p):
  data=(FIX/name).read_bytes(); j=parse_jpeg(data)
  self.assertEqual((j.width_px,j.height_px,j.components,j.progressive),(2550,3300,c,p));self.assertEqual(j.data,data)
 def test_gray_baseline(self): self.check('page-1.jpg',1,False)
 def test_rgb_baseline(self): self.check('page-2.jpg',3,False)
 def test_rgb_progressive(self): self.check('progressive.jpg',3,True)
 def test_reject_malformed_structure(self):
  d=(FIX/'page-1.jpg').read_bytes(); sof=d.index(b'\xff\xc0'); sos=d.index(b'\xff\xda')
  cases=[b'PDF',d[:20],d[:-2],d[:sos]+b'\xff\xd9',b'\xff\xd8\xff\xe0\x00\x01',d[:sof]+d[sof:].replace(b'\xff\xc0',b'\xff\xc3',1), d[:sof+4]+b'\x0c'+d[sof+5:],d[:sof+5]+struct.pack('>HH',2550,3300)+d[sof+9:],d[:sof+9]+b'\x04'+d[sof+10:],(FIX/'progressive.jpg').read_bytes()[:-100],b'x'*(MAX_PAGE_BYTES+1)]
  for x in cases:
   with self.subTest(length=len(x)),self.assertRaises(JpegImageError): parse_jpeg(x)
 def test_exif_orientation(self):
  d=(FIX/'page-1.jpg').read_bytes()
  self.assertEqual(parse_jpeg(d[:2]+exif()+d[2:]).components,1)
  for extra in [exif(6),segment(0xe1,b'Exif\0\0bad'),segment(0xe1,b'Exif\0\0II'+struct.pack('<HI',42,0xfffffff0))]:
   with self.assertRaises(JpegImageError):parse_jpeg(d[:2]+extra+d[2:])
