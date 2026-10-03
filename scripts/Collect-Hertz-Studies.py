"""Compare queued line-contact studies with the independent Hertz solution."""
import json,math
import h5py
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for size in (25,16):
    path=root/'validation'/f'hertz-line-{size}-study.json'
    if not path.exists():continue
    record=json.loads(path.read_text(encoding='utf-8'))
    if record['status']!='completed':continue
    folder=root/'data'/'studies'/record['id'];summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    with h5py.File(folder/'fields.h5') as f:
        native=f['native_contact/0'];total=json.loads(native.attrs['contact_totals_json'])['0'];peak=float(native['contact_stresses'][:,2].max())
    force=abs(total['normal_force_N'])
    length=.0002;radius=.002;reduced_E=2e9/(2*(1-.3**2))
    halfwidth=math.sqrt(4*force/length*radius/(math.pi*reduced_E));expected_peak=2*force/(math.pi*halfwidth*length)
    actual_width=total['area_m2']/length/2
    result={'source_study':record['id'],'normal_force_N':force,'expected_half_width_m':halfwidth,'actual_half_width_m':actual_width,'expected_peak_Pa':expected_peak,'actual_peak_Pa':peak,'pressure_error':abs(peak/expected_peak-1),'width_error':abs(actual_width/halfwidth-1),'balance':summary['mechanical_balance'],'mesh':summary['mesh']}
    result['passed']=result['pressure_error']<=.05 and result['width_error']<=.05 and result['balance']['passed']
    target=root/'validation'/f'hertz-line-{size}-K1e+15';target.mkdir(exist_ok=True)
    (target/'benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (target/'case.json').write_text(json.dumps(record['case'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(size,{k:result[k] for k in ('pressure_error','width_error','passed')})
