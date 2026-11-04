"""One portable quota scope for the five launchers and registered history."""
from pathlib import Path
import json,os
ROOT=Path(__file__).parent
def shared_roots(current):
    policy=ROOT/'log_policy.json';known=ROOT/'known_log_roots.json';paths=[Path(current)]
    if policy.exists():paths.extend(Path(p).expanduser() for p in json.loads(policy.read_text(encoding='utf-8')).get('additional_roots',[]))
    if known.exists():paths.extend(Path(p) for p in json.loads(known.read_text(encoding='utf-8')))
    if os.name=='nt':paths.extend(Path(letter+':/ForestD1_Logs') for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ' if Path(letter+':/ForestD1_Logs').is_dir())
    unique={str(p.resolve()).casefold():p.resolve() for p in paths}
    # Every later connection remembers previous selected roots; selecting an
    # empty directory cannot erase history from the shared quota calculation.
    temporary=known.with_suffix('.tmp');temporary.write_text(json.dumps([str(p) for p in unique.values()],ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(known)
    return list(unique.values())
