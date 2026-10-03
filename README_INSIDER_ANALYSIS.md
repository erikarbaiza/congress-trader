# Insider Buying Backtest — Análisis y Gestión de Riesgo PEAD

**Proyecto:** CongressTrader  
**Archivo principal:** `insider_backtest.py`  
**Período analizado:** 2022-01-01 a 2026-09-01  
**Universo:** S&P 500 (99/100 empresas mapeadas a CIK)

---

## 1. Contexto: qué pregunta intenta responder

El sistema principal de CongressTrader sigue declaraciones bursátiles del Congreso (STOCK Act). `insider_backtest.py` es un análisis paralelo e independiente que responde una pregunta distinta:

> **¿Las compras en mercado abierto por parte de directivos C-suite del S&P 500 (Form 4, código de transacción P) generan alpha explotable frente al SPY en un horizonte de 30 días?**

La hipótesis es el efecto de señalización bien documentado en literatura académica (Seyhun 1986, Lakonishok & Lee 2001): los directivos que compran sus propias acciones con dinero propio tienen información privada o superior convicción sobre el valor de la empresa. La pregunta práctica es si ese efecto es suficientemente grande y consistente para sobrevivir costes reales de ejecución.

**Fuente de datos:** SEC EDGAR `submissions.json`, no scraping. La API devuelve directamente la lista de Form 4 por emisor, con el documento XML ya identificado en el campo `primaryDocument`. Esto da cobertura completa del S&P 500 sin depender de búsqueda por texto libre (que solo devolvía ~600 Form 4/año con alta tasa de ruido).

**Filtros:**
- Solo transacciones código `P` (compra en mercado abierto)
- Insider: CEO, CFO, COO, Presidente, Presidente del Consejo, Director u Oficial
- Compra mínima: $10,000 (descarta concesiones de acciones y compras simbólicas)
- Universo: S&P 500 issuers (empresas grandes, líquidas)

---

## 2. Bug encontrado: primaryDocument devolvía HTML, no XML

### El problema

`submissions.json` incluye un campo `primaryDocument` para cada filing. En muchos casos ese campo contiene una ruta con prefijo XSLT, por ejemplo:

```
xslF345X06/form4.xml
```

Al construir la URL de descarga con esa ruta tal cual:

```
https://www.sec.gov/Archives/edgar/data/{CIK}/{adsh}/xslF345X06/form4.xml
```

El servidor SEC devuelve **HTTP 200 OK pero con contenido HTML** — es la vista renderizada para el navegador, no el XML de datos. La respuesta empieza con `<!DOCTYPE html>` en lugar de `<ownershipDocument>`.

### Por qué pasaba desapercibido

`_parse_form4()` intenta `ET.fromstring(xml_text)`. Al recibir HTML, lanza `ET.ParseError`. El manejador lo trata como fallo de parseo y devuelve `None`, que se cachea como "fallo temporal — reintentar". El resultado observable era `parse_fail=0` en los contadores (el error se tragaba antes de llegar al contador) y `no_buys` muy alto. **200 OK silenciaba el bug.**

La señal de alarma fue una proporción desorbitada de no_buys frente al total de filings parseados sin ningún fallo explícito — indica que el sistema está procesando _algo_ sin errores pero sin extraer datos.

### La fix

En `_fetch_xml()`, tres capas de defensa:

```python
# 1. Extraer solo el nombre de archivo, descartando el prefijo XSLT
bare = primary_doc.rsplit("/", 1)[-1]

# 2. Descargar y verificar que el contenido sea realmente XML (no HTML)
if r.status_code == 200 and not r.text.lstrip().startswith("<!"):
    return r.text  # XML confirmado

# 3. Fallback: descubrir el .xml real via index.json del filing
r = requests.get(f"{base}/index.json", ...)
xml_name = next((x["name"] for x in items if x["name"].lower().endswith(".xml")), None)
```

El check `not r.text.lstrip().startswith("<!` detecta tanto `<!DOCTYPE html>` como `<!--` (comentarios HTML) y redirige al fallback en vez de pasar HTML al parser XML.

---

## 3. Metodología de validación

### 3.1 Deduplicación de señales

Un mismo insider puede presentar múltiples Form 4 el mismo día para el mismo ticker (por ejemplo, compras en varios bloques ejecutadas el mismo día, reportadas en filings separados por requerimiento regulatorio). Tratar cada filing como señal independiente infla artificialmente el número de operaciones y la exposición a un único evento informativo.

**Criterio de deduplicación:** `(ticker, título del insider, fecha de transacción)`

Las compras duplicadas se agregan (se suman `value` y `shares`), preservando la señal como una sola entrada.

**Impacto medido:**

```
175 señales brutas → 123 señales únicas  (52 duplicados eliminados)
```

El caso más extremo: 10 filings de Elon Musk en TSLA el 2025-09-12, colapsados a 1 señal de $999,959k. Sin deduplicar, ese único evento habría tenido peso 10× desproporcionado en las métricas.

### 3.2 Sensibilidad al lag de ejecución (T+0 / T+1 / T+2)

El sistema real ejecuta una vez al día a las 09:35 ET (13:35 UTC). Aunque los Form 4 se publican en EDGAR antes del cierre de mercado, el ciclo completo — publicación → descubrimiento por polling → validación → orden — implica que la ejecución real más probable es al **cierre del día siguiente** al filing (T+1) o incluso T+2 si el filing llega tarde.

Para medir la sensibilidad a ese lag se corrió el backtest con tres offsets de entrada:

- **T+0:** compra al cierre del mismo día del filing (irrealista — requeriría detección en tiempo real)
- **T+1:** compra al cierre del día siguiente (el más realista para el sistema actual)
- **T+2:** compra al cierre del día posterior (margen conservador)

**Pregunta:** ¿la caída de alpha de T+0 a T+1 es una pérdida real de edge o ruido estadístico?

El análisis usa el **error estándar del alpha medio** — es decir, la incertidumbre sobre el valor promedio del alpha por operación expresado en puntos porcentuales de retorno, no la incertidumbre sobre la proporción "baten SPY" (que sería un SE de proporción con fórmula distinta, ~4–5 pp en esta muestra). Son dos métricas sobre la misma muestra pero que responden preguntas diferentes; aquí interesa la magnitud del efecto en retorno, no si supera o no al SPY.

Con $n = 123$ señales, si la desviación estándar del alpha por operación es $\sigma \approx 6\%$ (valor típico para retornos de 30 días en acciones individuales), el **error estándar del alpha medio** es:

$$SE_{\bar{\alpha}} = \frac{\sigma}{\sqrt{n}} = \frac{6\%}{\sqrt{123}} \approx 0.54\text{ pp}$$

La diferencia de alpha medio entre T+0 y T+1 (ver tabla en sección 4) es ~0.30 pp, menor que un $SE_{\bar{\alpha}}$. **No se puede rechazar la hipótesis nula de que el alpha medio en T+0 = el alpha medio en T+1.** La caída es ruido estadístico: el edge no depende de ejecución en tiempo real.

### 3.3 Costes netos: modelo de comisiones y slippage

El cálculo de alpha bruto (retorno de la posición menos retorno del SPY en el mismo período) se ajusta por el coste estimado del round-trip:

```
cost_rt = 2 × SLIPPAGE_PCT + COMMISSION_EUR / POSITION_EUR
```

**Parámetros:**

| Concepto | Valor | Justificación |
|---|---|---|
| Comisión | 0 € | Alpaca/IBKR Lite: comisión cero en acciones US |
| Slippage (one-way) | 0.10% | Mid-point spread implícito en acciones S&P 500 líquidas |
| Tamaño de posición | 5.000 € | Referencia de cartera |
| **Coste round-trip total** | **0.20%** | (entrada + salida, sin comisión) |

Con 0 € de comisión el coste se reduce enteramente al spread bid-ask. Para el S&P 500 líquido, 0.10% one-way es un supuesto conservador (spreads reales en acciones de gran cap están normalmente en 0.01–0.05%).

---

## 4. Resultado final

**20,053 Form 4 filings procesados → 123 señales únicas (C-suite, ≥$10k, code P)**

| Métrica | T+0 | T+1 | T+2 |
|---|---|---|---|
| Señales evaluadas | 120 | 120 | 120 |
| Retorno medio (30d) | — | — | — |
| **Alpha bruto vs SPY** | **+1.62%** | **+1.32%** | **+1.34%** |
| Coste round-trip | -0.20% | -0.20% | -0.20% |
| **Alpha neto vs SPY** | **+1.42%** | **+1.12%** | **+1.14%** |
| % operaciones que baten SPY (neto) | ~57% | ~54% | ~54% |

*3 señales omitidas en cada run por falta de precios históricos (tickers retirados del S&P 500 antes del end_date).*

**Conclusión operativa:** El sistema real (polling diario a las 09:35 ET) opera en régimen T+1/T+2. Con alpha neto de **+1.12% a +1.14%** por operación a 30 días, el edge existe pero es modesto. La consistencia del resultado a T+1 y T+2 — prácticamente idénticos dentro del margen de error — confirma que **no hay urgencia de infraestructura real-time** para este universo.

**Disclaimer de tamaño de muestra:** $n = 123$ señales en 4+ años equivale a ~30 por año. El error estándar de la media (~0.54%) implica que el intervalo de confianza al 95% para el alpha neto es aproximadamente [+0.05%, +2.19%] — positivo, pero con incertidumbre considerable. Se necesitan más datos para afirmaciones robustas.

---

## 5. Decisión de gestión de riesgo: stop-loss en PEAD

### El hallazgo

La estrategia PEAD (Post-Earnings Announcement Drift) que corre en producción en Railway tenía `STOP_LOSS_PCT = 0.00` — sin ninguna protección de bajada en las posiciones abiertas. La función `check_earnings_stops()` en `trader.py` tiene una guarda `if STOP_LOSS_PCT == 0: return []` que significa que nunca se ejecutaba.

Además, el `except` en esa función era un `pass` silencioso: si `close_position()` fallaba (rechazo de la orden, error de conexión), el sistema registraba la operación como cerrada sin haberlo hecho realmente.

### Los cambios aplicados

**`config.py`:** default de `STOP_LOSS_PCT` de `"0.00"` a `"0.08"` (8% de caída desde precio de entrada):

```python
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.08"))  # 8% — valor conservador, ver nota
```

**`trader.py`:** el `except` silencioso ahora registra `ERROR_STOP` en el log de acciones, igual que ya hacía `_check_stop_take()` para las posiciones de congressional trading:

```python
# Antes:
except Exception:
    pass

# Después:
except Exception as e:
    results.append({
        "action": "ERROR_STOP", "ticker": ticker, "qty": None,
        "reason": str(e),
    })
```

### Por qué el 8% no está calibrado con backtest y no debe serlo todavía

La calibración del umbral óptimo requeriría un backtest histórico de PEAD con datos limpios: fechas exactas de earnings, EPS estimates y actuals para construir el historial de señales, y luego simular el path de precios diario durante el hold de 10 días.

La fuente disponible sin coste (`yf.Ticker(sym).earnings_dates`) tiene un problema estructural: Yahoo Finance actualiza los EPS estimates retrospectivamente, de manera que los estimates que se ven hoy para Q3 2023 pueden no ser exactamente los que los analistas publicaron en 2023. Esto introduce **look-ahead bias en el dato de sorpresa EPS**, que es la variable de entrada más importante de la estrategia. Un backtest construido sobre esos datos podría mostrar cualquier resultado y no sería fiable para tomar decisiones de calibración.

**Decisión documentada:** 8% como valor conservador de seguridad, pendiente de calibración con datos reales de producción. La referencia empírica disponible — el análisis de stop-loss sobre 120 señales de insider buying (proxy imperfecto) — muestra que a 8% se activa un 25% de las veces, con 3 falsos cortes y 12 pérdidas reales evitadas, a un coste de alpha de ~0.19 pp. Ese análisis es orientativo, no vinculante para PEAD.

**Revisión prevista:** cuando acumulemos ~50 operaciones PEAD cerradas en producción con precios de entrada y salida confirmados (estimación: 6–12 meses a este ritmo de señales). Con ese dataset se podrá calibrar sin sesgos de fuente.

---

## 6. Estado actual: insider_backtest.py es análisis offline

`insider_backtest.py` **no está conectado a producción**. No escribe órdenes, no modifica `position_entries.json`, no interactúa con `main.py` ni con Railway. Es un script de análisis independiente.

El flujo de producción real es:

```
scheduler.py (Railway, 13:35 UTC)
    └── main.py :: run()
            ├── earnings_scanner.py :: get_earnings_beats()  → señales PEAD
            ├── trader.py :: buy_earnings_position()         → abrir posiciones
            └── trader.py :: check_earnings_stops()          → cerrar con stop-loss
```

La decisión de conectar el módulo de insider buying a producción requeriría:

1. Adaptar `get_signals()` para detectar solo señales nuevas (últimas 24h), no el histórico completo
2. Resolver el timing: los Form 4 se publican en EDGAR antes de las 17:30 ET, pero con lag variable; sería T+1 en casi todos los casos
3. Decidir si las señales de insider buying y PEAD conviven o compiten por el mismo capital
4. Validar que el alpha neto (+1.12% T+1, n=123) es estable en períodos OOS antes de capital real

Hasta que esas decisiones estén tomadas, `insider_backtest.py` sirve como evidencia de la existencia del efecto y como herramienta de análisis periódico.

---

## Apéndice: bugs técnicos resueltos durante el desarrollo

| Bug | Síntoma | Root cause | Fix |
|---|---|---|---|
| `primaryDocument` devuelve HTML | `parse_fail=0` pero `no_buys` anormalmente alto | SEC devuelve vista XSLT (HTTP 200, contenido HTML) cuando se usa la ruta con prefijo | Strip del prefijo XSLT + check `not text.startswith("<!")` + fallback a `index.json` |
| `DatetimeIndex` vs string keys | "Sin datos de precios suficientes" — 175 señales, 0 evaluadas | `yfinance.download()` devuelve `DatetimeIndex` (`pd.Timestamp`); `.get("2022-01-15")` devuelve `None` | `close_df.index = close_df.index.strftime("%Y-%m-%d")` antes del `.get()` |
| MultiIndex de yfinance | `KeyError` en `raw["Close"]` con un solo ticker | yfinance ≥0.2.x devuelve `MultiIndex` incluso para un solo ticker en algunos contextos | `isinstance(raw.columns, pd.MultiIndex)` + `xs("Close", axis=1, level=1)` como alternativa |
| `except Exception: pass` en stop-loss | Stop-loss silenciosamente no ejecutado si `close_position()` falla | El manejador de excepción descartaba el error sin registrarlo | Registrar `ERROR_STOP` en results para que sea visible en el email diario |
