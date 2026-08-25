"""Yahoo Finance via yfinance.

AVERTISSEMENT : yfinance est une bibliotheque NON OFFICIELLE qui interroge
les points d'entree internes de Yahoo Finance. Elle peut cesser de
fonctionner sans preavis si Yahoo modifie son service, et son usage doit
rester strictement personnel. Aucun engagement de disponibilite ni
d'exactitude. Voir README, section "Limites connues".

C'est neanmoins la seule source gratuite couvrant a la fois les prix et un
minimum de fondamentaux pour les titres europeens.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..config import Settings
from ..models import AnnualRecord, Snapshot
from .base import BROWSER_UA, DiskCache, RateLimiter, safe_call

log = logging.getLogger(__name__)

# Correspondance libelles yfinance -> vocabulaire interne. Ordre = priorite.
INCOME_MAP: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "net_income": ("Net Income", "Net Income Common Stockholders",
                   "Net Income From Continuing Operation Net Minority Interest"),
    "operating_income": ("Operating Income", "Total Operating Income As Reported", "EBIT"),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "eps_diluted": ("Diluted EPS", "Basic EPS"),
}

BALANCE_MAP: dict[str, tuple[str, ...]] = {
    "equity": ("Stockholders Equity", "Common Stock Equity",
               "Total Equity Gross Minority Interest"),
    "total_debt": ("Total Debt", "Long Term Debt And Capital Lease Obligation"),
    "cash": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
}


class YahooClient:
    """Enveloppe autour de yfinance, avec cache et limitation de debit."""

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.limiter = RateLimiter(settings.yahoo_rate_limit)
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self._session = None
        self._use_plain_session = settings.yahoo_force_requests_session

    # ------------------------------------------------------------- session
    def _plain_session(self):
        if self._session is None:
            import requests

            s = requests.Session()
            s.headers.update({"User-Agent": BROWSER_UA})
            self._session = s
        return self._session

    def _ticker(self, ticker: str):
        import yfinance as yf

        if self._use_plain_session:
            return yf.Ticker(ticker, session=self._plain_session())
        return yf.Ticker(ticker)

    @staticmethod
    def _is_empty(value: Any) -> bool:
        """Resultat vide, quelle que soit la forme renvoyee par yfinance."""
        if value is None:
            return True
        if hasattr(value, "empty"):
            return bool(value.empty)
        if isinstance(value, (dict, list, tuple, set)):
            return len(value) == 0
        return False

    def _fetch(self, ticker: str, fn_name: str, *args, attempts: int = 3, **kwargs):
        """Recupere une donnee en reessayant si la reponse revient vide.

        Sous charge, Yahoo repond parfois par un contenu vide avec un code
        HTTP 200. Sans reessai, le titre concerne perd tout son historique et
        sort du classement pour une raison purement transitoire — un defaut
        d'autant plus trompeur qu'il ressemble a une absence de donnees.
        """
        for tentative in range(attempts):
            resultat = self._with_fallback(ticker, fn_name, *args, **kwargs)
            if not self._is_empty(resultat):
                return resultat
            if tentative < attempts - 1:
                time.sleep(1.5 * (tentative + 1))
        log.warning(
            "Yahoo : %s indisponible pour %s après %s tentatives (bridage probable).",
            fn_name,
            ticker,
            attempts,
        )
        return None

    def _with_fallback(self, ticker: str, fn_name: str, *args, _retried: bool = False, **kwargs):
        """Appelle une methode yfinance, avec repli sur une session requests.

        Deux modes d'echec distincts doivent etre couverts :
          - une exception TLS, lorsque la couche curl_cffi de yfinance est
            bloquee (proxy d'entreprise qui re-termine le TLS) ;
          - un resultat VIDE sans exception : yfinance absorbe l'erreur reseau
            en interne et renvoie un DataFrame vide en journalisant
            « possibly delisted ». Sans ce second test, le repli ne se
            declenche jamais et tous les cours paraissent indisponibles.
        """
        self.limiter.wait()
        try:
            obj = self._ticker(ticker)
            attr = getattr(obj, fn_name)
            result = attr(*args, **kwargs) if callable(attr) else attr
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if not self._use_plain_session and not _retried:
                log.info(
                    "yfinance : couche TLS par défaut indisponible (%s), bascule sur "
                    "une session requests classique.",
                    name,
                )
                self._use_plain_session = True
                return self._with_fallback(ticker, fn_name, *args, _retried=True, **kwargs)
            log.debug("yfinance %s(%s) a echoue : %s: %s", fn_name, ticker, name, exc)
            return None

        if self._is_empty(result) and not self._use_plain_session and not _retried:
            log.info(
                "yfinance : reponse vide pour %s(%s) — nouvelle tentative avec une "
                "session requests classique.",
                fn_name,
                ticker,
            )
            self._use_plain_session = True
            return self._with_fallback(ticker, fn_name, *args, _retried=True, **kwargs)
        return result

    # ------------------------------------------------------------ snapshot
    @staticmethod
    def _info_is_complete(info: dict[str, Any] | None) -> bool:
        """Un payload tronque est le symptome d'un bridage de Yahoo.

        Sous forte parallelisation, Yahoo renvoie parfois un dictionnaire
        partiel (sans nom ni ratios) avec un code HTTP 200. Le mettre en cache
        figerait des donnees fausses pour toute la duree de vie du cache, et
        des piliers entiers seraient neutralises a tort.
        """
        if not info:
            return False
        has_identity = bool(info.get("longName") or info.get("shortName"))
        has_price = bool(
            info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        )
        return has_identity and has_price

    def snapshot(self, ticker: str, *, use_cache: bool = True, attempts: int = 3) -> Snapshot | None:
        cached = self.cache.get("yahoo_info", ticker) if use_cache else None
        info = cached
        if info is None:
            for attempt in range(attempts):
                info = self._with_fallback(ticker, "info")
                if self._info_is_complete(info):
                    break
                if attempt < attempts - 1:
                    # Pause croissante : le bridage de Yahoo est transitoire.
                    time.sleep(1.5 * (attempt + 1))
            # Un payload incomplet reste exploitable pour le peu qu'il contient,
            # mais il ne sera pas mis en cache (voir plus bas).
            if not info:
                return None
            if self._info_is_complete(info):
                self.cache.set("yahoo_info", ticker, info)
            else:
                log.warning(
                    "Yahoo : données de marché incomplètes pour %s (bridage probable) — "
                    "non mises en cache, certains critères seront marques N/A.",
                    ticker,
                )

        price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )
        # Rendement du dividende : on privilegie dividende annuel / prix, car
        # le champ dividendYield de yfinance est exprime tantot en pourcentage
        # tantot en fraction selon les versions.
        dy = None
        rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
        if rate and price:
            dy = float(rate) / float(price)
        elif info.get("trailingAnnualDividendYield"):
            dy = float(info["trailingAnnualDividendYield"])
        elif info.get("dividendYield"):
            raw = float(info["dividendYield"])
            dy = raw / 100.0 if raw > 1 else raw

        def _ts_to_date(value: Any) -> date | None:
            if not value:
                return None
            try:
                return datetime.utcfromtimestamp(int(value)).date()
            except (ValueError, OSError, TypeError):
                return None

        return Snapshot(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            country=info.get("country"),
            currency=info.get("currency"),
            exchange=info.get("exchange"),
            price=float(price) if price else None,
            market_cap=info.get("marketCap"),
            shares_outstanding=info.get("sharesOutstanding"),
            trailing_pe=info.get("trailingPE"),
            price_to_book=info.get("priceToBook"),
            dividend_yield=dy,
            trailing_eps=info.get("trailingEps"),
            next_earnings_date=_ts_to_date(info.get("earningsTimestamp")),
            last_earnings_date=_ts_to_date(info.get("mostRecentQuarter")),
            as_of=datetime.now(),
        )

    # ------------------------------------------------- etats financiers
    @staticmethod
    def _pick(frame: pd.DataFrame, labels: tuple[str, ...], column: Any) -> float | None:
        if frame is None or not hasattr(frame, "index"):
            return None
        index = {str(i): i for i in frame.index}
        for label in labels:
            if label in index:
                try:
                    value = frame.loc[index[label], column]
                except (KeyError, IndexError):
                    continue
                if value is None or pd.isna(value):
                    continue
                return float(value)
        return None

    def annual_records(self, ticker: str, *, use_cache: bool = True) -> tuple[list[AnnualRecord], list[str]]:
        cache_key = f"{ticker}:annual"
        if use_cache:
            cached = self.cache.get("yahoo_annual", cache_key)
            if cached is not None:
                return (
                    [
                        AnnualRecord(
                            fiscal_year=r["fiscal_year"],
                            period_end=date.fromisoformat(r["period_end"])
                            if r.get("period_end")
                            else None,
                            values=r["values"],
                        )
                        for r in cached["records"]
                    ],
                    cached.get("warnings", []),
                )

        warnings: list[str] = []
        income = self._fetch(ticker, "income_stmt")
        balance = self._fetch(ticker, "balance_sheet")
        if income is None or not hasattr(income, "columns") or income.empty:
            return [], [f"Yahoo : états financiers annuels indisponibles pour {ticker}."]

        dividends_by_year = self.dividends_by_year(ticker, use_cache=use_cache)

        records: list[AnnualRecord] = []
        for column in income.columns:
            period_end = pd.Timestamp(column).date()
            fiscal_year = period_end.year if period_end.month >= 6 else period_end.year - 1
            values: dict[str, float | None] = {}
            for field, labels in INCOME_MAP.items():
                values[field] = self._pick(income, labels, column)
            for field, labels in BALANCE_MAP.items():
                values[field] = (
                    self._pick(balance, labels, column)
                    if balance is not None and hasattr(balance, "columns") and column in balance.columns
                    else None
                )
            values["dividend_per_share"] = dividends_by_year.get(fiscal_year)
            records.append(
                AnnualRecord(fiscal_year=fiscal_year, period_end=period_end, values=values)
            )

        if balance is None or not hasattr(balance, "columns") or balance.empty:
            warnings.append(
                f"Yahoo : bilan annuel indisponible pour {ticker} — critères de "
                "qualité de bilan non calculables."
            )

        if use_cache:
            self.cache.set(
                "yahoo_annual",
                cache_key,
                {
                    "records": [
                        {
                            "fiscal_year": r.fiscal_year,
                            "period_end": r.period_end.isoformat() if r.period_end else None,
                            "values": r.values,
                        }
                        for r in records
                    ],
                    "warnings": warnings,
                },
            )
        return records, warnings

    # ------------------------------------------------------------ marches
    def price_history(self, ticker: str, *, period: str = "5y", use_cache: bool = True) -> pd.DataFrame | None:
        cache_key = f"{ticker}:{period}"
        if use_cache:
            cached = self.cache.get("yahoo_prices", cache_key)
            if cached is not None:
                frame = pd.DataFrame(cached)
                if frame.empty:
                    return None
                frame["date"] = pd.to_datetime(frame["date"])
                return frame.set_index("date")

        # auto_adjust=False : la colonne Close reste ajustee des divisions
        # d'actions mais PAS des dividendes. C'est la serie correcte pour
        # reconstituer un P/E historique (un cours reajuste des dividendes
        # sous-estime le P/E passe).
        hist = self._fetch(ticker, "history", period=period, auto_adjust=False)
        if hist is None or not hasattr(hist, "empty") or hist.empty:
            return None
        frame = hist[["Close"]].copy()
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "date"
        if use_cache:
            payload = [
                {"date": idx.strftime("%Y-%m-%d"), "Close": float(row["Close"])}
                for idx, row in frame.iterrows()
            ]
            self.cache.set("yahoo_prices", cache_key, payload)
        return frame

    def splits(self, ticker: str, *, use_cache: bool = True) -> dict[str, float]:
        """Divisions/regroupements d'actions : {date ISO: ratio}.

        Indispensable pour retraiter les donnees PAR ACTION d'EDGAR, qui sont
        publiees telles quelles a l'epoque du depot et ne sont jamais
        reajustees apres une division d'actions.
        """
        if use_cache:
            cached = self.cache.get("yahoo_splits", ticker)
            if cached is not None:
                return {str(k): float(v) for k, v in cached.items()}
        series = self._with_fallback(ticker, "splits")
        result: dict[str, float] = {}
        if series is not None and hasattr(series, "empty") and not series.empty:
            for idx, value in series.items():
                if value and float(value) > 0:
                    result[pd.Timestamp(idx).tz_localize(None).strftime("%Y-%m-%d")] = float(value)
        if use_cache:
            self.cache.set("yahoo_splits", ticker, result)
        return result

    def dividends_by_year(self, ticker: str, *, use_cache: bool = True) -> dict[int, float]:
        """Dividende total verse par annee civile (par action)."""
        if use_cache:
            cached = self.cache.get("yahoo_dividends", ticker)
            if cached is not None:
                return {int(k): float(v) for k, v in cached.items()}

        # Une serie de dividendes vide est un cas legitime (titre non
        # distributeur) : un seul essai suffit, inutile d'attendre.
        series = self._with_fallback(ticker, "dividends")
        result: dict[int, float] = {}
        if series is not None and hasattr(series, "empty") and not series.empty:
            grouped = safe_call(lambda: series.groupby(series.index.year).sum(), default=None)
            if grouped is not None:
                result = {int(year): float(value) for year, value in grouped.items()}
        if use_cache:
            self.cache.set("yahoo_dividends", ticker, result)
        return result
