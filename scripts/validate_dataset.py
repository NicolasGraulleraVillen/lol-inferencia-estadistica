"""
Valida calidad del dataset procesado antes de analisis estadistico.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PLAYER_FILE = DATA_DIR / "matches_player_level.csv"
REPORT_FILE = DATA_DIR / "data_quality_report.json"

MIN_MATCHES_REQUIRED = 2000

CRITICAL_COLUMNS = [
    "region",
    "win",
    "role",
    "champion_class",
    "gold_15",
    "damage_per_min",
]


def main() -> None:
    if not PLAYER_FILE.exists():
        raise FileNotFoundError(f"No existe {PLAYER_FILE}. Ejecuta primero process_data.py")

    df = pd.read_csv(PLAYER_FILE)

    report: dict[str, object] = {}
    report["rows_player_level"] = int(len(df))
    report["unique_matches"] = int(df["match_id"].nunique())
    report["rows_per_match_expected"] = 10
    report["duplicate_match_puuid"] = int(df.duplicated(subset=["match_id", "puuid"]).sum())
    report["region_counts"] = df["region"].value_counts(dropna=False).to_dict()
    report["queue_counts"] = df["queue_id"].value_counts(dropna=False).to_dict()

    null_counts = {col: int(df[col].isna().sum()) for col in CRITICAL_COLUMNS}
    report["critical_null_counts"] = null_counts

    checks = {
        "at_least_2000_matches": report["unique_matches"] >= MIN_MATCHES_REQUIRED,
        "no_duplicate_match_puuid": report["duplicate_match_puuid"] == 0,
        "critical_nulls_ok": all(v == 0 for v in null_counts.values()),
        "only_ranked_queue_420": set(df["queue_id"].dropna().unique().tolist()) == {420},
        "regions_include_euw_kr": {"EUW", "KR"}.issubset(set(df["region"].dropna().unique().tolist())),
    }
    report["checks"] = checks
    report["all_checks_passed"] = all(checks.values())

    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Reporte guardado en: {REPORT_FILE}")
    for name, value in checks.items():
        print(f"- {name}: {'PASS' if value else 'FAIL'}")


if __name__ == "__main__":
    main()
