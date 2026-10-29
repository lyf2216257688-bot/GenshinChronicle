"""One-pass RU catalog, exact upstream reprojection, typed quotations."""
import gzip
import json
import sqlite3
from contextlib import closing
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path

from .. import retrieval_units as ru
from .common import copy, digest, inside, read, require, seal, sha, verify, write


def prepare_sources(root, canonical_path, ru_path, source_ids):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    canonical, manifest = read(canonical_path), read(ru_path)
    entries = ru._validate_manifest(canonical)
    require(manifest['status'] == 'complete' and manifest['retrieval_unit_schema_version'] == ru.RETRIEVAL_UNIT_SCHEMA_VERSION,
            'Unsupported/incomplete RU build')
    require(manifest['canonical_input']['canonical_run_id'] == canonical['canonical_run_id'], 'Canonical run differs')
    config = ru.RetrievalUnitBuildConfig(**{k: manifest['projection_config'][k]
                                            for k in ('text_fragment_chars', 'structured_fragment_chars')})
    input_identity=ru._canonical_input_identity(canonical,entries)
    require(manifest['canonical_input']['canonical_input_identity']==input_identity and
            manifest['build_identity']==ru._build_identity(canonical,input_identity,config) and
            manifest['projection_config']==config.projection_dict(),'RU/Canonical snapshot or projection identity differs')
    selected = [(i, e) for i, e in enumerate(entries) if e['parsed_identity_key'] in source_ids]
    require(len(selected) == len(set(source_ids)) and bool(selected), 'Missing/duplicate source selection')
    descriptor = manifest['artifacts']['retrieval_units']
    artifact = inside(Path(ru_path).parent.parent, descriptor['path'])
    require(sha(artifact) == descriptor['sha256'] and artifact.stat().st_size == descriptor['byte_count'], 'RU artifact differs')
    # Validate all companion ledgers as well, without loading them into memory.
    for key, item in manifest['artifacts'].items():
        path = inside(Path(ru_path).parent.parent, item['path'])
        require(sha(path) == item['sha256'] and path.stat().st_size == item['byte_count'], 'RU companion differs: ' + key)
    documents = []
    wanted = {e['record_id'] for _, e in selected}
    with closing(sqlite3.connect(root / 'locator.sqlite')) as db:
        db.execute('create table rows(uid text primary key, rid text not null, body text not null)')
        db.execute('create index by_record on rows(rid)')
        count = 0
        with gzip.open(artifact, 'rt', encoding='utf8') as handle:
            for line in handle:
                row = json.loads(line)
                count += 1
                rid = row['source']['canonical_address']['record_id']
                if rid in wanted:
                    db.execute('insert into rows values(?,?,?)', (row['unit_id'], rid, json.dumps(row, ensure_ascii=False)))
        require(count == descriptor['row_count'], 'RU row count differs')
        for index, entry in selected:
            original = Path(entry['canonical_record_path']).resolve()
            require(original.is_relative_to(Path(canonical_path).resolve().parent.parent), 'Canonical path escapes run')
            record = ru._load_record(entry, index)
            expected, skips = ru._validate_record(record, entry, index, config,
                                                  source=canonical['source'], locale=canonical['locale'])
            stored = {uid: json.loads(body) for uid, body in db.execute('select uid,body from rows where rid=?', (entry['record_id'],))}
            require(stored == {r['unit_id']: r for r in expected}, 'RU full row/selector/lineage reprojection differs')
            name = entry['record_id'] + '.json'
            copy(original, root / 'canonical' / name)
            units = []
            for section in record['sections']:
                for unit in section['units']:
                    matched = [r['unit_id'] for r in expected if
                               r['source']['canonical_address']['section_ordinal'] == section['ordinal'] and
                               r['source']['canonical_address']['canonical_unit_ordinal'] == unit['ordinal']]
                    units.append({'section': section['ordinal'], 'unit': unit['ordinal'], 'kind': unit['kind'],
                                  'ru_ids': matched, 'disposition': 'AVAILABLE' if matched else 'EMPTY_OR_UPSTREAM_SKIPPED'})
            documents.append({'source_id': record['source_identity']['key'], 'record_id': record['record_id'],
                              'title': record['record_metadata']['name'], 'canonical_file': 'canonical/' + name,
                              'canonical_sha256': sha(original), 'original_path': str(original),
                              'entry': entry, 'entry_index': index, 'inventory': units, 'upstream_skips': skips})
        db.commit()
    write(root / 'sources.json', {'documents': documents, 'canonical_sha256': sha(canonical_path),
                                  'ru_manifest_sha256': sha(ru_path), 'ru_build': manifest['build_identity'],
                                  'projection_config': manifest['projection_config'], 'source': canonical['source'],
                                  'locale': canonical['locale'], 'scanned_ru_rows': count})
    seal(root)
    return documents


class Sources:
    def __init__(self, root):
        self.root = Path(root)
        verify(root)
        self.manifest = read(self.root / 'sources.json')
        self.documents = {d['source_id']: d for d in self.manifest['documents']}
        self._cache=OrderedDict()
        self.db = sqlite3.connect('file:' + (self.root / 'locator.sqlite').resolve().as_posix() + '?mode=ro', uri=True)

    def close(self):
        self.db.close()

    def rows(self, source):
        if source in self._cache:
            self._cache.move_to_end(source)
            return self._cache[source]
        d = self.documents[source]
        rows = [json.loads(body) for (body,) in self.db.execute('select body from rows where rid=?', (d['record_id'],))]
        rows=sorted(rows, key=lambda r: (r['source_order'], r['unit_id']))
        self._cache[source]=rows
        if len(self._cache)>2:
            self._cache.popitem(last=False)
        return rows

    def iter_rows(self, source, kind):
        rid=self.documents[source]['record_id']
        for (body,) in self.db.execute("select body from rows where rid=? and json_extract(body,'$.source.canonical_address.canonical_unit_kind')=? order by uid",(rid,kind)):
            yield json.loads(body)

    def row(self, uid):
        value = self.db.execute('select body from rows where uid=?', (uid,)).fetchone()
        require(value is not None, 'Unknown RU')
        return json.loads(value[0])

    def quote(self, uid, start=0, end=None):
        row = self.row(uid)
        text = row['retrieval_visible_text']
        end = len(text) if end is None else end
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(text), 'Invalid RU quote range')
        address = row['source']['canonical_address']
        source = address['source_identity_key']
        d = self.documents[source]
        result = {'id': digest([uid, start, end]), 'source_id': source, 'record_id': d['record_id'],
                  'canonical_sha256': d['canonical_sha256'], 'unit_id': uid, 'ru_start': start, 'ru_end': end,
                  'text': text[start:end], 'quote_basis': 'verified_ru_projection',
                  'canonical_address': address, 'nested_selector': row['nested_selector'],
                  'fragment_selector': row['fragment_selector'], 'structure': row['structure'],
                  'provenance': row['source']['provenance'], 'content_role': row['source']['content_role']}
        result['text_sha256'] = sha256(result['text'].encode()).hexdigest()
        # Rich text has an exact Canonical range; other kinds deliberately do not.
        if address['canonical_unit_kind'] == 'rich_text':
            record = read(self.root / d['canonical_file'])
            section = next(s for s in record['sections'] if s['ordinal'] == address['section_ordinal'])
            unit = next(u for u in section['units'] if u['ordinal'] == address['canonical_unit_ordinal'])
            siblings = [r for r in self.rows(source) if r['source']['canonical_address'] == address]
            siblings.sort(key=lambda r: r['fragment_selector'].get('fragment_index', 0))
            require(''.join(r['retrieval_visible_text'] for r in siblings) == unit['value']['normalized_text'], 'Rich occurrence incomplete')
            offset = sum(len(r['retrieval_visible_text']) for r in siblings[:next(i for i,r in enumerate(siblings) if r['unit_id']==uid)])
            result.update(canonical_start=offset+start, canonical_end=offset+end)
        return result

    def verify_quote(self, quote):
        require(self.quote(quote['unit_id'], quote['ru_start'], quote['ru_end']) == quote, 'Typed quote differs')
