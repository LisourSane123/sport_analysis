"""Rozpoznawanie, kto stanal na wadze.

Dla kazdego profilu liczymy **przedzial predykcyjny dla nowego pomiaru** na
podstawie wazen z ostatniego tygodnia:

    srodek  = przewidywana waga na moment pomiaru
              (srednia, a przy n >= 3 regresja liniowa - waga w trakcie diety dryfuje)
    se_pred = s_eff * sqrt(1 + 1/n)          # blad predykcji nowej obserwacji
    granice = srodek +- t(1-alpha/2, df) * se_pred

`s_eff` to odchylenie probkowe sciagniete w kierunku odchylenia a priori
(`SD_PRIOR`), zeby przy 1-2 pomiarach przedzial nie robil sie absurdalny:

    s_eff^2 = ((n-1) * s^2 + nu0 * SD_PRIOR^2) / (n - 1 + nu0)

Score = zestandaryzowana odleglosc od granicy przedzialu:

    z     = |waga - srodek| / se_pred
    score = t_krytyczne - z        # > 0 wewnatrz przedzialu, im wiecej tym pewniej

Wygrywa profil o najwyzszym score. Gdy waga nie trafia w zaden przedzial,
przypisujemy ja do profilu o najblizszym ostatnim pomiarze (a gdy profil nie ma
jeszcze zadnego - do wagi referencyjnej podanej przy zakladaniu profilu).

Rozklad t-Studenta jest tu wlasciwym wyborem: szacujemy srednia i wariancje z
kilku obserwacji o rozkladzie w przyblizeniu normalnym. Rozklad Beta opisuje
zmienne ograniczone do [0,1] (proporcje) - do wagi w kg nie pasuje.

Modul nie ma zaleznosci zewnetrznych (bez scipy) - liczy dystrybuante t
przez niepelna funkcje beta.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# --- parametry domyslne (nadpisywane z app.config) ---
WINDOW_DAYS = 7        # okno historii branej pod uwage
CONFIDENCE = 0.95      # poziom ufnosci przedzialu predykcyjnego
SD_PRIOR = 0.8         # zakladane wahania dobowe wagi [kg]
NU_PRIOR = 2.0         # sila tego zalozenia (w pseudo-obserwacjach)
SD_FLOOR = 0.2         # minimalne odchylenie (powtarzalnosc wagi) [kg]
MAX_HALF_KG = 6.0      # gorny limit polszerokosci przedzialu [kg]
FALLBACK_MAX_KG = 8.0  # dalej niz tyle od ostatniego pomiaru -> nie przypisuj


# --------------------------------------------------------------------------
# Rozklad t-Studenta bez scipy
# --------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    """Ulamek lancuchowy dla niepelnej funkcji beta (Numerical Recipes)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        d = 1.0 / (d if abs(d) > tiny else tiny)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        d = 1.0 / (d if abs(d) > tiny else tiny)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularyzowana niepelna funkcja beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Dystrybuanta rozkladu t-Studenta."""
    if df <= 0:
        raise ValueError("df musi byc dodatnie")
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Kwantyl rozkladu t-Studenta (bisekcja - wystarczajaco szybka i stabilna)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p musi byc w (0, 1)")
    if p == 0.5:
        return 0.0
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p and hi < 1e6:
        hi *= 2.0
    if p < 0.5:                                   # symetria rozkladu
        return -t_ppf(1.0 - p, df)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# Model jednego profilu
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    user_id: int
    username: str
    display_name: str
    n: int                  # liczba pomiarow w oknie
    center: float           # przewidywana waga [kg]
    se_pred: float          # blad predykcji [kg]
    t_crit: float
    score: float            # t_krytyczne - z (dodatni = w przedziale)
    trend_kg_per_day: float | None = None

    @property
    def inside(self) -> bool:
        return self.score >= 0

    @property
    def half_width(self) -> float:
        return self.t_crit * self.se_pred

    @property
    def bounds(self) -> tuple[float, float]:
        return self.center - self.half_width, self.center + self.half_width

    @property
    def margin_kg(self) -> float:
        """Odleglosc od granicy w kg (dodatnia = zapas wewnatrz przedzialu)."""
        return self.score * self.se_pred


def _linear_trend(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Regresja ys ~ a + b*xs metoda najmniejszych kwadratow. None gdy sie nie da."""
    n = len(xs)
    mean_x = sum(xs) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx < 1e-9:
        return None
    mean_y = sum(ys) / n
    b = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    return mean_y - b * mean_x, b


def build_candidate(user, samples: list[tuple[datetime, float]], at: datetime,
                    ref_weight: float | None = None, *,
                    confidence: float = CONFIDENCE, sd_prior: float = SD_PRIOR,
                    nu_prior: float = NU_PRIOR, sd_floor: float = SD_FLOOR,
                    max_half_kg: float = MAX_HALF_KG) -> Candidate | None:
    """Buduje przedzial predykcyjny profilu na moment `at`.

    `samples` to (czas, waga) z okna; `ref_weight` sluzy, gdy pomiarow brak.
    """
    weights = [w for _, w in samples]
    n = len(weights)

    if n == 0:
        if ref_weight is None:
            return None
        center, s2, resid_df, trend = float(ref_weight), 0.0, 0.0, None
        n_eff = 1
    else:
        n_eff = n
        days = [(ts - at).total_seconds() / 86400.0 for ts, _ in samples]
        trend = None
        fit = _linear_trend(days, weights) if n >= 3 else None
        if fit is not None:
            intercept, slope = fit
            if abs(slope) > 0.5:                  # >0.5 kg/dzien to nie trend, to szum
                fit = None
            else:
                trend = slope
                center = intercept                # predykcja w punkcie at (dni = 0)
                residuals = [w - (intercept + slope * d) for d, w in zip(days, weights)]
                s2 = sum(r * r for r in residuals) / (n - 2)
                resid_df = n - 2
        if fit is None:
            center = sum(weights) / n
            s2 = (sum((w - center) ** 2 for w in weights) / (n - 1)) if n > 1 else 0.0
            resid_df = max(n - 1, 0)

    # sciagniecie wariancji w kierunku a priori - chroni przy 1-2 pomiarach
    s_eff = math.sqrt((resid_df * s2 + nu_prior * sd_prior ** 2) / (resid_df + nu_prior))
    s_eff = max(s_eff, sd_floor)
    df = resid_df + nu_prior

    se_pred = s_eff * math.sqrt(1.0 + 1.0 / n_eff)
    t_crit = t_ppf(1.0 - (1.0 - confidence) / 2.0, df)
    if t_crit * se_pred > max_half_kg:            # limit szerokosci przedzialu
        t_crit = max_half_kg / se_pred

    return Candidate(
        user_id=user["id"], username=user["username"], display_name=user["display_name"],
        n=n, center=center, se_pred=se_pred, t_crit=t_crit, score=float("nan"),
        trend_kg_per_day=trend,
    )


# --------------------------------------------------------------------------
# Wynik rozpoznania
# --------------------------------------------------------------------------
@dataclass
class Identification:
    user_id: int | None
    username: str | None
    method: str            # interval | fallback_last | fallback_ref | unassigned
    score: float | None    # zapas w kg wzgledem granicy / minus odleglosc przy fallbacku
    candidates: list[Candidate]
    detail: str = ""


def identify(conn, weight: float, at: datetime | None = None, *,
             window_days: int = WINDOW_DAYS, confidence: float = CONFIDENCE,
             sd_prior: float = SD_PRIOR, nu_prior: float = NU_PRIOR,
             sd_floor: float = SD_FLOOR, max_half_kg: float = MAX_HALF_KG,
             fallback_max_kg: float = FALLBACK_MAX_KG) -> Identification:
    """Przypisuje pomiar do profilu. Nie zapisuje niczego do bazy."""
    at = at or datetime.now()
    since = (at - timedelta(days=window_days)).isoformat(timespec="seconds")

    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    if not users:
        return Identification(None, None, "unassigned", None, [], "brak profili")

    candidates: list[Candidate] = []
    for user in users:
        rows = conn.execute(
            """SELECT measured_at, weight_kg FROM measurements
               WHERE user_id = ? AND measured_at >= ? AND measured_at <= ?
               ORDER BY measured_at""",
            (user["id"], since, at.isoformat(timespec="seconds"))).fetchall()
        samples = [(datetime.fromisoformat(r["measured_at"]), r["weight_kg"]) for r in rows]
        cand = build_candidate(user, samples, at, user["ref_weight"],
                               confidence=confidence, sd_prior=sd_prior,
                               nu_prior=nu_prior, sd_floor=sd_floor,
                               max_half_kg=max_half_kg)
        if cand is None:
            continue
        cand.score = cand.t_crit - abs(weight - cand.center) / cand.se_pred
        candidates.append(cand)

    candidates.sort(key=lambda c: c.score, reverse=True)
    inside = [c for c in candidates if c.inside]

    if inside:
        best = inside[0]
        detail = f"przedzial {best.bounds[0]:.1f}-{best.bounds[1]:.1f} kg (n={best.n})"
        if len(inside) > 1:
            detail += f", drugi kandydat: {inside[1].display_name}"
        return Identification(best.user_id, best.username, "interval",
                              round(best.margin_kg, 2), candidates, detail)

    # --- brak trafienia: najblizszy ostatni pomiar ---
    last = conn.execute(
        """SELECT u.id, u.username, u.ref_weight, m.weight_kg, m.measured_at
           FROM users u
           LEFT JOIN measurements m ON m.id = (
               SELECT id FROM measurements WHERE user_id = u.id
               ORDER BY measured_at DESC LIMIT 1)""").fetchall()

    best_row, best_dist, source = None, None, ""
    for row in last:
        reference = row["weight_kg"] if row["weight_kg"] is not None else row["ref_weight"]
        if reference is None:
            continue
        dist = abs(weight - reference)
        if best_dist is None or dist < best_dist:
            best_row, best_dist = row, dist
            source = "last" if row["weight_kg"] is not None else "ref"

    if best_row is None:
        return Identification(None, None, "unassigned", None, candidates,
                              "profile nie maja zadnych pomiarow ani wagi referencyjnej")
    if best_dist > fallback_max_kg:
        return Identification(None, None, "unassigned", round(-best_dist, 2), candidates,
                              f"najblizszy profil ({best_row['username']}) rozni sie o "
                              f"{best_dist:.1f} kg > limit {fallback_max_kg} kg")

    return Identification(best_row["id"], best_row["username"], f"fallback_{source}",
                          round(-best_dist, 2), candidates,
                          f"poza przedzialami; {best_dist:.1f} kg od ostatniej wagi")
