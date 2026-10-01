"""Retry réseau partagé — urlopen avec tentatives + backoff sur erreurs transitoires.
Transitoire = URLError / timeout / SSL (handshake) et HTTP 429/5xx → backoff 2 s puis 5 s.
Définitif = HTTP 4xx (hors 429) → remonte immédiatement. Fail-loud conservé : après
épuisement des tentatives, l'exception d'origine est levée telle quelle."""
import time
import urllib.request
import urllib.error

_RETRY_CODES = (429, 500, 502, 503, 504)
_BACKOFF = (2, 5)


def urlopen_retry(req, timeout: int = 60, retries: int = 3):
    """urllib.request.urlopen avec retry. Retourne la réponse ouverte (context manager)."""
    for attempt in range(1, retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_CODES and attempt < retries:
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF)) - 1])
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            # URLError englobe les erreurs SSL (handshake timeout) ; TimeoutError = read timeout
            if attempt < retries:
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF)) - 1])
                continue
            raise
