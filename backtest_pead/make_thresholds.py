"""
Calcula los percentiles 20 y 80 de la sorpresa/precio de los 4 anios previos y los guarda en ../ls_thresholds.json
(los usa el modulo largo/corto en vivo). Refrescar una vez por trimestre (1 ene / 1 abr / 1 jul / 1 oct) con el panel
de eventos actualizado (fetch.py events + events.py). Fuera de ese calendario NO tocar: es parte de la regla congelada.

Uso:  python backtest_pead/make_thresholds.py [AAAA-MM-DD]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from universe import CACHE

asof = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp.today().normalize()
p = pd.read_pickle(CACHE / "events_panel.pkl")
w = p[(p.date >= asof - pd.Timedelta(days=4 * 365)) & (p.date < asof)]["surprise_px"].dropna()
out = {"as_of": asof.date().isoformat(), "window_years": 4, "n_events": int(len(w)),
       "p20": round(float(np.percentile(w, 20)), 4), "p80": round(float(np.percentile(w, 80)), 4),
       "panel_last_event": p.date.max().date().isoformat()}
dst = Path(__file__).resolve().parent.parent / "ls_thresholds.json"
dst.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(out)
