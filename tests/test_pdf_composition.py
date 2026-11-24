import copy,hashlib,json,re,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dealerpaperwork.doc_library import DealerDocument,FieldPlacement,DocumentLibrary
from dealerpaperwork.pdf_overlay import render_overlay_pdf
FIX=Path(__file__).parent/'fixtures'
def pdf_objects(pdf):
 start=int(pdf.split(b'startxref\n')[-1].splitlines()[0]);assert pdf[start:].startswith(b'xref\n')
 lines=pdf[start:].splitlines();n=int(lines[1].split()[1]);offsets=[int(x.split()[0]) for x in lines[3:3+n-1]]
 result={}
 for i,offset in enumerate(offsets,1):
  assert pdf[offset:].startswith(b'%d 0 obj\n'%i)
  end=offsets[i] if i<len(offsets) else start
  result[i]=pdf[offset:end].split(b'obj\n',1)[1].rsplit(b'\nendobj\n',1)[0]
 return result
def stream(obj):
 header,data=obj.split(b'\nstream\n',1);n=int(re.search(rb'/Length (\d+)',header)[1]);assert data[n:].lstrip(b'\n')==b'endstream';return data[:n]
class TestComposition(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.lib=DocumentLibrary(Path(self.tmp.name));self.doc=self.lib.add_document(source_paths=[FIX/'form_images'/f'page-{n}.jpg' for n in (1,2,3)],doc_id='form',title='Synthetic',fields=[FieldPlacement('buyer_name',1,20,55,11),FieldPlacement('vehicle_vin',3,20,80,10)]);self.images=self.lib.load_page_images(self.doc)
 def render(self,**kwargs):return render_overlay_pdf(self.doc,{'buyer_name':'Ada Buyer','vehicle_vin':'SAMPLEVIN'},page_images=self.images,**kwargs)
 def test_composed_page_contains_exact_image_and_values(self):
  objs=pdf_objects(self.render());page=objs[5];imageid=int(re.search(rb'/Im1 (\d+) 0 R',page)[1]);contentid=int(re.search(rb'/Contents (\d+) 0 R',page)[1]);self.assertEqual(stream(objs[imageid]),self.images[1].data);self.assertIn(b'/DCTDecode',objs[imageid]);self.assertIn(b'/DeviceGray',objs[imageid]);self.assertIn(b'/Width 2550 /Height 3300',objs[imageid]);c=stream(objs[contentid]);self.assertLess(c.index(b'/Im1 Do'),c.index(b'Ada Buyer'))
 def test_manifest_order_includes_page_without_fields(self):
  objs=pdf_objects(self.render());kids=re.search(rb'/Kids \[(.*?)\]',objs[2])[1];ids=[int(x) for x in re.findall(rb'(\d+) 0 R',kids)];self.assertEqual(len(ids),3)
  for n,ident in enumerate(ids,1):
   imageid=int(re.search(rb'/Im1 (\d+) 0 R',objs[ident])[1]);contentid=int(re.search(rb'/Contents (\d+) 0 R',objs[ident])[1]);self.assertEqual(stream(objs[imageid]),self.images[n].data)
   if n==2:self.assertNotIn(b'Ada Buyer',stream(objs[contentid]));self.assertNotIn(b'SAMPLEVIN',stream(objs[contentid]))
  self.doc.fields=[];self.assertIn(b'/Count 3',self.render())
 def test_marks_and_offsets_preserved(self):
  for mark in ['SAMPLE','DRAFT','REPRINT - original voided']:
   objs=pdf_objects(self.render(banner=mark,offsets_mm={'x_mm':2,'y_mm':-1}))
   for obj in objs.values():
    if b'/Length' in obj and b'/DCTDecode' not in obj:
     content=stream(obj);self.assertIn(mark.encode(),content);self.assertTrue(content.startswith(b'q 612 0 0 792 0 0 cm /Im1 Do Q\n'))
   self.assertIn(b'62.36 627.93 Tm',self.render(offsets_mm={'x_mm':2,'y_mm':-1}))
 def test_values_only_matches_baseline_bytes(self):
  for name,case in json.loads((FIX/'values_only/cases.json').read_text()).items():
   doc=DealerDocument('golden','Golden','old.pdf','old.pdf',fields=[FieldPlacement.from_dict(f) for f in case['fields']]);pdf=render_overlay_pdf(doc,case['values'],banner=case['banner'],offsets_mm=case['offsets_mm']);self.assertEqual(pdf,(FIX/'values_only'/f'{name}.pdf').read_bytes());self.assertNotIn(b'/XObject',pdf)
 def test_binary_lengths_and_xref_are_correct(self):
  objs=pdf_objects(self.render())
  for obj in objs.values():
   if b'\nstream\n' in obj:stream(obj)
 def test_incomplete_images_rejected(self):
  from dealerpaperwork.pdf_overlay import PdfOverlayError
  for images in [None,{}, {1:self.images[1]},dict(self.images,**{'4':self.images[1]})]:
   with self.assertRaises(PdfOverlayError):render_overlay_pdf(self.doc,{},page_images=images)
  wrong=dict(self.images);wrong[1]=self.images[2]
  with self.assertRaises(PdfOverlayError):render_overlay_pdf(self.doc,{},page_images=wrong)
  for attr,value in [('pages',[]),('image_revision',None),('form_source','bad')]:
   doc=copy.deepcopy(self.doc);setattr(doc,attr,value)
   with self.assertRaises(PdfOverlayError):render_overlay_pdf(doc,{},page_images=self.images)
  legacy=DealerDocument('old','Old')
  with self.assertRaises(PdfOverlayError):render_overlay_pdf(legacy,{},page_images=self.images)
