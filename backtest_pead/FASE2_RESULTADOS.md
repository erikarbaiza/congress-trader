# Fase 2 — estudio de eventos del PEAD (2026-11-15)

Panel: 34.599 anuncios de 639 empresas, miembros del S&P 500 en la fecha del anuncio (universo point-in-time).
Entrada en la apertura del dia siguiente a la reaccion (R+1), salida a h dias habiles; retorno anormal = accion − SPY.
t con error estandar agrupado por mes de anuncio. **Bruto de costes, sin ajuste por beta.** Tablas completas: `cache/phase2_report.txt`.

## Resultados (AR medio %, entre parentesis el t)

| Grupo | 2005-2015 (cota optimista) h=10d | 2016-2026 h=10d | 2016-2026 h=20d | 2022-2026 h=20d |
|---|---|---|---|---|
| Regla del bot (>=5 %, \|est\|>=0,10 $) | **+0,28 (3,1)** | −0,01 (0,0) | +0,18 (0,8) | −0,06 (−0,2) |
| Regla del bot − todos los anuncios | +0,16 (2,5) | −0,01 (−0,3) | +0,10 (1,2) | +0,11 (0,9) |
| Q5 (mejor quintil de sorpresa) | +0,48 (3,0) | +0,27 (1,2) | **+0,78 (2,1)** | +0,56 (1,3) |
| Q5 − Q1 | **+0,74 (3,5)** | +0,30 (1,7) | **+0,96 (3,7)** | +1,11 (2,7) |

## Lectura

1. **El PEAD existio en el S&P 500 hasta ~2015** (con la cautela del sesgo de supervivencia): la regla del bot da +0,28 % a 10 dias (t 3,1) y el spread Q5−Q1 es +0,74 % (t 3,5).
2. **En 2016-2026 la regla actual del bot (umbral 5 %, salida a ~7-10 dias) no rinde nada** frente al SPY ni frente a "todos los anuncios". Encaja con tu prueba de humo y con la literatura (Martineau 2022).
3. **Queda algo, pero mas lento y mas concentrado:** el spread Q5−Q1 sube a +0,96 % a 20 dias habiles (t 3,7) y +1,34 % a 40 (t 3,5), y el mejor quintil gana +0,78 % a 20 dias (t 2,1). Gran parte del spread es el lado corto (Q1 rinde −0,81 % a 40d), que el bot no opera.
4. **El umbral del 5 % es demasiado laxo:** deja pasar el 47 % de los anuncios, y el efecto solo aparece en el quintil superior.
5. **Por anio la regla es erratica:** de +0,93 % (2022) a −0,84 % (2023) y −0,57 % (2024) a 7 dias.
6. **Sin ayuda de los filtros probados:** entrar un dia mas tarde (R+2) da +0,10 (t 0,7), no significativo; exigir reaccion positiva el dia del anuncio no mejora (−0,03 frente a +0,03).

## Cautelas

- Retorno anormal frente al SPY (ponderado por capitalizacion): "todos los anuncios" sale −0,39 % a 40d en 2016-2026 porque la accion media (igual ponderada) rindio menos que el SPY. Por eso se comparan los grupos contra "todos" y no solo contra el SPY.
- Sesgo de supervivencia: cobertura del 79 % (2016) al 99 % (2025); antes de 2016 solo del 51 % al 72 %. Los resultados 2005-2015 son una cota optimista.
- Sin costes ni ajuste por beta. Consenso de Yahoo no necesariamente point-in-time.
- **La ventana "sellada" 2016-2026 ya se ha mirado de forma descriptiva.** Para las variantes de la fase 3 hay que elegir reglas con 2005-2015 y 2016-2020, y probarlas solo en 2021-2026 (y despues en el registro en vivo del bot).
