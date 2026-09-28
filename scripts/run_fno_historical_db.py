"""Run only the approved FnO historical database commands."""
from pathlib import Path
import runpy
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'systems'/'fno_momentum'/'src'))
# pythonw avoids inheriting a short-lived console window from the scheduler.
# Audit events are authoritative; these bounded logs retain startup diagnostics.
if sys.stdout is None or sys.stderr is None:
    import json
    from fno_momentum.historical_db import HistoricalCollector, used_bytes
    c=HistoricalCollector()
    class BoundedLog:
        def __init__(self,path): self.path=path
        def write(self,text):
            data=text.encode('utf-8',errors='replace')
            with c.write_lock():
                if (self.path.stat().st_size if self.path.exists() else 0)+len(data)>1024*1024: return len(text)
                if used_bytes(c.root)+len(data)+c.reserve>c.cap: return len(text)
                with self.path.open('ab') as out: out.write(data)
            return len(text)
        def flush(self): pass
    logs=c.root/'historical_db'; logs.mkdir(exist_ok=True)
    sys.stdout=BoundedLog(logs/'runner.stdout.log')
    sys.stderr=BoundedLog(logs/'runner.stderr.log')
from fno_momentum.historical_db import main
main()
