import hashlib,json
from forge.verification import read_verification


def test_independent_evidence_is_invalid_after_summary_or_field_identity_changes(tmp_path):
    for name in ('config.json','summary.json'):(tmp_path/name).write_text('{}',encoding='utf-8')
    (tmp_path/'fields.h5').write_bytes(b'actual original fields')
    raw_identity=hashlib.sha256(b'actual original fields').hexdigest()
    raw=tmp_path/'raw-fields.sha256.json';raw.write_text(json.dumps({'sha256':raw_identity}),encoding='utf-8')
    digest=hashlib.sha256(b'{}').hexdigest()
    record={'config_sha256':digest,'summary_sha256':digest,'raw_fields_sha256':raw_identity,'checks':[{'passed':True}]}
    (tmp_path/'verification.json').write_text(json.dumps(record),encoding='utf-8')
    assert read_verification(tmp_path)['status']=='verified'
    (tmp_path/'summary.json').write_text('{"changed":true}',encoding='utf-8')
    assert read_verification(tmp_path)['status']=='invalid'
    (tmp_path/'summary.json').write_text('{}',encoding='utf-8')
    raw.write_text(json.dumps({'sha256':'different-raw-identity'}),encoding='utf-8')
    assert read_verification(tmp_path)['status']=='invalid'


def test_actual_fields_corruption_invalidates_unchanged_sidecar(tmp_path):
    from forge.integrity import file_digest
    for name in ('config.json','summary.json'):(tmp_path/name).write_text('{}',encoding='utf-8')
    raw=tmp_path/'fields.h5';raw.write_bytes(b'original field values')
    checksum=file_digest(raw)
    (tmp_path/'raw-fields.sha256.json').write_text(json.dumps({'sha256':checksum}))
    record={'config_sha256':file_digest(tmp_path/'config.json'),'summary_sha256':file_digest(tmp_path/'summary.json'),
            'raw_fields_sha256':checksum,'checks':[{'passed':True}]}
    (tmp_path/'verification.json').write_text(json.dumps(record))
    assert read_verification(tmp_path)['status']=='verified'
    raw.write_bytes(b'corrupted field values')
    assert read_verification(tmp_path)['status']=='invalid'
