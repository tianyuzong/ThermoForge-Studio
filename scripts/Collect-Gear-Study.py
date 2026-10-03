"""Check actual free-follower motion over the saved single-tooth engagement."""
import argparse,json,math
from pathlib import Path
import h5py,numpy as np
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--record',default='gear-tooth-engagement-final');args=parser.parse_args()
record=json.loads((root/'validation'/(args.record+'.json')).read_text(encoding='utf-8'))
if record['status']!='completed':raise SystemExit('Gear study is not completed; no pass record generated.')
folder=root/'data'/'studies'/record['id'];summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
driver=[];follower=[];areas=[];pressures=[]
with h5py.File(folder/'fields.h5') as f:
    times=f['times_s'][:].tolist()
    for i in range(len(times)):
        native=f['native_contact'][str(i)];axles=json.loads(native.attrs['axles_json'])
        driven=next(v for v in axles.values() if v['driven']);free=next(v for v in axles.values() if not v['driven'])
        driver.append(driven['rotation_vector_rad'][driven['axis']]);follower.append(free['rotation_vector_rad'][free['axis']])
        totals=json.loads(native.attrs['contact_totals_json']);areas.append(sum(v['area_m2'] for v in totals.values()))
        values=native['contact_stresses'][:];pressures.append(float(values[:,2].max(initial=0)))
driver=np.unwrap(driver);follower=np.unwrap(follower);change=driver-driver[0];response=follower-follower[0]
error=float(np.max(abs(change+response))/max(abs(change[-1]),1e-12))
result={'source_study':record['id'],'scope':'20:20 gear, one 18-degree tooth-pitch engagement; not a full material-point revolution',
        'limitations':'Checks transmission, contact continuity and balance only. Local edge pressure peaks are not mesh-converged and must not be used for validated fatigue life.',
        'times_s':times,'driver_angle_rad':driver.tolist(),'follower_angle_rad':follower.tolist(),'contact_area_m2':areas,'native_peak_pressure_Pa':pressures,
        'driver_sweep_deg':float(math.degrees(change[-1])),'follower_sweep_deg':float(math.degrees(response[-1])),
        'maximum_transmission_deviation_relative_to_sweep':error,'transmission_reference_tolerance':.01,
        'mechanical_balance_passed':summary['mechanical_balance']['passed'],'all_frames_carry_contact':min(areas)>0}
result['passed']=abs(result['driver_sweep_deg']-18)<1e-3 and error<=.01 and result['mechanical_balance_passed'] and result['all_frames_carry_contact']
(root/'validation'/'gear-engagement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if not isinstance(v,list)},ensure_ascii=False))
