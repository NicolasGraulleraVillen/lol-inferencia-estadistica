# Inferencia estadística sobre League of Legends

Trabajo final de Inferencia Estadística. Aplica contrastes de hipótesis, métodos no paramétricos y un modelo ARIMA a **2.000 partidas rankeadas** de *League of Legends* (EUW y KR), extraídas con la API oficial de Riot Games.

Autor: **Nicolás Graullera Villén**.

## Cómo ver los resultados

La memoria completa, con gráficos, p-valores e intervalos de confianza, está en:

- **[main.pdf](main.pdf)** — documento compilado (la forma más directa de leer el trabajo en GitHub).
- **[main.qmd](main.qmd)** — fuente reproducible (Quarto + Python).

En GitHub, pulsa `main.pdf` y usa *View raw* o la vista previa del navegador. No hace falta ejecutar código para consultar las conclusiones.

## Qué se pregunta

1. ¿El equipo ganador llega con más oro al minuto 15 que el perdedor?
2. ¿El lado azul gana más del 50 % de las partidas?
3. ¿ADC inflige más daño que Mid?
4. ¿Los asesinatos por equipo siguen una binomial negativa?
5. ¿La clase de campeón está asociada con ganar o perder?
6. ¿El daño por minuto difiere entre EUW y KR?
7. ¿Cómo evoluciona la duración media de las partidas en el tiempo?

## Resultados principales

| Bloque | Contraste | Decisión (α = 5 %) | Lectura breve |
| --- | --- | --- | --- |
| **1. Paramétrico** | Oro al min. 15 (ganador vs perdedor), t pareada | Se rechaza H₀ | El ganador lleva ~437 oro más por jugador (IC 95 % [420, 454]; *d* = 0,69). Aun así, en ~1 de cada 4 partidas remonta el equipo con menos oro. |
| **1. Paramétrico** | Proporción de victorias del lado azul | No se rechaza H₀ | p̂ = 0,513; p = 0,12; IC 95 % [0,491, 0,535]. Incluye 0,5: no hay evidencia de ventaja azul. |
| **1. Paramétrico** | Daño ADC vs Mid (t de Welch) | No se rechaza H₀ | Diferencia 269; p = 0,38; IC 95 % [−329, 866]. Efecto práctico nulo. |
| **2. No paramétrico** | Bondad de ajuste de *kills* (binomial negativa) | Se rechaza H₀ | χ²(57) = 287,85; p ≈ 0. Hay más partidas con muy pocos asesinatos de las que predice el modelo. |
| **2. No paramétrico** | Clase de campeón vs resultado (χ²) | No se rechaza H₀ | p = 0,16; V de Cramér ≈ 0. La clase, por sí sola, no se asocia con ganar. |
| **2. No paramétrico** | Daño/min EUW vs KR (Mann–Whitney) | Se rechaza H₀ | EUW > KR, pero el efecto es pequeño (*d* ≈ 0,19). |
| **3. Series** | Duración media quincenal, ARIMA(2,0,3) | Serie estacionaria | ADF p ≈ 4·10⁻⁶. La duración se mantiene en torno a 29–30 minutos; Ljung–Box p = 0,65. |

Interpretación de conjunto: la creencia con más respaldo es la del **oro temprano**. La ventaja del lado azul y la influencia directa de la clase de campeón **no** aparecen con fuerza en esta muestra. Que un contraste sea significativo no implica un efecto grande (caso EUW vs KR).

## Estructura del repositorio

```text
main.pdf                 Memoria compilada (leer primero)
main.qmd                 Fuente del análisis y la redacción
main.html                HTML generado; las figuras viven en main_files/ (no está en GitHub)
data/
  matches_player_level.csv   20.000 filas (jugador-partida)
  matches_team_level.csv     4.000 filas (equipo-partida)
  dataset_spec.md            Esquema de variables
  data_quality_report.json   Validación de calidad
  seed_puuids.json           Semillas de muestreo
scripts/                 Pipeline de extracción y procesado
requirements.txt         Dependencias Python
.env.example             Plantilla de la clave de Riot (no hace falta para leer resultados)
```

Muestra analítica: 2.000 partidas Solo/Duo (`queue_id = 420`), 10.000 filas por región. Todas las comprobaciones de calidad en `data/data_quality_report.json` están en verde.

Los JSON crudos de Riot (`data/raw/`, ~2,5 GB) no están en el repositorio. Los CSV de `data/` bastan para repetir los contrastes.

## Repetir el análisis (sin volver a descargar partidas)

Con Python 3.11+ y [Quarto](https://quarto.org/):

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
quarto render main.qmd
```

Eso regenera `main.pdf` y `main.html` a partir de los CSV ya incluidos.

## Reconstruir el dataset desde la API (opcional)

Solo si se quiere repetir la extracción. Hace falta una clave en [developer.riotgames.com](https://developer.riotgames.com/):

```bash
copy .env.example .env
# Editar .env y pegar RIOT_API_KEY=...
python scripts/build_seed_puuids.py
python scripts/extract_data.py --target-matches 2000
python scripts/process_data.py --no-fetch-tier
python scripts/validate_dataset.py
```

La clave **no** debe subirse al repositorio.

## Limitaciones

La muestra depende de jugadores semilla, no es un sorteo perfecto de toda SoloQ. Algunas observaciones no son independientes (un mismo jugador puede aparecer en varias partidas). Los efectos pueden cambiar con el parche. Los resultados describen asociaciones, no causalidad.
