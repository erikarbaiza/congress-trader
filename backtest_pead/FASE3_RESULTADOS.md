# Fase 3 — simulacion de cartera (2026-11-15)

Motor: `portfolio_sim.py`. Reglas del bot (12 posiciones, tamano por sorpresa 5-20 %, sin apalancamiento, stop 8 %), entrada en la
apertura de R+1, salida por tiempo al cierre, efectivo sin rendimiento, costes round-trip 10/30/100 pb (principal: 30 pb).
**No modelado:** tope sectorial, tope de small caps, filtro de precio/volumen (sin datos historicos). Sesgo de supervivencia residual
(cobertura 51-99 %, mejor cuanto mas reciente). Precios ajustados por dividendos (retorno total).

Particion fijada antes: DISENO 2005-2020, PRUEBA 2021-2026. Criterio de eleccion preregistrado: mayor alpha anual neto a 30 pb con
t > 2 y drawdown no mas de 5 pp peor que la base. Si ninguna lo cumple, en PRUEBA solo se evalua la base.

## Diseno 2005-2020 (SPY: CAGR +9,5 %, max DD −55,2 %)

| Variante | CAGR | Max DD | Beta | Exposicion | Alpha anual a 30 pb (t) | a 10 pb | Neto por operacion |
|---|---|---|---|---|---|---|---|
| V0 base (regla del bot) | −0,1 % | −56,8 % | 0,53 | 53 % | −4,66 % (−1,6) | −0,77 % | +0,07 % |
| V1 tamano igual | −0,1 % | −51,5 % | 0,47 | 49 % | −4,38 % (−1,8) | −0,76 % | +0,04 % |
| V2 H=20 dias | +2,6 % | −51,5 % | 0,76 | 80 % | −3,91 % (−1,4) | −1,70 % | +0,50 % |
| V3 quintil alto | +2,0 % | −40,8 % | 0,47 | 41 % | −1,86 % (−0,6) | +1,20 % | +0,09 % |
| V4 quintil alto + H=20 | +2,9 % | −59,5 % | 0,67 | 64 % | −2,70 % (−0,8) | −0,90 % | +0,61 % |
| V5 sin stop | +2,6 % | −44,7 % | 0,59 | 53 % | −2,42 % (−0,8) | +1,40 % | +0,18 % |

**Variante elegida por el criterio: ninguna.** Ninguna tiene t > 2.

## Prueba 2021-2026 (solo V0; SPY: CAGR +14,2 %, max DD −24,5 %)

| Variante | CAGR | Max DD | Beta | Alpha anual a 30 pb (t) | a 10 pb | a 100 pb | Operaciones | Neto/op |
|---|---|---|---|---|---|---|---|---|
| V0 base | −2,4 % | −37,0 % | 0,56 | **−9,52 % (−1,9)** | −5,02 % | −25,08 % | 1.208 | +0,02 % |

## Lectura

1. **La estrategia, tal como esta, no supera los costes.** El edge bruto por operacion (~+0,2-0,4 %) es del mismo orden que el coste (30 pb de ida y vuelta) y desaparece.
2. **La rotacion es el problema:** ~20 vueltas de cartera al anio con posiciones de 7 dias suponen ~6 %/anio de costes a 30 pb y ~2 %/anio a 10 pb.
3. **En 2021-2026 el alpha es negativo** incluso a 10 pb (−5,0 %), coherente con el estudio de eventos (retorno anormal ~0 o ligeramente negativo en esos anios).
4. **Las variantes de 20 dias son las mas sensatas** (neto por operacion de +0,50 % y +0,61 % frente a +0,07 %), por menor rotacion, pero tampoco dan alpha significativo en diseno.
5. **Drawdowns muy altos** (−40 % a −60 %) por ser solo largo, con beta ~0,5 y con 2008 y 2022 dentro.
6. **Cautelas:** costes de 30 pb pueden ser pesimistas para acciones del S&P 500 (el escenario de 10 pb es mas realista para liquidez alta) y el efectivo no rinde. Aun asi, ninguna combinacion de coste da un alpha con t > 2. El bot real tiene filtros que el simulador no (sector, small caps, precio/volumen).
7. **La ventana de PRUEBA sigue sin usar para V1-V5.** Se conserva para hipotesis nuevas preregistradas.
