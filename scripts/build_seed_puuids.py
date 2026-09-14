"""
Genera data/seed_puuids.json automaticamente desde Riot API.

Estrategia:
- Toma puuids desde league-v4 (RANKED_SOLO_5x5) por tier/division.
- Si alguna respuesta no trae puuid, usa fallback summoner-v4.
- Selecciona semillas con muestreo estratificado por tier.
- Genera semillas balanceadas para EUW y KR.

Uso:
  python scripts/build_seed_puuids.py --per-region 30
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT_DIR / "data" / "seed_puuids.json"
REPORT_FILE = ROOT_DIR / "data" / "seed_sampling_report.json"
TIER_MAP_FILE = ROOT_DIR / "data" / "puuid_seed_tier.json"
# Incluye tiers bajos para reducir sesgo.
TIERS = ["BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
DIVISIONS = ["I", "II", "III", "IV"]
REGIONS = ["EUW1", "KR"]


def load_dotenv_if_present() -> None:
    if os.getenv("RIOT_API_KEY"):
        return

    env_file = ROOT_DIR / ".env"
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


def get_api_key() -> str:
    key = os.getenv("RIOT_API_KEY")
    if not key:
        raise RuntimeError("Falta RIOT_API_KEY en entorno o .env")
    return key


class RateLimiter:
    """Limitador simple para developer key."""

    def __init__(self, max_per_second: int = 18, max_per_two_min: int = 95) -> None:
        self.max_per_second = max_per_second
        self.max_per_two_min = max_per_two_min
        self.short_window = 1.0
        self.long_window = 120.0
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, host: str) -> None:
        while True:
            now = time.time()
            queue = self.events[host]

            while queue and now - queue[0] > self.long_window:
                queue.popleft()

            count_long = len(queue)
            count_short = 0
            for ts in reversed(queue):
                if now - ts <= self.short_window:
                    count_short += 1
                else:
                    break

            wait_short = 0.0
            wait_long = 0.0
            if count_short >= self.max_per_second:
                short_ts = queue[-self.max_per_second]
                wait_short = self.short_window - (now - short_ts) + 0.01
            if count_long >= self.max_per_two_min:
                long_ts = queue[0]
                wait_long = self.long_window - (now - long_ts) + 0.01

            wait_for = max(wait_short, wait_long)
            if wait_for <= 0:
                queue.append(now)
                return
            time.sleep(wait_for)


def riot_get(url: str, headers: dict[str, str], limiter: RateLimiter) -> Any:
    host = url.split("/")[2]
    for _ in range(5):
        limiter.acquire(host)
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        if r.status_code >= 500:
            time.sleep(2)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Fallo persistente en request: {url}")


def fetch_puuids_for_tier(
    region: str,
    tier: str,
    division: str,
    headers: dict[str, str],
    limiter: RateLimiter,
    page_limit: int,
) -> tuple[list[str], list[str]]:
    puuids: list[str] = []
    summoner_ids_fallback: list[str] = []
    out: list[str] = []
    for page in range(1, page_limit + 1):
        url = (
            f"https://{region.lower()}.api.riotgames.com/lol/league/v4/entries/"
            f"RANKED_SOLO_5x5/{tier}/{division}?page={page}"
        )
        data = riot_get(url, headers, limiter)
        if not isinstance(data, list) or not data:
            break
        for x in data:
            p = x.get("puuid")
            s = x.get("summonerId")
            if p:
                puuids.append(str(p))
            elif s:
                summoner_ids_fallback.append(str(s))
    return puuids, summoner_ids_fallback


def fetch_apex_puuids(
    region: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> tuple[list[str], list[str]]:
    puuids: list[str] = []
    summoner_ids_fallback: list[str] = []
    for tier_name in ["challenger", "grandmaster", "master"]:
        url = (
            f"https://{region.lower()}.api.riotgames.com/lol/league/v4/"
            f"{tier_name}leagues/by-queue/RANKED_SOLO_5x5"
        )
        data = riot_get(url, headers, limiter)
        entries = data.get("entries", []) if isinstance(data, dict) else []
        for x in entries:
            p = x.get("puuid")
            s = x.get("summonerId")
            if p:
                puuids.append(str(p))
            elif s:
                summoner_ids_fallback.append(str(s))
    return puuids, summoner_ids_fallback


def summoner_id_to_puuid(
    region: str,
    summoner_id: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> str | None:
    url = f"https://{region.lower()}.api.riotgames.com/lol/summoner/v4/summoners/{summoner_id}"
    try:
        payload = riot_get(url, headers, limiter)
    except requests.HTTPError:
        return None
    puuid = payload.get("puuid") if isinstance(payload, dict) else None
    return str(puuid) if puuid else None


def build_region_pool(
    region: str,
    headers: dict[str, str],
    limiter: RateLimiter,
    page_limit: int,
) -> dict[str, set[str]]:
    tier_to_puuids: dict[str, set[str]] = {tier: set() for tier in TIERS}
    fallback_summoner_ids: set[str] = set()
    per_tier_counts: dict[str, int] = {}
    for tier in TIERS:
        tier_before = len(tier_to_puuids[tier])
        for division in DIVISIONS:
            puuid_batch, sid_fallback_batch = fetch_puuids_for_tier(
                region=region,
                tier=tier,
                division=division,
                headers=headers,
                limiter=limiter,
                page_limit=page_limit,
            )
            tier_to_puuids[tier].update(puuid_batch)
            fallback_summoner_ids.update(sid_fallback_batch)
        per_tier_counts[tier] = len(tier_to_puuids[tier]) - tier_before

    print(f"[INFO] {region}: conteo por tier={per_tier_counts}")

    # Fallback: si entries devuelve muy poco, tiramos de ligas apex.
    total_direct = sum(len(v) for v in tier_to_puuids.values())
    if total_direct < 100:
        apex_puuids, apex_sid_fallback = fetch_apex_puuids(region, headers, limiter)
        # Asignamos apex al estrato DIAMOND para mantener estratificacion operativa.
        tier_to_puuids["DIAMOND"].update(apex_puuids)
        fallback_summoner_ids.update(apex_sid_fallback)
        print(f"[INFO] {region}: fallback apex añadido={len(apex_puuids)} puuids")

    total_after_apex = sum(len(v) for v in tier_to_puuids.values())
    print(f"[INFO] {region}: puuids directos candidatos={total_after_apex}")
    print(f"[INFO] {region}: summonerId fallback candidatos={len(fallback_summoner_ids)}")

    recovered_from_fallback = 0
    for sid in fallback_summoner_ids:
        puuid = summoner_id_to_puuid(region, sid, headers, limiter)
        if puuid:
            tier_to_puuids["DIAMOND"].add(puuid)
            recovered_from_fallback += 1

    print(f"[INFO] {region}: puuids via fallback summoner-v4={recovered_from_fallback}")
    total_final = sum(len(v) for v in tier_to_puuids.values())
    print(f"[INFO] {region}: puuids totales obtenidos={total_final}")
    return tier_to_puuids


def stratified_sample_by_tier(
    tier_to_puuids: dict[str, set[str]],
    per_region: int,
    rng: random.Random,
) -> tuple[list[str], dict[str, int], dict[str, int], dict[str, str]]:
    """
    Muestreo estratificado por tier:
    - Reparte per_region en cuotas casi iguales por tier.
    - Si un estrato no llega, rellena con los estratos que tengan excedente.
    """
    base = per_region // len(TIERS)
    remainder = per_region % len(TIERS)
    quotas = {tier: base for tier in TIERS}
    for tier in TIERS[:remainder]:
        quotas[tier] += 1

    selected: list[str] = []
    selected_set: set[str] = set()
    selected_by_tier: dict[str, int] = {tier: 0 for tier in TIERS}
    selected_tier_by_puuid: dict[str, str] = {}

    # Primer paso: respetar cuota por estrato
    for tier in TIERS:
        candidates = list(tier_to_puuids.get(tier, set()))
        candidates = [p for p in candidates if p not in selected_set]
        k = min(quotas[tier], len(candidates))
        picks = rng.sample(candidates, k) if k > 0 else []
        selected.extend(picks)
        selected_set.update(picks)
        selected_by_tier[tier] = k
        for p in picks:
            selected_tier_by_puuid[p] = tier

    # Segundo paso: si faltan, rellenar con cualquier excedente
    missing = per_region - len(selected)
    if missing > 0:
        pool = []
        for tier in TIERS:
            for p in tier_to_puuids.get(tier, set()):
                if p not in selected_set:
                    pool.append(p)
        if len(pool) < missing:
            raise RuntimeError(
                f"No hay puuids suficientes para completar el muestreo: faltan {missing}."
            )
        extra = rng.sample(pool, missing)
        selected.extend(extra)
        selected_set.update(extra)

        # Contabilizamos relleno por tier para reporte
        for p in extra:
            for tier in TIERS:
                if p in tier_to_puuids.get(tier, set()):
                    selected_by_tier[tier] += 1
                    selected_tier_by_puuid[p] = tier
                    break

    available_by_tier = {tier: len(tier_to_puuids.get(tier, set())) for tier in TIERS}
    return selected, selected_by_tier, available_by_tier, selected_tier_by_puuid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generador de seed_puuids.json desde Riot API")
    parser.add_argument("--per-region", type=int, default=30, help="Numero de puuids por region")
    parser.add_argument("--page-limit", type=int, default=2, help="Paginas por tier/division")
    parser.add_argument("--random-seed", type=int, default=42, help="Semilla de muestreo reproducible")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.random_seed)
    load_dotenv_if_present()

    api_key = get_api_key()
    headers = {"X-Riot-Token": api_key}
    limiter = RateLimiter()

    euw_pool_by_tier = build_region_pool("EUW1", headers, limiter, args.page_limit)
    kr_pool_by_tier = build_region_pool("KR", headers, limiter, args.page_limit)

    euw_rng = random.Random(args.random_seed + 1)
    kr_rng = random.Random(args.random_seed + 2)

    euw_selected, euw_selected_by_tier, euw_available_by_tier, euw_tier_map = stratified_sample_by_tier(
        euw_pool_by_tier,
        args.per_region,
        euw_rng,
    )
    kr_selected, kr_selected_by_tier, kr_available_by_tier, kr_tier_map = stratified_sample_by_tier(
        kr_pool_by_tier,
        args.per_region,
        kr_rng,
    )

    result = {
        "EUW": euw_selected,
        "KR": kr_selected,
    }
    OUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    tier_rows = []
    for region_key, puuids in result.items():
        tier_lookup = euw_tier_map if region_key == "EUW" else kr_tier_map
        for puuid in puuids:
            tier_rows.append(
                {
                    "puuid": puuid,
                    "region": region_key,
                    "tier": tier_lookup.get(puuid),
                    "source": "seed_stratified",
                }
            )
    TIER_MAP_FILE.write_text(json.dumps(tier_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "method": "stratified_by_tier",
        "tiers": TIERS,
        "per_region": args.per_region,
        "random_seed": args.random_seed,
        "available_by_region_tier": {
            "EUW": euw_available_by_tier,
            "KR": kr_available_by_tier,
        },
        "selected_by_region_tier": {
            "EUW": euw_selected_by_tier,
            "KR": kr_selected_by_tier,
        },
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Semillas guardadas en: {OUT_FILE}")
    print(f"[OK] EUW={len(result['EUW'])}, KR={len(result['KR'])}")
    print(f"[OK] Reporte de muestreo guardado en: {REPORT_FILE}")
    print(f"[OK] Tier de semillas guardado en: {TIER_MAP_FILE}")


if __name__ == "__main__":
    main()
