#!/usr/bin/env python3
"""Check local document links, anchors and fenced source structure.

No fixed wording, API inventories, diagram quotas or historical Git lookups.
Real dependency boundaries are checked by check_import_dependencies.py.
"""
from __future__ import annotations
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
METADATA = tomllib.loads((ROOT / 'pyproject.toml').read_text())
SOURCE_ROOT = METADATA['tool']['setuptools']['packages']['find']['where'][0]
FENCED = re.compile(r'^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$', re.MULTILINE | re.DOTALL)
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\'"][^\n]*[\'"])?\s*\)')
REFERENCE = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)', re.MULTILINE)


def _current_documents():
    roots = [ROOT / name for name in (SOURCE_ROOT, 'docs', 'tests', 'examples', 'tools')]
    documents = []
    ignored = {'output', 'build', '__pycache__'}
    for root in roots:
        for directory, subdirectories, filenames in os.walk(root):
            # Prune generated trees before visiting their contents.
            subdirectories[:] = [name for name in subdirectories if name not in ignored]
            documents.extend(Path(directory) / name for name in filenames if name.endswith('.md'))
    documents.extend(ROOT / name for name in ('README.md', 'README.zh-CN.md', 'AGENTS.md'))
    return tuple(sorted(set(documents)))


class _HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if not value:
                continue
            if name in {'href', 'src'}:
                self.links.append(value)
            if name == 'id' or (tag == 'a' and name == 'name'):
                self.anchors.add(value)


def _anchors(path):
    text = path.read_text(encoding='utf-8')
    html = _HTMLReferences()
    html.feed(text)
    anchors = set(html.anchors)
    if path.suffix == '.md':
        occurrences = {}
        headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*\s*$', FENCED.sub('', text), re.MULTILINE)
        for heading in headings:
            plain_heading = re.sub(r'<[^>]+>', '', heading).lower()
            slug = re.sub(r'[^\w\- ]', '', plain_heading).replace(' ', '-')
            count = occurrences.get(slug, 0)
            occurrences[slug] = count + 1
            anchor = f'{slug}-{count}' if count else slug
            anchors.add(anchor)
    return anchors


def _validate_links(documents):
    broken = []
    anchor_cache = {}
    for path in documents:
        source = path.read_text(encoding='utf-8')
        links = []
        if path.suffix == '.md':
            source = FENCED.sub('', source)
            links = [*LINK.findall(source), *REFERENCE.findall(source)]
            # Inline code describes paths/commands; only actual HTML is parsed below.
            source = re.sub(r'`[^`\n]+`', '', source)
        html = _HTMLReferences()
        html.feed(source)
        links.extend(html.links)
        for raw in links:
            parts = urlsplit(raw.strip('<>'))
            if parts.scheme or parts.netloc:
                continue
            destination = path.resolve()
            if parts.path:
                destination = (path.parent / unquote(parts.path)).resolve()
            if not destination.exists():
                broken.append(f'{path}: {raw} (missing target)')
                continue
            if parts.fragment and destination.suffix in {'.md', '.html'}:
                if destination not in anchor_cache:
                    anchor_cache[destination] = _anchors(destination)
                if unquote(parts.fragment) not in anchor_cache[destination]:
                    broken.append(f'{path}: {raw} (missing anchor)')
    if broken:
        raise AssertionError('broken documentation links:\n' + '\n'.join(broken))


def main():
    documents = _current_documents()
    for path in documents:
        if path.suffix == '.md':
            source = path.read_text(encoding='utf-8')
            # Catch an unclosed fence, without prescribing how many diagrams exist.
            opening = None
            for line in source.splitlines():
                match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
                if not match:
                    continue
                marker = match[1]
                if opening is None:
                    opening = marker
                elif (
                    marker[0] == opening[0]
                    and len(marker) >= len(opening)
                    and not match[2].strip()
                ):
                    opening = None
            if opening:
                raise AssertionError(f'unclosed code fence: {path}')
    _validate_links(documents)
    print(f'Documents checked: {len(documents)}')
    print('documentation result: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
