import hashlib,json,sys,tempfile,unittest,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dealerpaperwork.doc_library import *
from dealerpaperwork.jpeg_image import MAX_PAGE_BYTES
FIX=Path(__file__).parent/'fixtures/form_images'
MANAGER=SimpleNamespace(role='manager',display_name='Manager')
class TestDocImages(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.lib=DocumentLibrary(self.root/'documents')
 def add(self,fields=()): return self.lib.add_document(source_paths=[FIX/'page-2.jpg',FIX/'page-1.jpg'],doc_id='form',title='Synthetic',fields=fields)
 def approve(self): return self.lib.approve_document('form',MANAGER)
 def raw(self):return json.loads(self.lib.records_path.read_text())
 def save_raw(self,data):self.lib.records_path.write_text(json.dumps(data))
 def test_add_owns_ordered_jpegs(self):
  source=self.root/'export.jpg';source.write_bytes((FIX/'page-1.jpg').read_bytes());d=self.lib.add_document(source_paths=[source],doc_id='form',title='Form');source.unlink()
  self.assertEqual(d.status,'draft');self.assertEqual([p.page for p in d.pages],[1]);self.assertEqual(self.lib.load_page_images(d)[1].data,(FIX/'page-1.jpg').read_bytes());self.assertEqual(d.pages[0].sha256,hashlib.sha256((FIX/'page-1.jpg').read_bytes()).hexdigest())
 def test_image_manifest_round_trip(self):
  d=self.add([FieldPlacement('buyer_name',2,20,30,11)]);self.assertEqual(self.raw()['format_version'],'stage7-1');self.assertEqual(DocumentLibrary(self.lib.library_dir).get_document('form').to_dict(),d.to_dict());self.assertEqual(d.pages[0].source_filename,'page-2.jpg')
 def test_legacy_record_loads_without_images(self):
  self.lib.library_dir.mkdir();raw={'doc_id':'old','title':'Old','when_it_prints':['retail_new'],'stored_name':'missing.pdf','status':'approved','approved_by':'Boss','approved_at':'then','fields':[{'field_name':'buyer_name'}]};self.save_raw({'format_version':'stage5-1','documents':[raw]});before=self.lib.records_path.read_bytes();d=DocumentLibrary(self.lib.library_dir).get_document('old');self.assertEqual(d.form_source,'legacy_values_only');self.assertEqual(d.pages,[]);self.assertIsNone(d.image_revision);self.assertTrue(d.is_approved);self.assertEqual(self.lib.load_page_images(d),{});self.assertEqual(before,self.lib.records_path.read_bytes())
 def test_replacement_revokes_approval(self):
  self.add();old=copy.deepcopy(self.approve());d=self.lib.replace_page_images('form',[FIX/'page-3.jpg']);self.assertNotEqual(old.image_revision,d.image_revision);self.assertTrue(self.lib.page_image_path(old,1).exists());self.assertEqual((d.status,d.approved_by,d.approved_at),('draft',None,None))
 def test_failed_replacement_keeps_approved_record(self):
  self.add();old=copy.deepcopy(self.approve());before=self.lib.records_path.read_bytes();files=set(self.lib.library_dir.rglob('*.jpg'))
  for paths,mock in [([FIX/'missing.jpg'],False),([FIX/'page-3.jpg'],True)]:
   with patch.object(self.lib,'_save',side_effect=OSError('disk full')) if mock else patch.object(self.lib,'_unused',create=True):
    with self.assertRaises(DocumentLibraryError):self.lib.replace_page_images('form',paths)
   self.assertEqual(self.lib.records_path.read_bytes(),before);self.assertEqual(self.lib.get_document('form').to_dict(),old.to_dict());self.assertEqual(set(self.lib.library_dir.rglob('*.jpg')),files)
 def test_manifest_and_placement_validation(self):
  for paths in [[],[self.root],[FIX/'form.pdf']]:
   with self.assertRaises(DocumentLibraryError):self.lib.add_document(source_paths=paths,doc_id='form',title='Form')
  self.add([FieldPlacement('buyer_name',2,20,30)])
  with self.assertRaises(DocumentLibraryError):self.lib.replace_page_images('form',[FIX/'page-1.jpg'])
  with self.assertRaises(DocumentLibraryError):self.lib.update_placements('form',[FieldPlacement('buyer_name',3)])
  original=self.raw()
  for page in [1,3]:
   raw=copy.deepcopy(original);raw['documents'][0]['pages'][1]['page']=page;self.save_raw(raw)
   with self.assertRaises(DocumentLibraryError):DocumentLibrary(self.lib.library_dir)
 def test_missing_manifest_cannot_downgrade_image_record(self):
  self.add();original=self.raw()
  for key,value in [('pages',[]),('image_revision',None),('form_source','bad'),('form_source',None)]:
   raw=copy.deepcopy(original);raw['documents'][0][key]=value;self.save_raw(raw)
   with self.assertRaises(DocumentLibraryError):DocumentLibrary(self.lib.library_dir)
  for raw in [{'format_version':'future','documents':[]},{'documents':[]},[],{'format_version':'stage7-1','documents':[None]}]:
   self.save_raw(raw)
   with self.assertRaises(DocumentLibraryError):DocumentLibrary(self.lib.library_dir)
  self.lib.records_path.write_text('{')
  with self.assertRaises(DocumentLibraryError):DocumentLibrary(self.lib.library_dir)
 def test_refresh_resolves_published_revision(self):
  self.add();other=DocumentLibrary(self.lib.library_dir);d=other.replace_page_images('form',[FIX/'page-3.jpg']);other.update_placements('form',[FieldPlacement('buyer_name',1,30,40)]);other.approve_document('form',MANAGER);current=self.lib.get_document('form',refresh=True);self.assertEqual(current.image_revision,d.image_revision);self.assertEqual(current.fields[0].x_mm,30);self.assertTrue(current.is_approved)
 def test_source_snapshot_is_the_stored_snapshot(self):
  source=self.root/'source.jpg';accepted=(FIX/'page-1.jpg').read_bytes();source.write_bytes(accepted)
  import dealerpaperwork.doc_library as m
  parser=m.parse_jpeg
  def change(data):source.write_bytes((FIX/'page-2.jpg').read_bytes());return parser(data)
  with patch.object(m,'parse_jpeg',side_effect=change):d=self.lib.add_document(source_paths=[source],doc_id='form',title='Form')
  self.assertEqual(self.lib.load_page_images(d)[1].data,accepted)
  source.write_bytes(b'x'*(MAX_PAGE_BYTES+1))
  with patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded read')):
   with self.assertRaises(DocumentLibraryError):self.lib.replace_page_images('form',[source])
 def test_reorder_keeps_placement_numbers_and_revokes_approval(self):
  self.add([FieldPlacement('buyer_name',2,20,30)]);self.approve();d=self.lib.replace_page_images('form',[FIX/'page-1.jpg',FIX/'page-2.jpg']);self.assertEqual(d.pages[0].source_filename,'page-1.jpg');self.assertEqual(d.fields[0].to_dict(),FieldPlacement('buyer_name',2,20,30).to_dict());self.assertFalse(d.is_approved)
 def test_load_checks_integrity_and_confinement(self):
  d=self.add();path=self.lib.page_image_path(d,1);data=path.read_bytes()
  for operation in ['missing','changed','symlink','metadata','escape']:
   if path.is_symlink():path.unlink()
   path.write_bytes(data);bad=copy.deepcopy(d)
   if operation=='missing':path.unlink()
   if operation=='changed':path.write_bytes((FIX/'page-1.jpg').read_bytes())
   if operation=='symlink':path.unlink();path.symlink_to(FIX.resolve()/'page-2.jpg')
   if operation=='metadata':bad.pages[0].components=1
   if operation=='escape':bad.pages[0].stored_name='../outside.jpg'
   with self.subTest(operation=operation),self.assertRaises(DocumentLibraryError) as e:self.lib.load_page_images(bad)
   self.assertIn('form',str(e.exception));self.assertIn('page',str(e.exception).lower())
 def test_approval_requires_valid_images_and_manager(self):
  d=self.add()
  for role in ['fi','sales']:
   with self.assertRaises(DocumentLibraryError):self.lib.approve_document('form',SimpleNamespace(role=role))
  self.lib.page_image_path(d,1).unlink()
  with self.assertRaises(DocumentLibraryError):self.approve()

class TestSequentialLibraryWriters(unittest.TestCase):
 def test_stale_writer_cannot_republish_an_old_approved_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   first=DocumentLibrary(tmp)
   first.add_document(source_paths=[FIX/'page-1.jpg'],doc_id='form',title='Form')
   first.approve_document('form',MANAGER)
   stale=DocumentLibrary(tmp)
   replacement=first.replace_page_images('form',[FIX/'page-3.jpg'])
   stale.add_document(source_paths=[FIX/'page-2.jpg'],doc_id='another',title='Another')
   current=DocumentLibrary(tmp).get_document('form')
   self.assertEqual(current.image_revision,replacement.image_revision)
   self.assertFalse(current.is_approved)
 def test_stale_approval_requires_current_revision_review(self):
  with tempfile.TemporaryDirectory() as tmp:
   first=DocumentLibrary(tmp)
   first.add_document(source_paths=[FIX/'page-1.jpg'],doc_id='form',title='Form')
   stale=DocumentLibrary(tmp)
   replacement=first.replace_page_images('form',[FIX/'page-3.jpg'])
   with self.assertRaises(DocumentLibraryError):stale.approve_document('form',MANAGER)
   self.assertFalse(DocumentLibrary(tmp).get_document('form').is_approved)
   reviewed=stale.get_document('form',refresh=True)
   self.assertEqual(reviewed.image_revision,replacement.image_revision)
   approved=stale.approve_document('form',MANAGER)
   self.assertEqual(approved.image_revision,replacement.image_revision)

 def test_stale_placement_edit_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   first=DocumentLibrary(tmp)
   first.add_document(source_paths=[FIX/'page-1.jpg'],doc_id='form',title='Form')
   stale=DocumentLibrary(tmp)
   replacement=first.replace_page_images('form',[FIX/'page-3.jpg'])
   with self.assertRaises(DocumentLibraryError):stale.update_placements('form',[FieldPlacement('buyer_name',1,10,10)])
   self.assertEqual(DocumentLibrary(tmp).get_document('form').image_revision,replacement.image_revision)
