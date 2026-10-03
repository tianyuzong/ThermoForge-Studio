"""Bind independent numerical comparisons to the unchanged saved study fields."""
import hashlib,json
from pathlib import Path
from .integrity import checked_raw_fields


def read_verification(folder):
    folder=Path(folder)
    path=folder/'verification.json'
    if not path.is_file():return None
    try:
        record=json.loads(path.read_text(encoding='utf-8'))
        for name,key in [('config.json','config_sha256'),('summary.json','summary_sha256')]:
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=record[key]:raise ValueError('Study changed')
        checked_raw_fields(folder,record['raw_fields_sha256'])
        if not record['checks'] or not all(c.get('passed') is True for c in record['checks']):raise ValueError('Evidence does not match')
        return {**record,'status':'verified'}
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return {'status':'invalid','detail':'独立验证记录与当前工况或实际原始结果不匹配，不能标为已验证'}
