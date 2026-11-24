"""Storage freshness must never silently replace a displayed review token."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.doc_library import DocumentLibrary, DocumentLibraryError, FieldPlacement
from dealerpaperwork.document_setup import DocumentSetupActions
from dealerpaperwork.users import UserStore

FIX = Path(__file__).parent / 'fixtures/form_images'
tk = types.ModuleType('tkinter')
tk.filedialog = types.ModuleType('filedialog')
tk.messagebox = types.ModuleType('messagebox')
tk.ttk = types.ModuleType('ttk')
spec = importlib.util.spec_from_file_location('dealerpaperwork._review_binding_ui',
    Path(__file__).resolve().parents[1] / 'src/dealerpaperwork/docs_ui.py')
ui = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'tkinter': tk}):
    spec.loader.exec_module(ui)


class Variable:
    def __init__(self, value=''): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Widget:
    def __init__(self): self.options, self.items = {}, []
    def configure(self, **kwargs): self.options.update(kwargs)
    config = configure
    def delete(self, *args): self.items = []
    def insert(self, *args): self.items.append(args[-1])
    def curselection(self): return (0,)
    def selection_set(self, *args): pass


class DocumentReviewBinding(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        users = UserStore(self.root / 'users.json')
        admin = users.create_user(actor=None, username='admin', display_name='Sample Admin', role='admin', secret='sample-admin')
        self.manager = users.create_user(actor=admin, username='manager', display_name='Sample Manager', role='manager', secret='sample-manager')
        self.sales = users.create_user(actor=admin, username='sales', display_name='Sample Sales', role='sales', secret='sample-sales')
        self.library = DocumentLibrary(self.root / 'documents')
        self.library.add_document(source_paths=[FIX / 'page-1.jpg'], doc_id='form', title='Synthetic form',
            fields=[FieldPlacement('buyer_name', 1, 20, 55)])

    def window(self, actor=None):
        window = object.__new__(ui.DocumentsWindow)
        window.library = DocumentLibrary(self.library.library_dir)
        window.actions = DocumentSetupActions(window.library)
        window.current_user = actor or self.manager
        window.selected_id = None
        for name in ('doc_list', 'page_choice', 'source_info', 'placement_list', 'page_list'):
            setattr(window, name, Widget())
        variables = {}
        window.var = lambda name, initial='': variables.setdefault(name, Variable(initial))
        window.condition_vars = {'retail_new': Variable(True)}
        window.messages = []
        window._say = lambda message, **kwargs: window.messages.append(message)
        window._refresh_documents()
        window._on_select()
        return window

    def replace(self):
        editor = self.window(self.sales)
        editor.actions.set_page_paths([FIX / 'page-3.jpg'])
        editor._replace_images()
        return self.current()

    def current(self):
        return DocumentLibrary(self.library.library_dir).get_document('form')

    def assert_blocked_until_explicit_reload(self, window, changed):
        displayed = window.source_info.options['text']
        for _ in range(2):
            window._approve()
            self.assertFalse(self.current().is_approved)
            self.assertEqual(window.source_info.options['text'], displayed)
        window._reload_selected()
        window._approve()
        self.assertTrue(self.current().is_approved)
        self.assertEqual(self.current().image_revision, changed.image_revision)

    def test_repeated_approve_does_not_review_replacement(self):
        window = self.window()
        self.assert_blocked_until_explicit_reload(window, self.replace())

    def test_unrelated_add_does_not_review_the_selected_form(self):
        window = self.window()
        changed = self.replace()
        window.actions.set_page_paths([FIX / 'page-2.jpg'])
        window.var('doc_id').set('another')
        window.var('title').set('Another synthetic form')
        window._add_document()
        self.assert_blocked_until_explicit_reload(window, changed)

    def test_sample_success_and_failure_do_not_review_replacement(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                # Each explicit selection pins the current record, then another editor changes it.
                window = self.window()
                changed = self.replace()
                with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(self.root / 'sample.pdf'), create=True):
                    if failure:
                        with patch('dealerpaperwork.document_setup.write_overlay_pdf', side_effect=OSError('sample output failed')):
                            window._test_render()
                    else:
                        window._test_render()
                self.assert_blocked_until_explicit_reload(window, changed)

    def test_placement_only_change_is_bound_even_without_new_image_revision(self):
        window = self.window()
        editor = DocumentLibrary(self.library.library_dir)
        old_revision = editor.get_document('form').image_revision
        changed = editor.update_placements('form', [FieldPlacement('buyer_name', 1, 40, 60)])
        self.assertEqual(changed.image_revision, old_revision)
        window.library.get_document('form', refresh=True)
        self.assert_blocked_until_explicit_reload(window, changed)

    def test_sales_cannot_approve_after_an_explicit_reload(self):
        window = self.window(self.sales)
        window._approve()
        self.assertFalse(self.current().is_approved)

    def test_stale_remove_uses_displayed_placements_before_freshness_check(self):
        window = self.window()
        editor = DocumentLibrary(self.library.library_dir)
        editor.update_placements('form', [])
        window.actions.write_sample('form', self.root / 'sample.pdf')
        # Row zero is still displayed, but the refreshed storage record has none.
        window._remove_placement()
        self.assertEqual(self.current().fields, [])
        self.assertTrue(any('changed since' in message for message in window.messages))

    def test_library_rejections_and_unrelated_adds_do_not_advance_review(self):
        stale = DocumentLibrary(self.library.library_dir)
        changed = self.replace()
        for _ in range(2):
            with self.assertRaises(DocumentLibraryError): stale.approve_document('form', self.manager)
        stale.add_document(source_paths=[FIX / 'page-2.jpg'], doc_id='another', title='Another')
        with self.assertRaises(DocumentLibraryError): stale.approve_document('form', self.manager)
        stale.get_document('form', refresh=True)
        self.assertEqual(stale.approve_document('form', self.manager).image_revision, changed.image_revision)

    def test_headless_sample_refresh_also_preserves_review_expectation(self):
        stale = DocumentLibrary(self.library.library_dir)
        self.replace()
        DocumentSetupActions(stale).write_sample('form', self.root / 'sample.pdf')
        with self.assertRaises(DocumentLibraryError): stale.approve_document('form', self.manager)
