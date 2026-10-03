"""Build an allowlisted plugin distribution, never copy a running workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', '.gitattributes', '.mcp.json', 'README.md', 'README.en.md', 'THIRD_PARTY.md',
              'local-settings.example.json', 'requirements.txt', 'requirements-windows-py314.lock',
              'requirements-gpu.lock', 'requirements-plugin.txt', 'requirements-plugin.lock',
              'Install.ps1', 'Install-GPU.ps1', 'Install-CodexPlugin.ps1', 'Start.ps1', 'Start.bat',
              'Stop.ps1', 'Stop.bat', 'Diagnose-CPU.ps1', 'Diagnose-CPU.bat', 'Diagnose-GPU.bat'}
DIRECTORIES = {'.codex-plugin', 'skills', 'scripts', 'strength', 'tests', 'docs', 'examples'}
EXCLUDED_PARTS = {'__pycache__', 'node_modules', '.runtime', '.venv', '.git', '.visual-backups',
                  '.pytest_cache', 'test-results', 'playwright-report'}
EXCLUDED_FILES = {'GEAR_CONTACT_GEOMETRY.json', 'GEAR_STUDY_TASK.json', 'local-settings.json'}
FORBIDDEN_SUFFIXES = {'.exe', '.dll', '.pyc', '.tsbuildinfo', '.pem', '.key', '.p12', '.pfx'}


def release_files(root: Path):
    roots = [root / name for name in sorted(ROOT_FILES | DIRECTORIES)]
    roots += [root / 'frontend' / name for name in ('src', 'dist', 'tests', 'public')]
    roots += [root / 'vendor' / name for name in ('COPYING-CalculiX.txt', 'README_Install.txt')]
    roots += [p for p in (root / 'frontend').glob('*') if p.is_file() and
              p.name in {'package.json', 'package-lock.json', 'index.html', 'tsconfig.json',
                         'vite.config.ts', 'THIRD_PARTY_LICENSES.txt'}]
    for base in roots:
        candidates = [base] if base.is_file() else base.rglob('*') if base.is_dir() else []
        for path in candidates:
            rel = path.relative_to(root)
            if (not path.is_file() or path.is_symlink() or set(rel.parts) & EXCLUDED_PARTS
                    or path.name in EXCLUDED_FILES or path.name.startswith('.env')
                    or path.suffix.lower() in FORBIDDEN_SUFFIXES):
                continue
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('A release file resolves outside the source tree')
            yield path


def package(root: Path, destination: Path):
    files = sorted(set(release_files(root)))
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    version = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            archive.write(p, 'strength-studio/' + p.relative_to(root).as_posix())
        archive.writestr('strength-studio/MANIFEST.json', json.dumps({'version': version, 'sha256': hashes}, indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
    return {'path': str(destination), 'files': len(files), 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist-plugin' / 'Strength-Studio-Codex-0.5.0.zip')
    args = parser.parse_args()
    print(json.dumps(package(ROOT, args.output.resolve()), ensure_ascii=False, indent=2))
