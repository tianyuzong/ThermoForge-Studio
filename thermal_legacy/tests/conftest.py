import os,uuid
from pathlib import Path
os.environ['THERMAL_DATA']=str(Path(__file__).resolve().parents[2]/'.runtime'/'legacy-tests'/uuid.uuid4().hex)
