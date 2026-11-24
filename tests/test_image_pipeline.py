import hashlib,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from test_print_pipeline import PrintPipelineTestCase
from test_lease_pack import LeasePackTestCase
from dealerpaperwork.doc_library import DocumentLibrary,FieldPlacement
from dealerpaperwork.models import Buyer
from dealerpaperwork.print_pipeline import PrintSession,PrintPipelineError
from dealerpaperwork.print_config import PrintConfig
FIX=Path(__file__).parent/'fixtures/form_images'
MANAGER=SimpleNamespace(role='manager',display_name='Manager')
class ImageTests:
 def make_image_library(self):
  lib=DocumentLibrary(Path(self.tmp.name)/'images');lib.add_document(source_paths=[FIX/'page-1.jpg',FIX/'page-2.jpg'],doc_id='form',title='Form',when_it_prints=['retail_new','lease'],fields=[FieldPlacement('buyer_name',1,20,55)]);lib.approve_document('form',MANAGER);return lib
 def image_session(self,deal,lib,builder=None):
  return PrintSession.from_setup(deal=deal,setup=self.setup_path,print_config=PrintConfig(path=self.config_path),session_dir=self.sessions_dir,provider=self.provider,audit_log=self.audit,library=DocumentLibrary(lib.library_dir),actor=self.fi,document_payload_builder=builder)
 def get_deal(self,certify=True):return self.make_lease_deal(certify=certify) if isinstance(self,LeasePackTestCase) else self.make_deal(certify=certify)
 def test_retail_and_lease_submit_composed_pdf(self):
  lib=self.make_image_library();ps=self.image_session(self.get_deal(),lib);ps.print_item('form');job=self.provider.jobs[-1];d=lib.get_document('form');self.assertIn((FIX/'page-2.jpg').read_bytes(),job.payload);self.assertIn(b'Sample Buyer',job.payload);self.assertNotIn(b'DRAFT',job.payload);self.assertEqual(job.paper,'Letter');self.assertEqual(job.tray,'Tray 1');self.assertEqual(job.copies,ps.item('form').job['copies']);self.assertEqual(ps.item('form').job['library_revision'],d.image_revision);self.assertEqual(ps.item('form').job['library_page_sha256'],{str(p.page):p.sha256 for p in d.pages});event=self.audit.read_events()[-1];self.assertEqual(event.details['library_revision'],d.image_revision)
 def test_missing_image_fails_before_document_submission(self):
  lib=self.make_image_library();ps=self.image_session(self.get_deal(),lib);lib.page_image_path(lib.get_document('form'),2).unlink();
  with self.assertRaises(PrintPipelineError):ps.print_all()
  self.assertEqual(self.provider.jobs_for('form'),[]);self.assertEqual(ps.item('form').status,'failed');self.assertIn('page 2',ps.item('form').error);saved=json.loads(ps.file_path.read_text());self.assertEqual(saved['items']['form']['status'],'failed');self.assertEqual(self.audit.read_events()[-1].event_type,'document_print_failed')
 def test_resume_after_image_repair_does_not_duplicate(self):
  lib=self.make_image_library();deal=self.get_deal();ps=self.image_session(deal,lib);path=lib.page_image_path(lib.get_document('form'),2);data=path.read_bytes();first=ps.documents[0].doc_id;ps.print_item(first);path.unlink()
  with self.assertRaises(PrintPipelineError):ps.print_all()
  count=len(self.provider.jobs);path.write_bytes(data);loaded=PrintSession.load(deal.deal_id,deal=deal,setup=self.setup_path,print_config=PrintConfig(path=self.config_path),session_dir=self.sessions_dir,provider=self.provider,audit_log=self.audit,library=lib);result=loaded.resume();self.assertIn('form',result['printed']);self.assertIsNone(result['failed']);self.assertEqual(len(self.provider.jobs_for(first)),1);self.assertEqual(len(self.provider.jobs_for('form')),1);self.assertEqual(result['printed'][-1],'paper_packaging_checklist')
 def test_cached_session_cannot_print_unapproved_replacement(self):
  lib=self.make_image_library();ps=self.image_session(self.get_deal(),lib);other=DocumentLibrary(lib.library_dir);new=other.replace_page_images('form',[FIX/'page-3.jpg']);other.update_placements('form',[FieldPlacement('buyer_name',1,35,55)])
  with self.assertRaises(PrintPipelineError):ps.print_item('form')
  self.assertEqual(self.provider.jobs_for('form'),[]);other.approve_document('form',MANAGER)
  with self.assertRaises(PrintPipelineError):ps.print_item('form')
  ps=self.image_session(ps.deal,other);ps.print_item('form');payload=self.provider.jobs_for('form')[0].payload;self.assertIn((FIX/'page-3.jpg').read_bytes(),payload);self.assertEqual(ps.item('form').job['library_revision'],new.image_revision);self.assertIn(b'99.21',payload)
 def test_reload_retains_unapproved_required_form(self):
  lib=self.make_image_library();deal=self.get_deal();ps=self.image_session(deal,lib)
  first=ps.documents[0].doc_id;ps.print_item(first)
  other=DocumentLibrary(lib.library_dir);replacement=other.replace_page_images('form',[FIX/'page-3.jpg'])
  loaded=PrintSession.load(deal.deal_id,deal=deal,setup=self.setup_path,print_config=PrintConfig(path=self.config_path),session_dir=self.sessions_dir,provider=self.provider,audit_log=self.audit,library=DocumentLibrary(lib.library_dir))

  with self.assertRaises(PrintPipelineError):loaded.resume()
  self.assertEqual(self.provider.jobs_for('form'),[]);self.assertIn('form',loaded.items);self.assertEqual(loaded.documents[-1].doc_id,'paper_packaging_checklist');self.assertEqual(len(self.provider.jobs_for(first)),1)
  other.approve_document('form',MANAGER)
  with self.assertRaises(PrintPipelineError):loaded.resume()
  loaded=self.image_session(deal,other);loaded.resume();self.assertEqual(len(self.provider.jobs_for('form')),1);self.assertEqual(loaded.item('form').job['library_revision'],replacement.image_revision);self.assertEqual(len(self.provider.jobs_for(first)),2)  # a changed approved pack is a new revision
 def test_live_certification_controls_each_submission(self):
  lib=self.make_image_library();deal=self.get_deal();ps=self.image_session(deal,lib);deal.set_buyer(Buyer(full_name='Edited Buyer',residence_county='Sample County'));self.assertFalse(deal.is_certified)
  with self.assertRaises(PrintPipelineError):ps.print_item('form')
  ps=self.image_session(deal,lib);builtin=ps.documents[0].doc_id;ps.print_item(builtin);ps.print_item('form');self.assertIn('DRAFT',str(self.provider.jobs_for(builtin)[0].payload));self.assertIn(b'DRAFT',self.provider.jobs_for('form')[0].payload)
 def test_draft_then_certified_prints_clean(self):
  lib=self.make_image_library();deal=self.get_deal(False);ps=self.image_session(deal,lib);deal.certify()
  with self.assertRaises(PrintPipelineError):ps.print_item('form')
  ps=self.image_session(deal,lib);ps.print_item('form');self.assertNotIn(b'DRAFT',self.provider.jobs_for('form')[0].payload)
 def test_saved_flags_never_authorize_clean(self):
  lib=self.make_image_library();deal=self.get_deal();ps=self.image_session(deal,lib);deal.set_buyer(Buyer(full_name='Edited Buyer',residence_county='Sample County'));loaded=PrintSession.load(deal.deal_id,deal=deal,setup=self.setup_path,print_config=PrintConfig(path=self.config_path),session_dir=self.sessions_dir,provider=self.provider,library=lib);loaded.print_item('form');self.assertIn(b'DRAFT',self.provider.jobs_for('form')[0].payload)
 def test_drill_reprint_and_injected_hook(self):
  lib=self.make_image_library();deal=self.get_deal();ps=self.image_session(deal,lib);ps.print_item('form');ps.void_item('form',actor=self.fi,reason='spoiled');ps.reprint_item('form');self.assertIn(b'REPRINT',self.provider.jobs_for('form')[-1].payload);ps.void_item('form',actor=self.fi,reason='practice');deal.is_drill=True;deal.save();ps=self.image_session(deal,lib);ps.print_item('form');self.assertIn(b'SAMPLE',self.provider.jobs_for('form')[-1].payload)
  captured={}
  def builder(document,deal,**flags):captured.update(flags);return b'custom'
  other=self.get_deal();hook=self.image_session(other,lib,builder);other.set_buyer(Buyer(full_name='Edited',residence_county='Sample County'))
  with self.assertRaises(PrintPipelineError):hook.print_item('form')
  hook=self.image_session(other,lib,builder);hook.print_item('form');self.assertTrue(captured['draft']);self.assertEqual(self.provider.jobs_for('form')[-1].payload,b'custom')
class TestRetailImages(ImageTests,PrintPipelineTestCase):pass
class TestLeaseImages(ImageTests,LeasePackTestCase):pass
