"""Check exact roots, hashes and transitive closure for both supported CPython 3.13 targets.

Run in the selected clean installed profile. Installation itself is enforced by
pip --require-hashes/--only-binary in the requirements files, not this checker.
"""
from importlib import metadata
from pathlib import Path
from collections import deque
import argparse
import re
import sys
from packaging.requirements import Requirement
from packaging.markers import default_environment
from packaging.utils import canonicalize_name

ROOT=Path(__file__).resolve().parents[2]
PROFILES={'runtime':('backend/requirements.in','backend/requirements.txt'),
          'dev':('backend/requirements-dev.in','backend/requirements-dev.txt'),
          'security':('.github/requirements-security.in','.github/requirements-security.txt')}


def declarations(path,locked=False,seen=None):
    seen=set() if seen is None else seen
    if path in seen:raise ValueError('recursive requirements include')
    seen.add(path)
    result={}
    text=path.read_text().replace('\\\n',' ')
    if locked:
        assert '--require-hashes' in text and '--only-binary=:all:' in text
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#') or line in {'--require-hashes','--only-binary=:all:'}:continue
        if line.startswith('-r '):
            child=(path.parent/line[3:].strip()).resolve()
            if not child.is_relative_to(ROOT):raise ValueError('external requirements include')
            result.update(declarations(child,locked,set(seen)));continue
        parts=line.split();req=Requirement(parts[0]);name=canonicalize_name(req.name)
        assert req.url is None and req.marker is None and len(req.specifier)==1
        spec=next(iter(req.specifier));assert spec.operator=='==' and '*' not in spec.version
        if locked:
            assert len(parts)>1 and all(re.fullmatch(r'--hash=sha256:[a-f0-9]{64}',h) for h in parts[1:])
        else:assert len(parts)==1
        assert name not in result,'duplicate pinned package'
        result[name]=(spec.version,req)
    return result


def check(profile):
    source,lock=PROFILES[profile];roots=declarations(ROOT/source);pins=declarations(ROOT/lock,True)
    for name,(version,_) in roots.items():assert name in pins and pins[name][0]==version,('root differs',name)
    for target,system,machine in [('linux','Linux','x86_64'),('darwin','Darwin','arm64')]:
        env=default_environment();env.update(sys_platform=target,platform_system=system,platform_machine=machine,
                                            python_version='3.13',python_full_version='3.13.4')
        queue=deque(str(req) for _,req in roots.values());selected=set();extras={};processed={}
        while queue:
            req=Requirement(queue.popleft());name=canonicalize_name(req.name)
            assert name in pins,('unlocked dependency',name)
            version=pins[name][0];assert req.specifier.contains(version,prereleases=True)
            dist=metadata.distribution(name);assert dist.version==version,('installed version differs',name)
            selected.add(name);extras.setdefault(name,set()).update(req.extras);active=frozenset(extras[name]|{''})
            if processed.get(name)==active:continue
            processed[name]=active
            for raw in dist.requires or []:
                child=Requirement(raw)
                if child.marker is None or any(child.marker.evaluate({**env,'extra':extra}) for extra in active):queue.append(str(child))
        assert selected==set(pins),('closure differs',target,sorted(set(pins)-selected))
    print(f'OK: {profile}: {len(pins)} fully pinned/hashed packages; Linux x86_64 and macOS arm64 closures match')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=PROFILES,default='dev');args=parser.parse_args()
    if sys.version_info[:2]!=(3,13):raise SystemExit('CPython 3.13 required')
    check(args.profile)
