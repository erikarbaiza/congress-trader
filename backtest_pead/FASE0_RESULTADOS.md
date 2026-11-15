# Fase 0 — viabilidad de datos (2026-11-15)

Muestra: 30 empresas actuales del S&P 500 + 12 antiguos miembros. Script: `phase0_spike.py`.
**La prueba de humo del apartado 5 no es un resultado**: 30 supervivientes, sin costes, sin ajuste por beta, eventos agrupados por trimestre.

## Hallazgos

| Tema | Resultado | Consecuencia |
|---|---|---|
| Sorpresas de BPA (yfinance) | 30/30 empresas, ~99 eventos cada una desde 2002 (ZTS desde 2013, NVDA 71) | Datos suficientes para empresas supervivientes |
| Estimados ajustados por splits | |est| < 0,10 $ en el 69 % de NVDA, 32 % AMD, 25 % AAPL, 23 % SBUX, 21 % ISRG (5,6 % del total) | No usar el filtro de 0,10 $ en histórico; medir la sorpresa como (real − estimado) / precio |
| Hora de publicacion | ~70 % entre 6h y 8h (antes de apertura), ~28 % a las 16h (despues del cierre) | Se puede distinguir antes/despues de mercado; entrada exacta en T+1 |
| Antiguos miembros del indice | 0/12 con sorpresas y 0/12 con precios en yfinance | Sesgo de supervivencia confirmado; resultados = cota optimista |
| Precios de delistadas | Alpaca SIP (diario) desde 2016-01-04 para SIVB, TWTR, CERN... | Precios sin sesgo desde 2016; faltan sorpresas de esas empresas |
| Tabla de cambios del S&P 500 (Wikipedia) | La pagina ya no trae la tabla (solo 2 tablas, una de navegacion) | Buscar otra fuente de constituyentes historicos (pendiente de verificar) |

## Prueba de humo (no concluyente)

- Regla actual del bot (sorpresa >= 5 % y |est| >= 0,10 $), entrada en la apertura de T+2, salida a 10 dias naturales: n = 1.314, retorno anormal medio vs SPY **+0,16 %** (error estandar 0,11 pp, infravalorado por agrupacion), acierto 51 %.
- Resto de eventos: +0,23 %. Por quintiles de sorpresa: +0,19, +0,34, +0,12, +0,05, +0,32 % (sin gradiente).
- Lectura: en estas 30 empresas, incluso con sesgo de supervivencia favorable, no se ve drift. Coherente con la literatura que lo da por desaparecido en large caps desde ~2006, pero falta ampliar muestra y separar subperiodos.

## Decision

Go para la fase 1 con estos cambios: sorpresa escalada por precio, entrada segun antes/despues de mercado, precios de delistadas con Alpaca SIP (ventana 2016-2026), analisis separado pre/post 2006 y ventana reciente 2023-2026 como la menos sesgada.
