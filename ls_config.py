"""
Configuracion del modulo largo/corto (hipotesis preregistrada, especificacion congelada).
Detalle y criterio de evaluacion: backtest_pead/ls_sim.py y backtest_pead/LARGO_CORTO_RESULTADOS.md.

NO modificar estos valores durante la prueba hacia delante: cambiarlos invalida la validacion.
Siempre paper trading en una cuenta Alpaca APARTE de la del bot PEAD (credenciales ALPACA_LS_KEY / ALPACA_LS_SECRET).
"""
import os

from dotenv import load_dotenv

load_dotenv()

LS_KEY    = os.getenv("ALPACA_LS_KEY", "")
LS_SECRET = os.getenv("ALPACA_LS_SECRET", "")

LS_HOLD_DAYS      = 20      # dias habiles desde la entrada hasta la salida
LS_POSITION_PCT   = 0.05    # 5 % del patrimonio por posicion
LS_MAX_PER_SIDE   = 10      # max 10 largos y 10 cortos (bruto <= 100 %)
LS_MAX_ENTRY_LAG  = 1       # dias habiles de margen tras el dia de entrada teorico (por si el bot corre tarde)
LS_LOOKBACK_DAYS  = 8       # dias naturales hacia atras para buscar anuncios de resultados
