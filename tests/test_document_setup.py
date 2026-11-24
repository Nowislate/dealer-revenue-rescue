import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dealerpaperwork.doc_library import DocumentLibrary,DocumentLibraryError,document_fingerprint
from dealerpaperwork.document_setup import DocumentSetupActions
FIX=Path(__file__).parent/'fixtures/form_images'
class TestDocumentSetup(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.lib=DocumentLibrary(self.root/'library');self.actions=DocumentSetupActions(self.lib)
 def add(self):
  self.actions.set_page_paths([FIX/'page-1.jpg',FIX/'page-2.jpg']);return self.actions.add_document(doc_id='form',title='Form',when_it_prints=['retail_new'])
 def test_order_move_remove_and_add(self):
  a=self.actions;a.set_page_paths([FIX/'page-1.jpg',FIX/'page-2.jpg',FIX/'page-1.jpg']);self.assertEqual(a.move_page(0,-1),0);self.assertEqual(a.move_page(2,1),2);self.assertEqual(a.move_page(0,1),1);a.remove_page(2);d=a.add_document(doc_id='form',title='Form',when_it_prints=['retail_new']);self.assertEqual([p.source_filename for p in d.pages],['page-2.jpg','page-1.jpg']);a.remove_page(0);self.assertEqual(len(self.lib.get_document('form').pages),2)
  with self.assertRaises(ValueError):a.move_page(0,2)
 def test_replace_forwards_order_and_revokes(self):
  self.add();a=self.actions;a.set_page_paths([FIX/'page-3.jpg',FIX/'page-1.jpg']);d=a.replace_page_images('form');self.assertEqual([p.source_filename for p in d.pages],['page-3.jpg','page-1.jpg']);self.assertFalse(d.is_approved)
 def test_sample_composes_current_pages(self):
  self.add();self.lib.replace_page_images('form',[FIX/'page-3.jpg']);target=self.root/'sample.pdf';self.assertEqual(self.actions.write_sample('form',target),target);data=target.read_bytes();self.assertIn((FIX/'page-3.jpg').read_bytes(),data);self.assertIn(b'SAMPLE',data);self.assertNotIn((FIX/'page-2.jpg').read_bytes(),data)
 def test_failed_sample_preserves_existing_output(self):
  self.add();target=self.root/'sample.pdf';target.write_bytes(b'previous good sample')
  for where in ['dealerpaperwork.document_setup.write_overlay_pdf','pathlib.Path.replace']:
   with patch(where,side_effect=OSError('disk failed')):
    with self.assertRaises(OSError):self.actions.write_sample('form',target)
   self.assertEqual(target.read_bytes(),b'previous good sample');self.assertEqual(list(self.root.glob('.sample-*')),[])
 def test_failed_replace_and_empty_selection(self):
  self.add();before=self.lib.records_path.read_bytes();self.actions.set_page_paths([])
  with self.assertRaises(DocumentLibraryError):self.actions.replace_page_images('form')
  self.actions.set_page_paths([FIX/'page-3.jpg'])
  with patch.object(self.lib,'_save',side_effect=OSError('disk failed')):
   with self.assertRaises(DocumentLibraryError):self.actions.replace_page_images('form')
  self.assertEqual(before,self.lib.records_path.read_bytes())
 def test_controller_imports_without_tkinter(self):
  import subprocess
  result=subprocess.run([sys.executable,'-c',"import sys; sys.path.insert(0,'src'); import dealerpaperwork.document_setup; assert 'tkinter' not in sys.modules"],capture_output=True);self.assertEqual(result.returncode,0,result.stderr)

class TestApprovalStatusAdapter(unittest.TestCase):
 def test_save_reports_new_approval_requirement_without_a_display(self):
  # Execute the actual adapter method; replace only the optional Tk import,
  # never the library behavior. This does not assert Windows GUI coverage.
  import importlib.util,types
  from types import SimpleNamespace
  tk=types.ModuleType('tkinter')
  tk.filedialog=types.ModuleType('filedialog')
  tk.messagebox=types.ModuleType('messagebox')
  tk.ttk=types.ModuleType('ttk')
  path=Path(__file__).resolve().parents[1]/'src/dealerpaperwork/docs_ui.py'
  spec=importlib.util.spec_from_file_location('dealerpaperwork._status_adapter_test',path)
  module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,{'tkinter':tk}):spec.loader.exec_module(module)
  with tempfile.TemporaryDirectory() as tmp:
   lib=DocumentLibrary(tmp)
   lib.add_document(source_paths=[FIX/'page-1.jpg'],doc_id='form',title='Form')
   approved=lib.approve_document('form',SimpleNamespace(role='manager',display_name='Manager'))
   messages=[]
   window=SimpleNamespace(library=lib,reviewed_fingerprint=document_fingerprint(approved),_selected_document=lambda:approved,_say=lambda message,**kw:messages.append(message),_refresh_documents=lambda:None,_show_document=lambda document:None)
   module.DocumentsWindow._save_placements(window)
   self.assertFalse(lib.get_document('form').is_approved)
   self.assertIn('must approve',messages[-1])
