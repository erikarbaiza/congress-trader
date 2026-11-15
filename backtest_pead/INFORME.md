# Validación histórica del PEAD sobre el S&P 500 — informe (2026-11-15)

**Pregunta:** ¿la regla que opera el bot (comprar tras una sorpresa de BPA ≥ 5 %, mantener ~10 días, stop del 8 %) tiene edge neto de costes en el S&P 500?

**Respuesta corta:** no se detecta. El efecto existió hasta ~2015, pero su tamaño es del orden de los costes de operar y desde 2016 no se distingue de cero. En 2021-2026 la selección de la regla es peor que elegir al azar entre las empresas que anuncian el mismo día.

## Datos y método

| Elemento | Detalle |
|---|---|
| Anuncios | 34.599 anuncios de 639 empresas, 2004-2026 (yfinance: BPA real y estimado, hora de publicación) |
| Universo | Miembros del S&P 500 *en la fecha del anuncio* (dataset público de pertenencia por fechas) |
| Precios | yfinance ajustados (retorno total); Alpaca SIP para empresas ya no cotizadas (2016+) |
| Cobertura | 51 % (2005) → 72 % (2015) → 79 % (2016) → 94 % (2022) → 99 % (2025). El sesgo de supervivencia es real y está medido |
| Entrada | Apertura del día siguiente a la reacción del mercado (distingue anuncios antes de apertura y después de cierre) |
| Estadística | Retorno anormal frente al SPY; t con error estándar agrupado por mes de anuncio |
| Partición | Diseño 2005-2020, prueba 2021-2026 (fijada antes de simular). Criterio de elección preregistrado |
| Placebo | 20 semillas: mismas fechas, número y tamaños, acción sustituida al azar entre las que anunciaron ese día |

## Resultados

1. **Estudio de eventos** (`FASE2_RESULTADOS.md`): regla del bot +0,28 % a 10 días en 2005-2015 (t 3,1) y −0,01 % en 2016-2026 (t 0,0). Mejor quintil menos peor quintil: +0,74 % (t 3,5) en 2005-2015, +0,96 % a 20 días (t 3,7) en 2016-2026, sobre todo por el lado corto.
2. **Simulación de cartera** (`FASE3_RESULTADOS.md`): ninguna de las 6 variantes cumple el criterio (alpha neto con t > 2) en 2005-2020. En 2021-2026 la regla base da −9,5 % anual de alpha a 30 pb (t −1,9) y −5,0 % a 10 pb.
3. **Robustez** (`FASE4_RESULTADOS.md`): incluso sin costes el alpha no es significativo (+1,2 %, t 0,4 en diseño; −2,8 %, t −0,6 en prueba). Punto de equilibrio de costes ≈ 5-6 pb en diseño; nunca en prueba. Placebo: la regla supera al 70 % de los placebos en diseño y al **5 %** en prueba.

## Limitaciones

- Sesgo de supervivencia (favorece a la estrategia; aun así sale negativa). En 2021-2026 la cobertura es del 91-99 %.
- No se modelan el tope sectorial, el tope de small caps ni el filtro de precio/volumen del bot real (sin datos históricos).
- Consenso de BPA de Yahoo no necesariamente point-in-time; precios ajustados por dividendos.
- Retorno anormal frente al SPY (ponderado por capitalización): la acción media rinde menos que el SPY en 2016-2026; por eso los grupos se comparan contra "todos los anuncios".
- La ventana 2021-2026 se miró de forma descriptiva en el estudio de eventos antes de la simulación; para la simulación solo se evaluó la regla base. V1-V5 no se han evaluado en ella.
- Efectivo sin rendimiento en la simulación.

## Qué queda por probar (solo con hipótesis preregistradas)

| Hipótesis | Por qué | Riesgo |
|---|---|---|
| Largo/corto sobre quintiles de sorpresa a 20-40 días | La diferencia Q5−Q1 es la parte más robusta de la fase 2 (t 3,5-3,7, bruto) | Coste de préstamo y de operar el lado corto; capacidad |
| Largo con horizonte 20-40 días y beta cubierta | Menos rotación (neto por operación +0,5 %/+0,6 % frente a +0,07 %) | En diseño no fue significativo |
| Efectivo en letras del Tesoro | El efectivo ocioso rinde 0 en la simulación | Mejora el retorno absoluto, no el alpha |

Regla: una sola prueba por hipótesis, en la ventana 2021-2026 aún sellada para V1-V5, y descartar si falla.

### Resultado de la hipótesis largo/corto (`LARGO_CORTO_RESULTADOS.md`)

Preregistrada y probada una sola vez. Neutral al mercado (beta ≈ 0), alpha neto de +3,5 % anual en diseño (t 2,0) y +5,4 % en prueba (t 1,2), con 10 pb por pata y 0,5 % de préstamo. **No cumple el criterio de prueba (t > 2): no queda confirmada.** El signo es consistente, pero la pata que aporta cambia entre periodos y la hipótesis nació de datos ya vistos. Siguiente paso honesto: seguirla hacia delante en paper trading sin tocar la especificación.

## Reproducir

```bash
python backtest_pead/universe.py      # universo del S&P 500 por fechas
python backtest_pead/fetch.py all     # sorpresas y precios (reanudable, ~1 h)
python backtest_pead/coverage.py      # cobertura por año
python backtest_pead/events.py        # panel de eventos
python backtest_pead/event_study.py   # fase 2
python backtest_pead/portfolio_sim.py design
python backtest_pead/portfolio_sim.py test V0
python backtest_pead/robustness.py 20
```
