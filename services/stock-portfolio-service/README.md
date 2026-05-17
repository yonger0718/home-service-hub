# Stock Portfolio Service

To refresh the bundled broker name map, run `python scripts/refresh_name_to_symbol.py` from this service root. The script regenerates `app/data/name_to_symbol.json` by enumerating `twstock.codes` (TWSE listed + TPEx OTC + ETFs + ETNs + active warrants), so the only prerequisite is an up-to-date `twstock` dependency.
