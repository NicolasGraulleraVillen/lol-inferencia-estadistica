"""
Procesa JSON crudo de Riot API a datasets listos para inferencia.

Entradas:
  - data/raw/matches/*.json
  - data/raw/timelines/*.json

Salidas:
  - data/matches_player_level.csv
  - data/matches_team_level.csv
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pandas as pd
import requests


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAW_MATCH_DIR = DATA_DIR / "raw" / "matches"
RAW_TIMELINE_DIR = DATA_DIR / "raw" / "timelines"
PLAYER_OUT = DATA_DIR / "matches_player_level.csv"
TEAM_OUT = DATA_DIR / "matches_team_level.csv"
CLASS_MAP_FILE = DATA_DIR / "champion_class_map.json"
SEED_TIER_FILE = DATA_DIR / "puuid_seed_tier.json"
TIER_CACHE_FILE = DATA_DIR / "puuid_tier_cache.json"

REGION_TO_PLATFORM = {
    "EUW": "euw1",
    "KR": "kr",
}


def load_dotenv_if_present() -> None:
    if os.getenv("RIOT_API_KEY"):
        return
    env_file = DATA_DIR.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class RateLimiter:
    def __init__(self, max_per_second: int = 18, max_per_two_min: int = 95) -> None:
        self.max_per_second = max_per_second
        self.max_per_two_min = max_per_two_min
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, host: str) -> None:
        while True:
            now = time.time()
            queue = self.events[host]
            while queue and now - queue[0] > 120.0:
                queue.popleft()
            count_short = sum(1 for ts in reversed(queue) if now - ts <= 1.0)
            wait_for = 0.0
            if count_short >= self.max_per_second:
                wait_for = max(wait_for, 1.0 - (now - queue[-self.max_per_second]) + 0.01)
            if len(queue) >= self.max_per_two_min:
                wait_for = max(wait_for, 120.0 - (now - queue[0]) + 0.01)
            if wait_for <= 0:
                queue.append(now)
                return
            time.sleep(wait_for)


def riot_get(url: str, headers: dict[str, str], limiter: RateLimiter) -> Any:
    host = url.split("/")[2]
    for _ in range(5):
        limiter.acquire(host)
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", "2")))
            continue
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            time.sleep(2)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Fallo persistente en request: {url}")


def load_seed_tier_map() -> dict[str, dict[str, str]]:
    if not SEED_TIER_FILE.exists():
        return {}
    rows = json.loads(SEED_TIER_FILE.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        puuid = row.get("puuid")
        if puuid:
            out[str(puuid)] = {
                "tier": row.get("tier"),
                "rank": None,
                "league_points": None,
                "source": row.get("source", "seed_stratified"),
            }
    return out


def load_tier_cache() -> dict[str, dict[str, Any]]:
    if not TIER_CACHE_FILE.exists():
        return {}
    return json.loads(TIER_CACHE_FILE.read_text(encoding="utf-8"))


def save_tier_cache(cache: dict[str, dict[str, Any]]) -> None:
    TIER_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_soloq_entry(platform: str, puuid: str, headers: dict[str, str], limiter: RateLimiter) -> dict[str, Any] | None:
    url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    data = riot_get(url, headers, limiter)
    if not isinstance(data, list):
        return None
    for entry in data:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return {
                "tier": entry.get("tier"),
                "rank": entry.get("rank"),
                "league_points": entry.get("leaguePoints"),
                "source": "league_v4_snapshot",
            }
    return None


def enrich_player_tiers(df: pd.DataFrame, fetch_missing: bool) -> pd.DataFrame:
    seed_map = load_seed_tier_map()
    cache = load_tier_cache()
    limiter = RateLimiter()
    headers = {"X-Riot-Token": os.getenv("RIOT_API_KEY", "")}

    unique_pairs = df[["puuid", "region"]].drop_duplicates()
    fetched = 0
    for _, row in unique_pairs.iterrows():
        puuid = str(row["puuid"])
        region = str(row["region"])
        if puuid in cache:
            continue
        if puuid in seed_map and not fetch_missing:
            cache[puuid] = seed_map[puuid]
            continue
        if not fetch_missing or not headers["X-Riot-Token"]:
            continue
        platform = REGION_TO_PLATFORM.get(region)
        if not platform:
            continue
        entry = fetch_soloq_entry(platform, puuid, headers, limiter)
        if entry:
            cache[puuid] = entry
        else:
            cache[puuid] = {"tier": None, "rank": None, "league_points": None, "source": "not_found"}
        fetched += 1
        if fetched % 50 == 0:
            save_tier_cache(cache)
            print(f"[INFO] Tier cache actualizado ({fetched} consultas nuevas)")

    save_tier_cache(cache)

    def lookup_tier(puuid: str) -> str | None:
        payload = cache.get(str(puuid)) or seed_map.get(str(puuid))
        return payload.get("tier") if payload else None

    def lookup_rank(puuid: str) -> str | None:
        payload = cache.get(str(puuid)) or seed_map.get(str(puuid))
        return payload.get("rank") if payload else None

    def lookup_lp(puuid: str) -> float | None:
        payload = cache.get(str(puuid)) or seed_map.get(str(puuid))
        lp = payload.get("league_points") if payload else None
        return float(lp) if lp is not None else None

    df = df.copy()
    df["tier"] = df["puuid"].map(lookup_tier)
    df["rank"] = df["puuid"].map(lookup_rank)
    df["league_points"] = df["puuid"].map(lookup_lp)
    known = int(df["tier"].notna().sum())
    print(f"[INFO] Filas con tier conocido: {known}/{len(df)}")
    return df


def normalize_role(position: str) -> str:
    value = (position or "").upper()
    mapping = {
        "TOP": "Top",
        "JUNGLE": "Jungle",
        "MIDDLE": "Mid",
        "MID": "Mid",
        "BOTTOM": "ADC",
        "BOT": "ADC",
        "UTILITY": "Support",
        "SUPPORT": "Support",
    }
    return mapping.get(value, "Unknown")


def detect_region(match_id: str) -> str:
    if match_id.startswith("KR_"):
        return "KR"
    if match_id.startswith("EUW1_"):
        return "EUW"
    return "OTHER"


def get_duration_minutes(info: dict[str, Any]) -> float:
    duration = float(info.get("gameDuration", 0))
    # Compatibilidad con casos donde duration viene en ms.
    if duration > 10000:
        duration = duration / 1000.0
    return max(duration / 60.0, 1e-6)


def extract_gold_15(timeline: dict[str, Any], participant_id: int) -> float | None:
    frames = timeline.get("info", {}).get("frames", [])
    if not frames:
        return None
    frame_index = 15 if len(frames) > 15 else len(frames) - 1
    participant_frames = frames[frame_index].get("participantFrames", {})
    payload = participant_frames.get(str(participant_id), {})
    total_gold = payload.get("totalGold")
    return float(total_gold) if total_gold is not None else None


def fetch_champion_class_map() -> dict[str, str]:
    if CLASS_MAP_FILE.exists():
        return json.loads(CLASS_MAP_FILE.read_text(encoding="utf-8"))

    versions = requests.get(
        "https://ddragon.leagueoflegends.com/api/versions.json",
        timeout=30,
    ).json()
    latest = versions[0]
    champ_data = requests.get(
        f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/champion.json",
        timeout=30,
    ).json()

    out: dict[str, str] = {}
    for name, payload in champ_data.get("data", {}).items():
        tags = payload.get("tags", [])
        out[name] = tags[0] if tags else "Unknown"

    CLASS_MAP_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_player_dataset() -> pd.DataFrame:
    class_map = fetch_champion_class_map()
    rows: list[dict[str, Any]] = []

    match_files = sorted(RAW_MATCH_DIR.glob("*.json"))
    for match_file in match_files:
        match_id = match_file.stem
        timeline_file = RAW_TIMELINE_DIR / f"{match_id}.json"
        if not timeline_file.exists():
            continue

        match_data = read_json(match_file)
        timeline_data = read_json(timeline_file)
        info = match_data.get("info", {})
        participants = info.get("participants", [])

        queue_id = int(info.get("queueId", -1))
        if queue_id != 420:
            continue

        game_creation = int(info.get("gameCreation", 0))
        game_version = str(info.get("gameVersion", ""))
        region = detect_region(match_id)
        duration_min = get_duration_minutes(info)

        for p in participants:
            participant_id = int(p.get("participantId", -1))
            champion_name = str(p.get("championName", "Unknown"))
            gold_15 = extract_gold_15(timeline_data, participant_id)
            damage_dealt = float(p.get("totalDamageDealtToChampions", 0))

            role_raw = p.get("teamPosition") or p.get("individualPosition") or ""
            role = normalize_role(str(role_raw))

            row = {
                "match_id": match_id,
                "puuid": p.get("puuid"),
                "game_creation": game_creation,
                "game_version": game_version,
                "queue_id": queue_id,
                "region": region,
                "win": int(bool(p.get("win", False))),
                "team_id": int(p.get("teamId", -1)),
                "side": "blue" if int(p.get("teamId", -1)) == 100 else "red",
                "role": role,
                "champion_name": champion_name,
                "champion_class": class_map.get(champion_name, "Unknown"),
                "gold_15": gold_15,
                "damage_dealt": damage_dealt,
                "damage_per_min": damage_dealt / duration_min if duration_min > 0 else None,
                "kills": int(p.get("kills", 0)),
                "wards_placed": int(p.get("wardsPlaced", 0)),
                "match_duration_min": duration_min,
            }
            rows.append(row)

    if not rows:
        raise RuntimeError("No se generaron filas player-match. Revisa data/raw/")

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["match_id", "puuid"])
    return df


def build_team_dataset(df_player: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df_player.groupby(["match_id", "region", "game_creation", "game_version", "queue_id", "team_id", "side"])
        .agg(
            win=("win", "max"),
            gold_15=("gold_15", "mean"),
            damage_dealt=("damage_dealt", "sum"),
            kills=("kills", "sum"),
            wards_placed=("wards_placed", "sum"),
            match_duration_min=("match_duration_min", "mean"),
        )
        .reset_index()
    )
    grouped["damage_per_min"] = grouped["damage_dealt"] / grouped["match_duration_min"]
    return grouped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Procesa JSON crudo a CSV analitico")
    parser.add_argument(
        "--no-fetch-tier",
        action="store_true",
        help="No consultar API; usar solo cache + tier de semillas",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv_if_present()
    fetch_tier = not args.no_fetch_tier

    if not RAW_MATCH_DIR.exists() or not RAW_TIMELINE_DIR.exists():
        raise FileNotFoundError("No existe data/raw/matches o data/raw/timelines.")

    df_player = build_player_dataset()
    df_player = enrich_player_tiers(df_player, fetch_missing=fetch_tier)
    df_team = build_team_dataset(df_player)

    df_player.to_csv(PLAYER_OUT, index=False)
    df_team.to_csv(TEAM_OUT, index=False)

    print(f"[OK] Player-level: {PLAYER_OUT} ({len(df_player)} filas)")
    print(f"[OK] Team-level: {TEAM_OUT} ({len(df_team)} filas)")


if __name__ == "__main__":
    main()
