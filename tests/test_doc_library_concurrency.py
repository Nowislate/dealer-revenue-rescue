"""Two document windows (or two app copies) saving the library at once.

Each change reads the index, checks it, and writes it back. Without one
lock around those three steps, a second writer that saves between
another writer's read and write is silently erased (a lost update).
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

from dealerpaperwork.doc_library import DocumentLibrary, FieldPlacement  # noqa: E402

JPEG = (REPO / 'tests/fixtures/form_images/page-1.jpg').read_bytes()


class SimultaneousLibraryWriters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / 'documents'
        self.page = Path(self.tmp.name) / 'page.jpg'
        self.page.write_bytes(JPEG)

    def add(self, library, doc_id):
        return library.add_document(source_paths=[self.page], doc_id=doc_id, title=doc_id,
                                    when_it_prints=('retail_new',), created_by='synthetic')

    def race(self, first_write, second_write):
        """Run second_write on another thread at the moment first_write
        is about to publish the index, then let both finish."""
        first = DocumentLibrary(self.dir)
        original = first._save
        state = {}

        def save_with_interruption(documents=None):
            if 'thread' not in state:
                state['thread'] = threading.Thread(target=lambda: state.setdefault('result', second_write()))
                state['thread'].start()
                state['thread'].join(timeout=1.0)  # a lock makes the other writer wait here
                state['waited'] = state['thread'].is_alive()
            original(documents)

        first._save = save_with_interruption
        first_write(first)
        state['thread'].join(timeout=30)
        self.assertFalse(state['thread'].is_alive())
        return state

    def test_two_new_forms_saved_at_once_both_survive(self):
        state = self.race(lambda lib: self.add(lib, 'form-a'),
                          lambda: self.add(DocumentLibrary(self.dir), 'form-b'))
        self.assertTrue(state['waited'], 'the second writer must wait for the first')
        ids = {d.doc_id for d in DocumentLibrary(self.dir).list_documents()}
        self.assertEqual(ids, {'form-a', 'form-b'})

    def test_placement_edit_does_not_erase_a_form_added_meanwhile(self):
        self.add(DocumentLibrary(self.dir), 'form-a')

        def edit(lib):
            lib.update_placements('form-a', [FieldPlacement('buyer_name', 1, 20, 30, 11)])

        self.race(edit, lambda: self.add(DocumentLibrary(self.dir), 'form-b'))
        library = DocumentLibrary(self.dir)
        self.assertEqual({d.doc_id for d in library.list_documents()}, {'form-a', 'form-b'})
        self.assertEqual(library.get_document('form-a').fields[0].field_name, 'buyer_name')

    def test_second_editor_of_the_same_form_is_told_to_reload(self):
        self.add(DocumentLibrary(self.dir), 'form-a')
        stale = DocumentLibrary(self.dir)

        def edit(lib):
            lib.update_placements('form-a', [FieldPlacement('buyer_name', 1, 20, 30, 11)])

        def stale_edit():
            try:
                stale.update_placements('form-a', [FieldPlacement('buyer_address', 1, 40, 60, 11)])
            except Exception as exc:
                return exc
            return None

        state = self.race(edit, stale_edit)
        self.assertIn('changed since it was opened', str(state['result']))
        self.assertEqual(DocumentLibrary(self.dir).get_document('form-a').fields[0].field_name, 'buyer_name')


if __name__ == '__main__':
    unittest.main()
