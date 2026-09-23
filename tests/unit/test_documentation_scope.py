"""Archived source assets do not hide broken links in maintained documents."""
import importlib.util
from pathlib import Path
import pytest


@pytest.fixture
def checker(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/check_documentation.py'
    spec = importlib.util.spec_from_file_location('documentation_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.setattr(module, 'ROOT', root)
    return module


def test_archives_are_checked_as_link_targets_not_local_websites(checker):
    root = checker.ROOT
    archive = root / 'research/topic/references/source.html'
    archive.parent.mkdir(parents=True)
    archive.write_text('<h1 id="source">Original</h1><script src="/original-site.js"></script>')
    index = root / 'research/topic/README.md'
    index.write_text('[Source](references/source.html#source)')
    documents = checker._current_documents()
    assert index in documents and archive not in documents
    checker._validate_links(documents)
    index.write_text('[Source](references/source.html#missing)')
    with pytest.raises(AssertionError, match='missing anchor'):
        checker._validate_links(documents)
    archive.unlink()
    with pytest.raises(AssertionError, match='missing target'):
        checker._validate_links(documents)


def test_authored_research_html_links_are_still_checked(checker):
    page = checker.ROOT / 'research/local-view.html'
    page.parent.mkdir()
    page.write_text('<script src="missing.js"></script>')
    assert page in checker._current_documents()
    with pytest.raises(AssertionError, match='missing target'):
        checker._validate_links(checker._current_documents())


def test_documentation_inventory_stays_inside_project(checker):
    (checker.ROOT.parent / 'README.md').write_text('[Unrelated](absent)')
    (checker.ROOT / 'README.md').write_text('Project')
    assert checker._current_documents() == (checker.ROOT / 'README.md',)
