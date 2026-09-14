import pandas as pd
import numpy as np
from scipy import stats

df = pd.read_csv("data/matches_player_level.csv")
df = df[df["queue_id"] == 420].copy()
df = df.dropna(subset=["role", "damage_dealt"])
df = df[df["role"] != "Unknown"].copy()


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
    print(f"=== {label} ===")
    print(f"n_ADC = {n1:,}  |  n_Mid = {n2:,}")
    print(f"X_bar = {xbar:.2f}  |  Y_bar = {ybar:.2f}  |  diff = {diff:.2f}")
    print(f"t({df_w:.1f}) = {t_stat:.4f}  |  p(bilateral) = {p_bil:.4f}")
    print(f"IC 95% = [{ci_lo:.2f}, {ci_hi:.2f}]  |  Cohen d = {d:.3f}  |  MW p = {p_mw:.4f}")
    print()


def one_per_match(sub: pd.DataFrame, side: str | None = None) -> pd.Series:
    s = sub.sort_values(["match_id", "team_id"])
    if side is not None:
        s = s[s["side"].str.lower() == side.lower()]
    return s.drop_duplicates("match_id", keep="first")["damage_dealt"]


df_c3 = df[df["role"].isin(["ADC", "Mid"])].copy()
adc_all = df_c3.loc[df_c3["role"] == "ADC", "damage_dealt"]
mid_all = df_c3.loc[df_c3["role"] == "Mid", "damage_dealt"]
run_contrast(adc_all, mid_all, "ACTUAL (todas las filas)")

adc_sub = df[df["role"] == "ADC"]
mid_sub = df[df["role"] == "Mid"]
run_contrast(
    one_per_match(adc_sub),
    one_per_match(mid_sub),
    "1 por partida (primer equipo por match_id)",
)
run_contrast(
    one_per_match(adc_sub, "blue"),
    one_per_match(mid_sub, "blue"),
    "1 por partida (solo lado azul)",
)

adc1 = adc_sub.sort_values(["match_id", "team_id"]).drop_duplicates("match_id", keep="first")
mid1 = mid_sub.sort_values(["match_id", "team_id"]).drop_duplicates("match_id", keep="first")
common = set(adc1["match_id"]).intersection(mid1["match_id"])
run_contrast(
    adc1.loc[adc1["match_id"].isin(common), "damage_dealt"],
    mid1.loc[mid1["match_id"].isin(common), "damage_dealt"],
    "1 ADC + 1 Mid por la misma partida",
)

print("Filas por partida (actual):")
print("ADC:", adc_sub.groupby("match_id").size().value_counts().sort_index().to_dict())
print("Mid:", mid_sub.groupby("match_id").size().value_counts().sort_index().to_dict())
