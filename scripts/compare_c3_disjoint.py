import pandas as pd
import numpy as np
from scipy import stats

df = pd.read_csv("data/matches_player_level.csv")
df = df[df["queue_id"] == 420].copy()
df = df.dropna(subset=["role", "damage_dealt"])
df = df[df["role"] != "Unknown"].copy()

adc_sub = df[df["role"] == "ADC"].sort_values(["match_id", "team_id"])
mid_sub = df[df["role"] == "Mid"].sort_values(["match_id", "team_id"])

# Una fila por (match_id, rol): si hay 2, nos quedamos con el primero
adc_one = adc_sub.drop_duplicates("match_id", keep="first")
mid_one = mid_sub.drop_duplicates("match_id", keep="first")


def run_contrast(adc: pd.Series, mid: pd.Series, label: str) -> None:
    adc = adc.astype(float)
    mid = mid.astype(float)
    n1, n2 = len(adc), len(mid)
    xbar, ybar = adc.mean(), mid.mean()
    diff = xbar - ybar
    s1, s2 = adc.std(ddof=1), mid.std(ddof=1)
    t_stat, p_bil = stats.ttest_ind(adc, mid, equal_var=False)
    num = (s1**2 / n1 + s2**2 / n2) ** 2
    den = (s1**2 / n1) ** 2 / (n1 - 1) + (s2**2 / n2) ** 2 / (n2 - 1)
    df_w = num / den
    se = np.sqrt(s1**2 / n1 + s2**2 / n2)
    tc = stats.t.ppf(0.975, df_w)
    ci_lo, ci_hi = diff - tc * se, diff + tc * se
    sp = np.sqrt(((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2))
    d = diff / sp if sp > 0 else np.nan
    _, p_mw = stats.mannwhitneyu(adc, mid, alternative="two-sided")
    overlap = len(set(adc.index) & set(mid.index)) if hasattr(adc, "index") else "?"
    print(f"=== {label} ===")
    print(f"n_ADC = {n1:,}  |  n_Mid = {n2:,}  |  partidas solapadas = {overlap}")
    print(f"X_bar = {xbar:.2f}  |  Y_bar = {ybar:.2f}  |  diff = {diff:.2f}")
    print(f"t({df_w:.1f}) = {t_stat:.4f}  |  p(bilateral) = {p_bil:.4f}")
    print(f"IC 95% = [{ci_lo:.2f}, {ci_hi:.2f}]  |  Cohen d = {d:.3f}  |  MW p = {p_mw:.4f}")
    print()


# Referencia: actual (todas las filas)
run_contrast(
    df.loc[df["role"] == "ADC", "damage_dealt"],
    df.loc[df["role"] == "Mid", "damage_dealt"],
    "ACTUAL (todas las filas, ~2 ADC y 2 Mid por partida)",
)

# A) Partidas DISJUNTAS: 1000 partidas solo ADC + 1000 partidas solo Mid
match_ids = np.sort(df["match_id"].unique())
rng = np.random.default_rng(42)
shuffled = rng.permutation(match_ids)
half = len(shuffled) // 2
adc_matches = set(shuffled[:half])
mid_matches = set(shuffled[half:])

adc_disjoint = adc_one[adc_one["match_id"].isin(adc_matches)].set_index("match_id")[
    "damage_dealt"
]
mid_disjoint = mid_one[mid_one["match_id"].isin(mid_matches)].set_index("match_id")[
    "damage_dealt"
]
run_contrast(
    adc_disjoint,
    mid_disjoint,
    "DISJUNTO: ~1000 partidas aportan solo ADC + ~1000 solo Mid (sin solape)",
)

# B) Misma idea pero reparto fijo (primeras 1000 vs últimas 1000 por match_id ordenado)
adc_matches_fix = set(match_ids[:1000])
mid_matches_fix = set(match_ids[1000:])
adc_d2 = adc_one[adc_one["match_id"].isin(adc_matches_fix)]["damage_dealt"]
mid_d2 = mid_one[mid_one["match_id"].isin(mid_matches_fix)]["damage_dealt"]
run_contrast(
    adc_d2,
    mid_d2,
    "DISJUNTO (fijo): 1000 primeras partidas -> ADC, 1000 restantes -> Mid",
)

# C) Una obs por partida: en cada partida coges ADC *o* Mid (aleatorio), nunca ambos
rows = []
for mid_val, g in df.groupby("match_id"):
    adc_rows = g[g["role"] == "ADC"]
    mid_rows = g[g["role"] == "Mid"]
    if len(adc_rows) == 0 or len(mid_rows) == 0:
        continue
    pick_adc = rng.random() < 0.5
    row = adc_rows.iloc[0] if pick_adc else mid_rows.iloc[0]
    rows.append(row)

mixed = pd.DataFrame(rows)
run_contrast(
    mixed.loc[mixed["role"] == "ADC", "damage_dealt"],
    mixed.loc[mixed["role"] == "Mid", "damage_dealt"],
    "1 obs/partida: ADC o Mid al azar (mismas 2000 partidas, ~50/50, sin duplicar rol)",
)

print(f"Partidas totales en dataset: {len(match_ids):,}")
