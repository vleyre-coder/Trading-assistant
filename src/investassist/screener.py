"""Orchestration d'une analyse d'univers.

Deroulement en deux passes, necessaire pour les criteres relatifs :
  1. chargement des fondamentaux de tous les titres (appels reseau, en
     parallele limite) ;
  2. calcul des medianes sectorielles, puis notation de chaque titre.

Un titre en echec n'interrompt jamais l'execution : il est signale comme
non classable avec la raison, et le classement se fait sur les autres.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

import pandas as pd

from . import criteria as crit
from . import scoring
from .config import ScoringConfig, Settings, load_universes
from .fundamentals import FundamentalsService
from .models import Fundamentals, StockScore
from .storage import Database

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ScreeningResult:
    scores: list[StockScore]
    ranked: list[StockScore]
    excluded: list[StockScore]
    failures: dict[str, str] = field(default_factory=dict)
    # Derniere periode de reference publiee, par ticker : sert a detecter une
    # nouvelle publication de resultats sans appel reseau supplementaire.
    last_earnings: dict[str, str] = field(default_factory=dict)
    # Medianes de P/E par secteur calculees sur cet univers : reutilisables
    # ailleurs dans l'interface pour que le critere relatif au secteur ait un
    # sens (comparer un titre a lui-meme donnerait toujours un ratio de 1).
    sector_medians: dict[str, float] = field(default_factory=dict)
    run_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    universes: list[str] = field(default_factory=list)

    @property
    def ranks(self) -> dict[str, int]:
        return {s.ticker: i + 1 for i, s in enumerate(self.ranked)}

    def dataframe(self) -> pd.DataFrame:
        return scoring.to_dataframe(self.ranked)


def tickers_for(universes: Sequence[str], catalogue: dict | None = None) -> list[str]:
    """Liste des tickers des univers demandes, dedoublonnee.

    Les valeurs non textuelles sont ecartees avec un message explicite : en
    YAML, un ticker non quote parmi ON, OFF, YES, NO, Y, N, TRUE ou FALSE est
    lu comme un booleen. Le ticker ON (ON Semiconductor) du Nasdaq-100 devient
    ainsi True, disparait de l'analyse et fait echouer les affichages en aval.
    """
    if catalogue is None:
        catalogue = load_universes().get("universes") or {}
    tickers: list[str] = []
    for name in universes:
        block = catalogue.get(name)
        if not block:
            log.warning("Univers inconnu dans config/universes.yaml : %s", name)
            continue
        for valeur in block.get("tickers") or []:
            if isinstance(valeur, str) and valeur.strip():
                tickers.append(valeur.strip())
            else:
                log.error(
                    "Ticker invalide dans l'univers « %s » : %r. Entourez chaque "
                    "ticker de guillemets dans config/universes.yaml (ON, NO, Y… "
                    "sont interpretes comme des booleens par YAML).",
                    name,
                    valeur,
                )
    # Dedoublonnage en preservant l'ordre.
    return list(dict.fromkeys(tickers))


class Screener:
    def __init__(
        self,
        settings: Settings,
        scoring_config: ScoringConfig,
        *,
        service: FundamentalsService | None = None,
        database: Database | None = None,
    ) -> None:
        self.settings = settings
        self.cfg = scoring_config
        self.service = service or FundamentalsService(settings)
        self.db = database

    # --------------------------------------------------------------- passe 1
    def _load_one(
        self, ticker: str, *, use_cache: bool
    ) -> tuple[str, Fundamentals | None, pd.DataFrame | None, str]:
        try:
            fund = self.service.load(
                ticker, target_years=self.cfg.target_years, use_cache=use_cache
            )
            prices = self.service.price_history(ticker, use_cache=use_cache)
            return ticker, fund, prices, ""
        except Exception as exc:  # noqa: BLE001
            log.warning("Chargement de %s impossible : %s: %s", ticker, type(exc).__name__, exc)
            return ticker, None, None, f"{type(exc).__name__}: {exc}"

    # ----------------------------------------------------------------- public
    def run(
        self,
        universes: Sequence[str],
        *,
        tickers: Sequence[str] | None = None,
        use_cache: bool = True,
        persist: bool = True,
        progress: ProgressCallback | None = None,
    ) -> ScreeningResult:
        symbols = list(tickers) if tickers else tickers_for(universes)
        started = datetime.now()
        run_id = None
        if persist and self.db is not None:
            run_id = self.db.start_run(list(universes) or ["personnalise"])

        funds: dict[str, Fundamentals] = {}
        prices: dict[str, pd.DataFrame | None] = {}
        failures: dict[str, str] = {}
        raw_values: dict[str, dict[str, crit.Result]] = {}

        total = len(symbols)
        done = 0
        workers = max(1, min(self.settings.yahoo_max_workers, total or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._load_one, t, use_cache=use_cache): t for t in symbols
            }
            for future in as_completed(futures):
                ticker, fund, price_frame, error = future.result()
                done += 1
                if progress:
                    progress(done, total, ticker)
                if fund is None:
                    failures[ticker] = error
                    continue
                if fund.fetch_failed:
                    # Distinction importante pour l'utilisateur : ce titre n'est
                    # pas « mauvais », il n'a pas pu etre lu. Une relance suffit
                    # generalement.
                    failures[ticker] = (
                        "aucune donnee recuperee (source momentanement "
                        "indisponible ou ticker inconnu) — relancer l'analyse"
                    )
                    continue
                funds[ticker] = fund
                prices[ticker] = price_frame
                # Le calcul des criteres propres au titre peut se faire des
                # maintenant ; seuls les criteres relatifs attendent la passe 2.
                raw_values[ticker] = crit.compute_all(fund, price_frame)

        # --------------------------------------------------------- passe 2
        medians = scoring.sector_pe_medians(list(funds.values()))
        scores: list[StockScore] = []
        for ticker, fund in funds.items():
            score = scoring.score_stock(
                fund,
                self.cfg,
                prices=prices.get(ticker),
                sector_medians=medians,
                raw_values=raw_values.get(ticker),
            )
            scores.append(score)

        ranked = scoring.rank(scores)
        excluded = scoring.excluded(scores)
        last_earnings = {
            ticker.upper(): str(fund.snapshot.last_earnings_date)
            for ticker, fund in funds.items()
            if fund.snapshot.last_earnings_date
        }

        result = ScreeningResult(
            scores=scores,
            ranked=ranked,
            excluded=excluded,
            failures=failures,
            last_earnings=last_earnings,
            sector_medians=medians,
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(),
            universes=list(universes),
        )

        if persist and self.db is not None and run_id is not None:
            self.db.save_scores(run_id, scores, result.ranks)
            self.db.finish_run(
                run_id,
                n_analyzed=len(scores),
                n_ranked=len(ranked),
                notes=(
                    f"{len(excluded)} titre(s) exclu(s) pour donnees incompletes ; "
                    f"{len(failures)} echec(s) de recuperation"
                ),
                sector_medians=medians,
            )
        return result
