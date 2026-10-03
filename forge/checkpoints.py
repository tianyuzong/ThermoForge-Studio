"""Append-only physical-step journal. Trial iterations are never checkpoints."""
import json
from pathlib import Path
import h5py,numpy as np
from collections.abc import Sequence
from strength.result_catalog import finite_json
from strength.store import write_json


def append_step(folder,index,time,temperature,structure=None,audit=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'physical-steps.h5'
    with h5py.File(path,'a') as f:
        f.attrs.update(checkpoint_schema_version=1,meaning='Converged physical states only; not a final result archive')
        key=str(index)
        # A worker restart recomputes the physical chronology; replace only its
        # own checkpoint index, never a historical study or its final archive.
        if key in f:del f[key]
        g=f.create_group(key);g.attrs.update(committed=False,time_s=float(time))
        g.create_dataset('temperature_C',data=temperature,compression='gzip')
        if structure:
            state=g.create_group('structure');metadata={}
            for name,value in structure.items():
                if isinstance(value,np.ndarray):state.create_dataset(name,data=value,compression='gzip' if value.ndim else None)
                elif name in ('contact_stresses','contact_displacements'):
                    state.create_dataset(name,data=np.asarray(value).reshape(-1,5),compression='gzip')
                else:metadata[name]=value
            # Dataset, rather than an HDF5 attribute, permits long solver notes.
            state.create_dataset('metadata_json',data=json.dumps(finite_json(metadata),ensure_ascii=False),dtype=h5py.string_dtype('utf-8'))
        audit=audit or {}
        g.create_dataset('audit_json',data=json.dumps(finite_json({k:v for k,v in audit.items() if k!='contact'}),ensure_ascii=False),dtype=h5py.string_dtype('utf-8'))
        contact=g.create_group('contact')
        for name,record in audit.get('contact',{}).items():
            pair=contact.create_group(name)
            for field,value in record.items():
                if isinstance(value,np.ndarray):pair.create_dataset(field,data=value,compression='gzip')
                elif isinstance(value,str):pair.attrs[field]=value
        f.flush();g.attrs['committed']=True;f.attrs['last_committed_index']=index;f.flush()
    write_json(folder/'checkpoint.json',{'schema_version':1,'last_committed_index':index,'last_converged_time_s':float(time),
                                      'file':path.name,'status':'converged_physical_step','automatic_restart':False})


class JournalSeries(Sequence):
    """Read one committed frame at a time; memory is independent of frame count."""
    def __init__(self,folder,indices,field=None):
        self.path=Path(folder)/'physical-steps.h5';self.indices=indices;self.field=field

    def __len__(self):return len(self.indices)

    def __getitem__(self,index):
        if isinstance(index,slice):return JournalSeries(self.path.parent,self.indices[index],self.field)
        physical=self.indices[index]
        with h5py.File(self.path) as f:
            g=f[str(physical)]
            if not bool(g.attrs.get('committed',False)):raise ValueError('请求的物理步尚未提交')
            if self.field:return g[self.field][:]
            state=g['structure'];result=json.loads(state['metadata_json'][()].decode('utf-8'))
            for name,value in state.items():
                if name=='metadata_json':continue
                # Legacy recovery/export consumers test these collections for
                # truthiness; retain their original list contract.
                result[name]=value[:].tolist() if name in ('contact_stresses','contact_displacements') else value[:]
            return result


class DerivedSeries(Sequence):
    """Fields derived from one saved temperature frame, persisted incrementally."""
    def __init__(self,path,field):self.path=Path(path);self.field=field

    def __len__(self):
        with h5py.File(self.path) as f:return len(f[self.field])

    def __getitem__(self,index):
        with h5py.File(self.path) as f:return f[self.field][index]


def derive_thermal_fields(folder,system,temperatures):
    path=Path(folder)/'thermal-derived.h5'
    with h5py.File(path,'w') as f:
        for i,T in enumerate(temperatures):
            gradient,flux=system.gradients(T)
            for name,value in (('gradient',gradient),('flux',flux)):
                if i==0:f.create_dataset(name,shape=(len(temperatures),*value.shape),dtype=value.dtype,compression='gzip',chunks=(1,*value.shape))
                f[name][i]=value
            f.flush()
    return DerivedSeries(path,'gradient'),DerivedSeries(path,'flux')


def committed_steps(folder):
    path=Path(folder)/'physical-steps.h5'
    if not path.is_file():return []
    with h5py.File(path) as f:
        last=int(f.attrs.get('last_committed_index',-1))
        return [{'index':int(k),'time_s':float(g.attrs['time_s'])} for k,g in sorted(f.items(),key=lambda p:int(p[0]))
                if int(k)<=last and bool(g.attrs.get('committed',False))]
