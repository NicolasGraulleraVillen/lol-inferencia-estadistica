# Especificacion del dataset (Trabajo final)

## Objetivo de muestra

- Poblacion: SoloQ rankeada de jugadores diversos.
- Regiones foco: EUW y KR.
- Ventana temporal: ultimos 2-3 parches.
- Tamano objetivo: 2.000 partidas validas.

## Granularidad

- Tabla principal: `player-match` (10 filas por partida).
- Tabla derivada opcional: `team-match` (2 filas por partida), para analisis por equipo.

## Archivos de salida

- `data/matches_player_level.csv` (principal para contrastes).
- `data/matches_team_level.csv` (opcional, agregada).
- `data/data_quality_report.json` (resumen de validaciones).

## Esquema minimo obligatorio (player-match)

### Identificacion y control

- `match_id` (string): ID unico de partida.
- `puuid` (string): identificador unico del jugador.
- `region` (string): `EUW` o `KR`.
- `tier` (string): liga SoloQ (ej. `GOLD`, `PLATINUM`) — snapshot via `league-v4`.
- `rank` (string): division dentro del tier (`I`, `II`, `III`, `IV`).
- `league_points` (float): LP en el momento de la consulta.
- `game_creation` (int): timestamp epoch en milisegundos.
- `game_version` (string): version/parche de partida.
- `queue_id` (int): cola de juego (solo ranked).

### Contexto competitivo

- `win` (int): 1 si gana, 0 si pierde.
- `side` (string): `blue` o `red`.
- `team_id` (int): 100 (blue) o 200 (red).
- `role` (string): `Top`, `Jungle`, `Mid`, `ADC`, `Support`.
- `champion_name` (string): campeon jugado.
- `champion_class` (string): clase sintetica (`Tank`, `Mage`, `Assassin`, `Fighter`, `Marksman`, `Support`, `Specialist`, `Unknown`).

### Metricas para contrastes

- `gold_15` (float): oro al minuto 15 (timeline).
- `damage_dealt` (float): dano total a campeones.
- `damage_per_min` (float): dano por minuto.
- `kills` (int): asesinatos.
- `wards_placed` (int): wards colocados.
- `match_duration_min` (float): duracion en minutos (control de calidad y derivadas).

## Endpoints Riot necesarios

- `GET /lol/match/v5/matches/by-puuid/{puuid}/ids`
  - Recolectar IDs de partidas por jugador semilla.
- `GET /lol/match/v5/matches/{matchId}`
  - Obtener metadatos y estadisticas finales por participante.
- `GET /lol/match/v5/matches/{matchId}/timeline`
  - Extraer snapshots para construir `gold_15`.

## Que es un PUUID y para que se usa

- `puuid` significa *platform unique universal identifier* del jugador.
- Es un identificador estable y anonimo que Riot usa para referenciar cuentas.
- Se usa para:
  - pedir historial de partidas (`match-v5/by-puuid/ids`),
  - enlazar datos de jugador entre endpoints,
  - evitar depender de nombre de invocador (que puede cambiar).

## Como seleccionamos los PUUID (muestreo estratificado)

Para reducir sesgo de muestra, los `puuid` semilla se eligen con estratificacion por tier:

1. Para cada region (`EUW1`, `KR`), se consultan entradas SoloQ (`RANKED_SOLO_5x5`) en los estratos:
   - `BRONZE`, `SILVER`, `GOLD`, `PLATINUM`, `EMERALD`, `DIAMOND`.
2. Se construye un pool de `puuid` por estrato.
3. Se reparte una cuota por estrato (casi uniforme) segun `--per-region`.
4. Se seleccionan `puuid` aleatoriamente dentro de cada estrato con semilla reproducible (`--random-seed`).
5. Si un estrato no cubre su cuota, se rellena con excedente de otros estratos.

Salida de semillas:

- `data/seed_puuids.json` con listas `EUW` y `KR`.
- `data/seed_sampling_report.json` con disponibilidad y seleccion por tier.
- `data/puuid_seed_tier.json` tier de jugadores semilla (estrato de muestreo).
- `data/puuid_tier_cache.json` cache de tier/rank por `puuid` (consulta `league-v4`).

**Nota sobre tier:** Match-V5 no incluye elo historico por partida. El tier se obtiene con `league-v4` en el procesado (snapshot al ejecutar `process_data.py`), no necesariamente el tier exacto en el minuto de esa partida.

## Estrategia de muestreo (EUW/KR)

1. Crear semillas de `puuid` en ambas regiones (diversidad de perfiles).
2. Obtener `match_ids` rankeadas recientes por cada semilla.
3. Unificar y deduplicar `match_ids`.
4. Descargar detalle y timeline de cada partida.
5. Mantener balance minimo por region para comparacion Mann-Whitney.

## Restricciones de cola

- Solo colas rankeadas estandar de Summoner's Rift:
  - `420` (Ranked Solo/Duo)
  - `440` (Ranked Flex) opcional; se puede excluir para mayor homogeneidad.

## Criterios de aceptacion de datos

- >= 2.000 partidas validas.
- Sin nulos criticos en `region`, `win`, `role`, `champion_class`, `gold_15`, `damage_per_min`.
- Sin duplicados `match_id + puuid`.
- Muestra usable para los contrastes parametricos actuales y reutilizable para fases no parametricas futuras.
