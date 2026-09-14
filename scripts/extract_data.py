"""
Extraccion incremental de partidas LoL desde Riot API (Match-V5).

Uso:
  python scripts/extract_data.py --target-matches 5000

Requisitos:
  1) Variable de entorno RIOT_API_KEY o archivo .env con RIOT_API_KEY=...
  2) Archivo data/seed_puuids.json con semillas por region
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RAW_DIR = DATA_DIR / "raw"
MATCH_DIR = RAW_DIR / "matches"
TIMELINE_DIR = RAW_DIR / "timelines"
METADATA_FILE = RAW_DIR / "metadata.json"
SEED_FILE = DATA_DIR / "seed_puuids.json"

REGION_TO_ROUTE = {
    "EUW": "europe",
    "KR": "asia",
}

RANKED_QUEUE_IDS = {420}


def load_dotenv_if_present() -> None:
    """Carga RIOT_API_KEY desde .env si no existe en entorno."""
    if os.getenv("RIOT_API_KEY"):
        return

    env_file = Path(__file__).resolve().parents[1] / ".env"
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


class RouteRateLimiter:
    """
    Limitador por routing value.
    Riot dev key típico:
      - 20 req / 1s
      - 100 req / 120s
    """

    def __init__(self, max_per_second: int = 20, max_per_two_min: int = 100) -> None:
        self.max_per_second = max_per_second
        self.max_per_two_min = max_per_two_min
        self.short_window = 1.0
        self.long_window = 120.0
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, route: str) -> None:
        while True:
            now = time.time()
            queue = self.events[route]

            # Limpieza de eventos fuera de ventana larga.
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


def get_api_key() -> str:
    api_key = os.getenv("RIOT_API_KEY")
    if not api_key:
        raise RuntimeError("Falta RIOT_API_KEY (entorno o archivo .env).")
    return api_key


def infer_region_from_match_id(match_id: str) -> str:
    if match_id.startswith("KR_"):
        return "KR"
    if match_id.startswith("EUW1_"):
        return "EUW"
    return "OTHER"


def route_for_match_id(match_id: str) -> str:
    region = infer_region_from_match_id(match_id)
    return REGION_TO_ROUTE.get(region, "europe")


def load_seeds() -> dict[str, list[str]]:
    if not SEED_FILE.exists():
        raise FileNotFoundError(
            f"No existe {SEED_FILE}. Crea el archivo con formato {{'EUW': [...], 'KR': [...]}}."
        )
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    return {k.upper(): v for k, v in data.items()}


def route_from_url(url: str) -> str:
    host = urlparse(url).netloc
    return host.split(".")[0] if host else "unknown"


def riot_get(
    url: str,
    headers: dict[str, str],
    limiter: RouteRateLimiter,
    retries: int = 5,
) -> dict[str, Any] | list[Any]:
    route = route_from_url(url)
    for attempt in range(1, retries + 1):
        limiter.acquire(route)
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if attempt < retries:
                wait = min(2**attempt, 20)
                print(f"[RETRY] Error de red ({exc.__class__.__name__}). Reintentando en {wait}s...")
                time.sleep(wait)
                continue
            raise
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "2"))
            print(f"[RATE_LIMIT] Esperando {retry_after}s...")
            time.sleep(retry_after)
            continue
        if response.status_code >= 500 and attempt < retries:
            wait = min(2**attempt, 20)
            print(f"[RETRY] Error {response.status_code}. Reintentando en {wait}s...")
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"No se pudo resolver request tras {retries} intentos: {url}")


def fetch_match_ids(
    puuid: str,
    route: str,
    headers: dict[str, str],
    limiter: RouteRateLimiter,
    start_time: int | None,
    count: int = 100,
) -> list[str]:
    url = (
        f"https://{route}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        f"?start=0&count={count}&queue=420"
    )
    if start_time:
        url += f"&startTime={start_time}"
    data = riot_get(url, headers=headers, limiter=limiter)
    return list(data) if isinstance(data, list) else []


def fetch_match(
    route: str,
    match_id: str,
    headers: dict[str, str],
    limiter: RouteRateLimiter,
) -> dict[str, Any]:
    url = f"https://{route}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    data = riot_get(url, headers=headers, limiter=limiter)
    if not isinstance(data, dict):
        raise ValueError(f"Respuesta invalida para match {match_id}")
    return data


def fetch_timeline(
    route: str,
    match_id: str,
    headers: dict[str, str],
    limiter: RouteRateLimiter,
) -> dict[str, Any]:
    url = f"https://{route}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    data = riot_get(url, headers=headers, limiter=limiter)
    if not isinstance(data, dict):
        raise ValueError(f"Respuesta invalida para timeline {match_id}")
    return data


def load_metadata() -> dict[str, Any]:
    if not METADATA_FILE.exists():
        return {"downloaded_matches": [], "generated_at": None}
    return json.loads(METADATA_FILE.read_text(encoding="utf-8"))


def save_metadata(metadata: dict[str, Any]) -> None:
    METADATA_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extraccion incremental de partidas LoL (Riot API)")
    parser.add_argument("--target-matches", type=int, default=5000, help="Numero objetivo de partidas unicas")
    parser.add_argument(
        "--start-time",
        type=int,
        default=None,
        help="Filtro temporal epoch segundos (startTime endpoint by-puuid)",
    )
    parser.add_argument("--ids-per-seed", type=int, default=100, help="Match IDs maximos por puuid semilla")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv_if_present()
    api_key = get_api_key()
    headers = {"X-Riot-Token": api_key}
    seeds_by_region = load_seeds()
    limiter = RouteRateLimiter(max_per_second=20, max_per_two_min=100)

    MATCH_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINE_DIR.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata()
    already_downloaded = set(metadata.get("downloaded_matches", []))
    downloaded_by_region = defaultdict(int)
    for mid in already_downloaded:
        downloaded_by_region[infer_region_from_match_id(mid)] += 1

    candidate_match_ids_by_region: dict[str, set[str]] = defaultdict(set)
    for region, puuids in seeds_by_region.items():
        region = region.upper()
        route = REGION_TO_ROUTE.get(region)
        if not route:
            print(f"[WARN] Region no soportada: {region}")
            continue
        for puuid in puuids:
            try:
                ids = fetch_match_ids(
                    puuid=puuid,
                    route=route,
                    headers=headers,
                    limiter=limiter,
                    start_time=args.start_time,
                    count=args.ids_per_seed,
                )
                candidate_match_ids_by_region[region].update(ids)
                print(f"[INFO] {region} {puuid[:10]}... -> {len(ids)} IDs")
            except requests.HTTPError as exc:
                print(f"[WARN] No se pudieron recuperar IDs para {puuid[:10]}...: {exc}")

    total_candidates = sum(len(v) for v in candidate_match_ids_by_region.values())
    print(f"[INFO] IDs unicos recolectados: {total_candidates}")
    for region in sorted(candidate_match_ids_by_region.keys()):
        print(f"[INFO] IDs candidatos {region}: {len(candidate_match_ids_by_region[region])}")

    active_regions = [r for r in ["EUW", "KR"] if r in candidate_match_ids_by_region]
    if not active_regions:
        print("[WARN] No hay regiones candidatas para descargar.")
        return

    if len(active_regions) == 1:
        target_by_region = {active_regions[0]: args.target_matches}
    else:
        half = args.target_matches // 2
        target_by_region = {
            "EUW": half + (args.target_matches % 2),
            "KR": half,
        }

    remaining_by_region = {}
    for region in active_regions:
        current = downloaded_by_region.get(region, 0)
        remaining_by_region[region] = max(target_by_region[region] - current, 0)

    pending_ids_by_region: dict[str, deque[str]] = {}
    for region in active_regions:
        pending = sorted(
            mid
            for mid in candidate_match_ids_by_region[region]
            if mid not in already_downloaded
        )
        pending_ids_by_region[region] = deque(pending)

    print(f"[INFO] Target por region: {target_by_region}")
    print(
        f"[INFO] Ya descargadas por region: "
        f"{{'EUW': {downloaded_by_region.get('EUW', 0)}, 'KR': {downloaded_by_region.get('KR', 0)}}}"
    )
    print(f"[INFO] Faltantes por region: {remaining_by_region}")

    downloaded_now = 0
    while True:
        needs_work = any(remaining_by_region[r] > 0 for r in active_regions)
        if not needs_work:
            break
        if len(already_downloaded) >= args.target_matches:
            break

        progress_this_round = False
        for region in active_regions:
            if remaining_by_region[region] <= 0:
                continue

            queue = pending_ids_by_region[region]
            while queue and queue[0] in already_downloaded:
                queue.popleft()
            if not queue:
                continue

            match_id = queue.popleft()
            route = route_for_match_id(match_id)
            try:
                match_data = fetch_match(route=route, match_id=match_id, headers=headers, limiter=limiter)
                queue_id = int(match_data.get("info", {}).get("queueId", -1))
                if queue_id not in RANKED_QUEUE_IDS:
                    continue
                timeline_data = fetch_timeline(
                    route=route,
                    match_id=match_id,
                    headers=headers,
                    limiter=limiter,
                )

                (MATCH_DIR / f"{match_id}.json").write_text(
                    json.dumps(match_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (TIMELINE_DIR / f"{match_id}.json").write_text(
                    json.dumps(timeline_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                already_downloaded.add(match_id)
                downloaded_now += 1
                downloaded_by_region[region] += 1
                remaining_by_region[region] = max(remaining_by_region[region] - 1, 0)
                progress_this_round = True

                if downloaded_now % 50 == 0:
                    metadata["downloaded_matches"] = sorted(already_downloaded)
                    metadata["generated_at"] = int(time.time())
                    save_metadata(metadata)
                    print(f"[INFO] Guardado incremental: {downloaded_now} partidas nuevas")

            except requests.RequestException as exc:
                print(f"[WARN] Fallo al descargar {match_id}: {exc}")
                continue

        if not progress_this_round:
            print("[WARN] No hubo progreso en esta ronda (faltan IDs o hubo errores).")
            break

    metadata["downloaded_matches"] = sorted(already_downloaded)
    metadata["generated_at"] = int(time.time())
    save_metadata(metadata)

    print(f"[OK] Partidas totales disponibles: {len(already_downloaded)}")
    print(f"[OK] Partidas nuevas en esta ejecucion: {downloaded_now}")
    print(
        "[OK] Totales por region descargada: "
        f"EUW={downloaded_by_region.get('EUW', 0)}, KR={downloaded_by_region.get('KR', 0)}"
    )
    print(f"[OK] JSONs en: {MATCH_DIR} y {TIMELINE_DIR}")


if __name__ == "__main__":
    main()
