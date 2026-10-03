"""Generate explicit step refinements of the supplied verification examples."""
import json,math
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
bearing=root/'examples'/'local-bearing'
case=read(bearing/'case.json')
case['name']='局部滚动体—滚道 · 129 帧步长验证'
times=[i*.1/128 for i in range(129)];angles=[.02*math.sin(2*math.pi*t/.1) for t in times]
case['history']['times_s']=times
case['motions'][0]['angles_rad']=angles
case['motions'][0]['translations_m']=[[.002*a,0,-.000005] for a in angles]
save(bearing/'step-129.json',case)
gap=root/'examples'/'thermal-gap';base=read(gap/'case.json')
# Fixed steps keep regeneration reproducible after the validated default changes.
for label,step in [('coarse',5),('half',2.5),('quarter',1.25),('eighth',.625),('sixteenth',.3125),('thirtysecond',.15625),('sixtyfourth',.078125)]:
    case=json.loads(json.dumps(base))
    case['name']=f'热致间隙闭合 · {step:g} 秒步长验证'
    case['thermal']['dt_s']=step
    save(gap/f'step-{label}.json',case)
print('Created rolling and transient coupling step refinements')
