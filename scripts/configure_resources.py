"""Upgrade only resource settings, preserving existing jobs, identity, and evidence."""
import argparse,json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from lvm.manager import Manager
from lvm.resources import configure
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dry-run',action='store_true');args=p.parse_args()
print(json.dumps(configure(Manager().init(),dry_run=args.dry_run),indent=2))
