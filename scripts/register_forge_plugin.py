"""Optional explicit plugin installation using Codex's official marketplace helpers."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
p=argparse.ArgumentParser();p.add_argument('--codex',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1]
helpers=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills/.system/plugin-creator/scripts'
required=['create_basic_plugin.py','read_marketplace_name.py','update_plugin_cachebuster.py']
if not all((helpers/name).is_file() for name in required):raise SystemExit('Codex plugin-creator helpers are unavailable. Use the prepared standalone .runtime/thermoforge-mcp.json connection.')
def run(name,*arguments):return subprocess.run([sys.executable,str(helpers/name),*map(str,arguments)],check=True)
if (Path.home()/'.agents/plugins/marketplace.json').exists():run('read_marketplace_name.py')
destination=Path.home()/'plugins/thermoforge-studio';existed=destination.exists()
if existed:
    manifest=json.loads((destination/'.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    if manifest.get('name')!='thermoforge-studio' or destination.is_symlink():raise SystemExit('Existing destination is not this plugin; preserving it.')
    destination.rename(destination.with_name('thermoforge-studio.previous-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')))
run('create_basic_plugin.py','thermoforge-studio','--with-marketplace','--force')
shutil.copytree(root/'integrations/thermoforge-studio',destination,dirs_exist_ok=True)
if existed:run('update_plugin_cachebuster.py',destination)
marketplace=json.loads((Path.home()/'.agents/plugins/marketplace.json').read_text(encoding='utf-8-sig'))
subprocess.run([args.codex,'plugin','add','thermoforge-studio@'+marketplace['name'],'--json'],check=True)
print('ThermoForge Studio installed. Open a new Codex task to load its tools.')
