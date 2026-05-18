# Stock Portfolio Service

To refresh the bundled broker name map, run `python scripts/refresh_name_to_symbol.py` from this service root. The script regenerates `app/data/name_to_symbol.json` by enumerating `twstock.codes` (TWSE listed + TPEx OTC + ETFs + ETNs + active warrants), so the only prerequisite is an up-to-date `twstock` dependency.

Post-import networth recalculation now derives the weekdays where the portfolio actually had exposure and limits both historical price fetches and snapshot replay to those active dates. The optimization is always applied by the post-import chain, so a fully closed historical position no longer causes the service to walk every later weekday in the requested range. In recalc status, `dates_inactive` counts weekdays skipped because no symbol was held; it is intentionally separate from `dates_skipped`, which still means a fetched date where both markets were empty (for example, a holiday).
