"""Headless ordered intake and atomic SAMPLE export; no UI dependencies."""
from pathlib import Path
import tempfile
from typing import Sequence, Union
from .doc_library import DocumentLibrary, DealerDocument, sample_field_values
from .pdf_overlay import write_overlay_pdf

class DocumentSetupActions:
    def __init__(self, library: DocumentLibrary):
        self.library = library
        self.page_paths: list[Path] = []

    def set_page_paths(self, source_paths: Sequence[Union[str, Path]]) -> None:
        self.page_paths = [Path(p) for p in source_paths]

    def move_page(self, index: int, delta: int) -> int:
        if delta not in (-1, 1):
            raise ValueError("Move one page up or down.")
        if not 0 <= index < len(self.page_paths):
            raise IndexError("Select a page first.")
        target = index + delta
        if not 0 <= target < len(self.page_paths):
            return index
        self.page_paths[index], self.page_paths[target] = self.page_paths[target], self.page_paths[index]
        return target

    def remove_page(self, index: int) -> None:
        del self.page_paths[index]

    def add_document(self, *, doc_id: str, title: str, when_it_prints: Sequence[str], created_by: str = "") -> DealerDocument:
        return self.library.add_document(source_paths=self.page_paths, doc_id=doc_id, title=title, when_it_prints=when_it_prints, created_by=created_by)

    def replace_page_images(self, doc_id: str, *, expected_fingerprint=None) -> DealerDocument:
        return self.library.replace_page_images(doc_id, self.page_paths, expected_fingerprint=expected_fingerprint)

    def write_sample(self, doc_id: str, target: Union[str, Path]) -> Path:
        record = self.library.get_document(doc_id, refresh=True, reviewed=False)
        images = self.library.load_page_images(record)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".sample-", suffix=".pdf", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            write_overlay_pdf(temporary, record, sample_field_values(), banner="SAMPLE", page_images=images)
            temporary.replace(target)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return target
