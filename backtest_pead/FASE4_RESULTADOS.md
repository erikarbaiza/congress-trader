# Fase 4 — robustez de la regla actual del bot (V0) (2026-11-15)

Solo V0. V1-V5 siguen sin evaluarse en 2021-2026. Informe completo: `cache/phase4_report.txt`; codigo: `robustness.py`.

## A) Barrido de costes (alpha anual vs SPY, t entre parentesis)

| Coste ida y vuelta | Diseno 2005-2020 | Prueba 2021-2026 |
|---|---|---|
| 0 pb (bruto) | +1,18 % (0,4) | −2,77 % (−0,6) |
| 5 pb | +0,20 % (0,1) | −3,89 % (−0,8) |
| 10 pb | −0,77 % (−0,3) | −5,02 % (−1,0) |
| 20 pb | −2,71 % (−0,9) | −7,27 % (−1,5) |
| 30 pb | −4,66 % (−1,6) | −9,52 % (−1,9) |

SPY: CAGR +9,5 % (diseno) y +14,2 % (prueba). **Incluso sin costes el alpha no es significativo; el punto de equilibrio en diseno esta en ~5-6 pb y en prueba nunca se alcanza.**

## B) Por anio (10 pb) y con la beta cubierta

- Exposicion media 46-65 %; operaciones por anio 151-218; neto por operacion de −0,78 % (2007) a +1,13 % (2013).
- La cartera amortigua las caidas (2008: −16 % frente a −37 %; 2022: +7 % frente a −18 %) pero se queda muy atras en los anios alcistas (2023: −4 % frente a +26 %; 2024: −10 % frente a +25 %).
- Retorno anual con beta cubierta a 10 pb: **−0,77 % (Sharpe −0,06) en diseno y −5,02 % (Sharpe −0,41) en prueba.**
- La cobertura de datos pasa del 51 % (2005) al 99 % (2025-2026). **La prueba 2021-2026 tiene una cobertura del 91-99 %, asi que su resultado negativo apenas se ve afectado por el sesgo de supervivencia.**

## C) Placebo (20 semillas, sin costes)

Mismas fechas de entrada, mismo numero de operaciones y mismos tamanos que V0; la accion se sustituye por otra al azar de las que anunciaron ese dia.

| Periodo | V0 (alpha bruto) | Placebo (media ± desv.) | V0 supera a |
|---|---|---|---|
| Diseno 2005-2020 | +1,18 % | +0,11 % ± 1,77 | 70 % de los placebos |
| Prueba 2021-2026 | −2,77 % | +0,60 % ± 1,78 | **5 % de los placebos** |

- El placebo da un alpha cercano a 0 en diseno (+0,11 %): el simulador no inventa perdidas ni ganancias.
- En 2021-2026 las acciones que superan el 5 % rinden **peor que acciones al azar del mismo dia** (+0,33 % frente a +0,45 % por operacion, bruto).

## Conclusion

La regla actual del bot no tiene capacidad de seleccion demostrable: en diseno es indistinguible del azar y en prueba es peor que el azar (percentil 5). Con costes realistas (>=5-10 pb) el alpha es negativo en ambos periodos.
