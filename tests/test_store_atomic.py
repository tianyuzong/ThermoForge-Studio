import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from strength import store


def denied(code=5):
    error=PermissionError('temporary Windows file sharing conflict')
    error.winerror=code
    return error


@pytest.mark.skipif(os.name!='nt',reason='Windows replacement retry policy')
@pytest.mark.parametrize('code',[5,32,33])
def test_temporary_replace_failure_retries_complete_payload(tmp_path,monkeypatch,code):
    target=tmp_path/'status.json'
    target.write_text('{"status":"old"}',encoding='utf-8')
    replace=Path.replace
    calls=[]
    def temporarily_denied(source,destination):
        calls.append(source)
        assert json.loads(target.read_text(encoding='utf-8'))=={'status':'old'}
        if len(calls)<3:raise denied(code)
        return replace(source,destination)
    monkeypatch.setattr(Path,'replace',temporarily_denied)
    monkeypatch.setattr(store.time,'sleep',lambda _:None)
    store.write_json(target,{'status':'完成','values':list(range(100))})
    assert json.loads(target.read_text(encoding='utf-8'))=={'status':'完成','values':list(range(100))}
    assert len(calls)==3 and all(p.parent==target.parent and p!=target for p in calls)
    assert list(tmp_path.iterdir())==[target]


@pytest.mark.skipif(os.name!='nt',reason='Windows replacement retry policy')
def test_permanent_replace_failure_preserves_old_json_and_cleans_temp(tmp_path,monkeypatch):
    target=tmp_path/'status.json'
    original='{"status":"old"}'
    target.write_text(original,encoding='utf-8')
    calls=[]
    def always_denied(source,destination):
        calls.append(source)
        raise denied()
    monkeypatch.setattr(Path,'replace',always_denied)
    monkeypatch.setattr(store.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):store.write_json(target,{'status':'new'})
    assert 1<len(calls)<=24
    assert target.read_text(encoding='utf-8')==original
    assert list(tmp_path.iterdir())==[target]


def test_unrelated_write_error_is_not_retried(tmp_path,monkeypatch):
    calls=[]
    def disk_error(source,destination):
        calls.append(source)
        raise OSError('not a transient sharing conflict')
    monkeypatch.setattr(Path,'replace',disk_error)
    with pytest.raises(OSError):store.write_json(tmp_path/'status.json',{'status':'new'})
    assert len(calls)==1
    assert list(tmp_path.iterdir())==[]


def test_concurrent_writers_publish_complete_json_with_unique_temp_files(tmp_path,monkeypatch):
    target=tmp_path/'status.json'
    replace=Path.replace
    staging=[]
    def observed(source,destination):
        staging.append(source)
        value=json.loads(source.read_text(encoding='utf-8'))
        assert value['payload']==str(value['writer'])*2000
        return replace(source,destination)
    monkeypatch.setattr(Path,'replace',observed)
    def write(index):store.write_json(target,{'writer':index,'payload':str(index)*2000})
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(write,range(80)))
    result=json.loads(target.read_text(encoding='utf-8'))
    assert result['payload']==str(result['writer'])*2000
    assert len(set(staging))==80
    assert list(tmp_path.iterdir())==[target]


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handle integration')
def test_windows_exclusive_reader_release_allows_replace(tmp_path):
    target=tmp_path/'status.json'
    target.write_text('{"status":"old"}',encoding='utf-8')
    script=r'''
import ctypes,sys,time
from ctypes import wintypes
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
kernel.CreateFileW.restype=wintypes.HANDLE
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
handle=kernel.CreateFileW(sys.argv[1],0x80000000,0,None,3,0,None)
if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
print('locked',flush=True)
time.sleep(.35)
kernel.CloseHandle(handle)
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(target)],stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        assert child.stdout.readline().strip()=='locked'
        store.write_json(target,{'status':'new'})
        stdout,stderr=child.communicate(timeout=5)
        assert child.returncode==0,stderr
        assert json.loads(target.read_text(encoding='utf-8'))=={'status':'new'}
        assert list(tmp_path.iterdir())==[target]
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
