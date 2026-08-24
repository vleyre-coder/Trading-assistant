#!/usr/bin/env python3
"""Validation d'UN SEUL titre, de bout en bout.

C'est l'outil de controle a utiliser avant tout screening large : il montre
chaque donnee brute recuperee, chaque critere calcule, son sous-score et le
detail du calcul. Toute anomalie de parsing se voit ici, sur 2 ou 3 appels
reseau, plutot que noyee dans une execution de 140 titres.

Exemples :
    python scripts/validate_ticker.py MSFT
    python scripts/validate_ticker.py AIR.PA --no-cache
    python scripts/validate_ticker.py MSFT --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investassist import scoring  # noqa: E402
from investassist.config import load_scoring, load_settings  # noqa: E402
from investassist.disclaimers import MAIN  # noqa: E402
from investassist.fundamentals import FundamentalsService  # noqa: E402
from investassist.providers.fmp import FmpClient  # noqa: E402
from investassist.storage import _score_payload  # noqa: E402


def money(value: float | None, currency: str | None) -> str:
    if value is None:
        return "n/d"
    for divisor, suffix in ((1e9, "Md"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= divisor:
            return f"{value / divisor:>10,.2f} {suffix} {currency or ''}".replace(",", " ")
    return f"{value:>10,.2f} {currency or ''}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", help="Ticker au format Yahoo Finance (ex. MSFT, AIR.PA)")
    parser.add_argument("--no-cache", action="store_true", help="Force la relecture des sources")
    parser.add_argument("--json", action="store_true", help="Sortie JSON exploitable")
    parser.add_argument("--verbose", action="store_true", help="Journal detaille")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    settings, config = load_settings(), load_scoring()
    service = FundamentalsService(settings)
    ticker = args.ticker.upper()
    use_cache = not args.no_cache

    fundamentals = service.load(ticker, target_years=config.target_years, use_cache=use_cache)
    prices = service.price_history(ticker, use_cache=use_cache)
    # Sans univers de comparaison, le critere relatif au secteur est
    # volontairement non calculable : c'est le comportement attendu.
    score = scoring.score_stock(fundamentals, config, prices=prices, sector_medians={})

    if args.json:
        print(json.dumps(
            {
                "ticker": ticker,
                "avertissement": MAIN,
                "nom": score.name,
                "region": score.region,
                "classable": score.ranked,
                "raison_exclusion": score.exclusion_reason,
                **_score_payload(score),
            },
            ensure_ascii=False, indent=2, default=str,
        ))
        return 0

    snapshot = fundamentals.snapshot
    currency = snapshot.currency
    line = "─" * 78
    print(line)
    print(f"  {ticker} — {snapshot.name or 'nom inconnu'}")
    print(f"  {snapshot.sector or 'secteur n/d'} | {snapshot.country or 'pays n/d'} | "
          f"zone retenue : {fundamentals.region} | devise : {currency or 'n/d'}")
    print(line)
    print(f"  Cours : {snapshot.price} | capitalisation : {money(snapshot.market_cap, currency)}")
    print(f"  P/E : {snapshot.trailing_pe} | P/B : {snapshot.price_to_book} | "
          f"rendement : {'n/d' if snapshot.dividend_yield is None else f'{snapshot.dividend_yield * 100:.2f} %'}")
    print(f"  Dernière période publiée : {snapshot.last_earnings_date} | "
          f"prochaine publication : {snapshot.next_earnings_date}")

    print(f"\n  DONNÉES ANNUELLES RETENUES ({len(fundamentals.annual)} exercices)")
    print(f"  {'Exercice':>9} {'Clôture':>11} {'CA':>17} {'Rés. net':>17} {'EBITDA':>17}")
    for record in fundamentals.sorted_annual():
        print(f"  {record.fiscal_year:>9} {str(record.period_end):>11} "
              f"{money(record.get('revenue'), None):>17} "
              f"{money(record.get('net_income'), None):>17} "
              f"{money(record.get('ebitda'), None):>17}")
    print(f"\n  Sources par champ : " + ", ".join(f"{k}→{v}" for k, v in sorted(fundamentals.sources.items())))

    print(f"\n  CRITÈRES ET SOUS-SCORES")
    for pillar_key, pillar in score.pillars.items():
        head = f"n/a (couverture {pillar.coverage:.0%})" if pillar.score is None else f"{pillar.score:5.1f}/100"
        print(f"\n  ▸ {pillar_key.upper():<15} {head}  poids {pillar.weight:.0%}"
              + ("  [neutralisé]" if pillar.neutralized else ""))
        for criterion in pillar.criteria:
            value = "n/d" if criterion.value is None else f"{criterion.value:.4f}"
            sub = "  n/a" if criterion.score is None else f"{criterion.score:5.1f}"
            print(f"      {criterion.label:<44} {value:>10}  → {sub}")
            print(f"        {criterion.detail or criterion.reason_missing}")

    print(f"\n{line}")
    composite = "non calculable" if score.composite is None else f"{score.composite:.1f}/100"
    print(f"  SCORE COMPOSITE : {composite}   "
          f"fenêtre {score.window_years} ans   couverture {score.coverage:.0%}")
    if not score.ranked:
        print(f"  ⚠️  NON CLASSABLE — {score.exclusion_reason}")
    for warning in score.warnings:
        print(f"  ℹ️  {warning}")

    fmp = FmpClient(settings)
    if fmp.enabled:
        computed = {c.key: c.value for c in score.criteria_flat()}
        messages = fmp.cross_check(ticker, computed)
        print(f"\n  CONTRÔLE CROISÉ FMP ({fmp.budget_left()} requêtes restantes aujourd'hui)")
        for message in messages or ["aucun écart significatif détecté"]:
            print(f"    • {message}")

    print(f"\n  ⚠️  {MAIN}")
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
