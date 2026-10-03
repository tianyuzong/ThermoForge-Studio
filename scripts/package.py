"""Backward-compatible source distribution entry point using the safe allowlist."""
import json
from package_plugin import ROOT, package

if __name__ == '__main__':
    print(json.dumps(package(ROOT, ROOT / 'dist-plugin/Strength-Studio-Windows-0.5.0.zip'), ensure_ascii=False, indent=2))
