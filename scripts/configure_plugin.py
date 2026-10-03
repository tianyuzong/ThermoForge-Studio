"""Install a clean plugin source snapshot, preserving other marketplace entries."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import uuid

from package_plugin import release_files
from strength_client import config_path, local_url


def configure(workspace: Path, url: str, plugin_python: Path, user_home: Path | None = None):
    workspace = workspace.resolve()
    local_url(url)
    user_home = (user_home or Path.home()).resolve()
    marketplace = user_home / '.agents/plugins/marketplace.json'
    payload = json.loads(marketplace.read_text(encoding='utf-8-sig')) if marketplace.exists() else {
        'name': 'personal', 'interface': {'displayName': 'Personal'}, 'plugins': []}
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', payload.get('name', '')) or not isinstance(payload.get('plugins'), list):
        raise ValueError('Invalid existing marketplace; no files changed.')
    plugin_parent = user_home / 'plugins'
    destination = plugin_parent / 'strength-studio'
    if workspace == destination.resolve() or workspace.is_relative_to(destination.resolve()):
        raise ValueError('Deploy the engine outside the plugin snapshot directory.')
    plugin_parent.mkdir(parents=True, exist_ok=True)
    temporary = plugin_parent / ('strength-studio-install-' + uuid.uuid4().hex)
    temporary.mkdir()
    files = list(release_files(workspace))
    for source in files:
        target = temporary / source.relative_to(workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest_file = temporary / '.codex-plugin/plugin.json'
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest['name'] != 'strength-studio':
        raise ValueError('Unexpected plugin identifier')
    if destination.exists():
        if destination.is_symlink() or not destination.resolve().is_relative_to(plugin_parent.resolve()):
            raise ValueError('Refusing to replace a plugin path outside the personal plugin directory')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + uuid.uuid4().hex[:6]
        manifest['version'] = manifest['version'].split('+')[0] + '+codex.' + stamp
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        destination.rename(plugin_parent / ('strength-studio.previous-' + stamp))
    temporary.rename(destination)
    entry = {'name': 'strength-studio', 'source': {'source': 'local', 'path': './plugins/strength-studio'},
             'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Productivity'}
    previous = next((i for i, value in enumerate(payload['plugins']) if value.get('name') == 'strength-studio'), None)
    if previous is None:
        payload['plugins'].append(entry)
    else:
        payload['plugins'][previous] = entry
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    marketplace.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    settings = config_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({'workspace': str(workspace), 'url': url,
                                   'plugin_python': str(plugin_python.resolve())}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'plugin': str(destination), 'marketplace': str(marketplace),
            'selector': 'strength-studio@' + payload['name'], 'configuration': str(settings), 'files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8876')
    parser.add_argument('--plugin-python', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(configure(args.workspace, args.url, args.plugin_python), ensure_ascii=False))
