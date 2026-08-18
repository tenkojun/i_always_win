# -*- coding: utf-8 -*-
import traceback
from engine.data.analyst import get_analyst_targets
for tk in ("AAPL", "MSFT", "005930.KS"):
    try:
        d = get_analyst_targets(tk)
        print(f"{tk:12s} ->", {k: d.get(k) for k in
              ("target_mean", "target_high", "target_low",
               "current_price", "upside_pct", "n_analysts", "error")})
    except Exception as e:
        print(f"{tk:12s} 예외 {type(e).__name__}: {e}")
        traceback.print_exc()
