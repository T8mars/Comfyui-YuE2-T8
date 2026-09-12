"""Run the staged model installer without PowerShell inline-code quoting."""
import argparse
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--root', required=True, type=Path)
parser.add_argument('--source', required=True, type=Path)
args = parser.parse_args()
source = args.source.resolve()
root = args.root.resolve()
if (root / 'cache/updates').resolve() not in source.parents:
    raise ValueError('Staged models must come from this installation update directory')
script = source / 'scripts/download_rvc_models.py'
sys.path.insert(0, str(source))
sys.argv = [str(script), '--root', str(root), '--source', str(source)]
runpy.run_path(str(script), run_name='__main__')
