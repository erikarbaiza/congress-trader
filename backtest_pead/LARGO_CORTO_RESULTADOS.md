# Hipótesis preregistrada: PEAD largo/corto a 20 días (2026-11-15)

Código y especificación (escrita antes de simular): `ls_sim.py`. Una sola especificación, sin variantes.

**Especificación:** largo si la sorpresa/precio está en el quintil superior (percentil 80 de los 4 años previos) y la sorpresa es positiva; corto si está en el quintil inferior (percentil 20) y es negativa. Entrada en la apertura de R+1, salida a 20 días hábiles, 5 % del patrimonio por posición, máximo 10 largos y 10 cortos, sin stop. Costes: 10 pb ida y vuelta por pata más 0,5 % anual de préstamo sobre lo corto.

**Criterio:** 1) alpha neto > 0 con t > 2 en diseño (2005-2020); 2) solo si pasa, lo mismo en prueba (2021-2026), una sola vez. Aviso preregistrado: la idea nació de la fase 2, que ya había mirado 2016-2026 de forma descriptiva.

## Resultados (caso principal)

| | Diseño 2005-2020 | Prueba 2021-2026 |
|---|---|---|
| Alpha anual frente al SPY | **+3,54 % (t 2,0)** | **+5,36 % (t 1,2)** |
| Beta | −0,05 | +0,02 |
| CAGR / volatilidad | +2,8 % / 7,3 % | +5,2 % / 11,1 % |
| Sharpe | 0,42 | 0,50 |
| Máx. drawdown | −16,8 % | −7,7 % |
| Exposición bruta media | 61 % | 64 % |
| Operaciones | 2.427 | 924 |
| Retorno neto por operación, largos / cortos | +1,28 % / −0,45 % | +2,49 % / −0,98 % |
| SPY (CAGR) | +9,5 % | +14,2 % |

Por patas (alpha anual): diseño, larga +0,86 % (t 0,5) y corta +2,67 % (t 1,4); prueba, larga +5,41 % (t 1,3) y corta −0,04 % (t 0,0).

Sensibilidad (largo+corto, alpha anual %): diseño de +4,0 (5 pb, préstamo 0,25 %) a +1,6 (30 pb, préstamo 2 %); prueba de +5,8 a +3,3. Siempre positivo, con t entre 0,8 y 2,3.

## Veredicto según el criterio preregistrado

- Criterio 1 (diseño): **cumple**, por poco (t ≈ 2,0).
- Criterio 2 (prueba): **no cumple** (t 1,2 < 2). La hipótesis **no queda confirmada**.

## Lectura

- El signo es positivo en ambos periodos, con beta ~0 y drawdown bajo. Es lo único de todo el análisis con ese patrón.
- Pero no es estadísticamente distinguible de cero en la ventana de prueba (5,9 años), y **la pata que aporta cambia entre periodos** (corta en diseño, larga en prueba), lo que no es lo esperable si el efecto fuera estable.
- La idea nació de mirar datos 2016-2026, así que la prueba no es completamente independiente. La validación limpia solo puede ser hacia delante.
- No se prueban variantes para "arreglarlo": cada ajuste posterior sería ajustar sobre datos ya vistos.

## Cautelas prácticas

- Sesgo de supervivencia: faltan empresas desaparecidas, que serían las mejores cortas (favorece a las largas y perjudica a las cortas).
- Préstamo: 0,5 % anual es un supuesto para valores líquidos del S&P 500; tras un mal resultado, el préstamo puede ser más caro o no estar disponible.
- Sin interés sobre el efectivo ni la garantía; sin restricciones de margen; datos de consenso no necesariamente point-in-time.
