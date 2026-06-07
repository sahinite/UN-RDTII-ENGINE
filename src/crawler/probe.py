"""
Auto-probe portal discovery. [Z1-2]

Sends each indicator's keywords to every portal in the economy config,
ranks portals by hit count, and skips zero-result portals — replacing
manual portal-to-pillar tagging.

TODO: def probe_portals(economy_cfg, keyword_sets) -> list[RankedPortal]
"""
