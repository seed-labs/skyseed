#!/usr/bin/env python3
"""Check local document links, anchors and fenced source structure.

No fixed wording, API inventories, diagram quotas or historical Git lookups.
Real dependency boundaries are checked by check_import_dependencies.py.
"""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT
PRODUCTION_ROOT = ROOT / 'src/skyseed'
MERMAID_BLOCK = re.compile(r'```mermaid\s*\n(.*?)```', re.DOTALL)
OWNER_READMES = tuple(sorted(PRODUCTION_ROOT.rglob('README.md')))
FENCED = re.compile(r'^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$', re.MULTILINE | re.DOTALL)
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\'"][^\n]*[\'"])?\s*\)')
REFERENCE = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)', re.MULTILINE)


def _full_repository_documents():
    documents = []
    for directory in ('docs', 'src', 'tools', 'examples', 'templates', 'data', 'research'):
        for path in (ROOT/directory).rglob('*'):
            if (path.suffix in {'.md', '.html'}
                    and not {'output','build','__pycache__'}.intersection(path.relative_to(ROOT).parts)
                    and not _is_source_archive(path)):
                documents.append(path)
    documents.extend(p for p in (ROOT/'README.md',ROOT/'AGENTS.md') if p.exists())
    return tuple(sorted(set(documents)))


def _is_source_archive(path):
    # research/**/references/*.html are downloaded third-party source snapshots,
    # not maintained local websites. Links *to* them (including anchors) are still
    # validated from research indexes and other authored documents below.
    return (path.suffix == '.html' and path.is_relative_to(ROOT/'research')
            and 'references' in path.relative_to(ROOT/'research').parts[:-1])


def _current_documents():
    return tuple(p for p in _full_repository_documents() if not p.is_relative_to(ROOT/'docs/history'))


class _HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.anchors=set()
    def handle_starttag(self, tag, attrs):
        for name,value in attrs:
            if value and name in {'href','src'}:self.links.append(value)
            if value and (name=='id' or tag=='a' and name=='name'):self.anchors.add(value)


def _anchors(path):
    text=path.read_text(encoding='utf-8')
    html=_HTMLReferences();html.feed(text)
    anchors=set(html.anchors)
    if path.suffix=='.md':
        occurrences={}
        for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*\s*$',FENCED.sub('',text),re.MULTILINE):
            slug=re.sub(r'[^\w\- ]','',re.sub(r'<[^>]+>','',heading).lower()).replace(' ','-')
            count=occurrences.get(slug,0);occurrences[slug]=count+1
            anchors.add(slug+(f'-{count}' if count else ''))
    return anchors


def _validate_links(documents):
    broken=[];anchor_cache={}
    for path in documents:
        source=path.read_text(encoding='utf-8')
        if path.suffix=='.md':
            source=FENCED.sub('',source)
            links=[*LINK.findall(source),*REFERENCE.findall(source)]
            # Inline code describes paths/commands; only actual HTML is parsed below.
            source=re.sub(r'`[^`\n]+`','',source)
        else:links=[]
        html=_HTMLReferences();html.feed(source);links.extend(html.links)
        for raw in links:
            url=raw.strip('<>');parts=urlsplit(url)
            if parts.scheme or parts.netloc:continue
            destination=(path.parent/unquote(parts.path)).resolve() if parts.path else path.resolve()
            if not destination.exists():
                broken.append(f'{path}: {raw} (missing target)');continue
            if parts.fragment and destination.suffix in {'.md','.html'}:
                if destination not in anchor_cache:anchor_cache[destination]=_anchors(destination)
                if unquote(parts.fragment) not in anchor_cache[destination]:
                    broken.append(f'{path}: {raw} (missing anchor)')
    if broken:raise AssertionError('broken documentation links:\n'+'\n'.join(broken))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',choices=('focused','full'),default='focused')
    args=parser.parse_args()
    documents=_current_documents() if args.scope=='focused' else _full_repository_documents()
    for path in documents:
        if path.suffix=='.md':
            source=path.read_text()
            # Catch an unclosed fence, without prescribing how many diagrams exist.
            opening=None
            for line in source.splitlines():
                match=re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$',line)
                if match:
                    marker=match[1]
                    if opening is None:opening=marker
                    elif marker[0]==opening[0] and len(marker)>=len(opening) and not match[2].strip():opening=None
            if opening:raise AssertionError(f'unclosed code fence: {path}')
    _validate_links(documents)
    print(f'Documents checked: {len(documents)}')
    print('documentation result: PASS')
    return 0


if __name__=='__main__':raise SystemExit(main())
