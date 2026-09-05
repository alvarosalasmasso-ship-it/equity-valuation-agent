# Metodología financiera

Fuente de verdad: `Advanced DCF.xlsx` (modelo profesional de valoración de
Amazon, elaborado por un ex-banquero de M&A/ECM de JP Morgan). Este
documento mapea cada fórmula del Excel a la función Python que la
implementa en `engine/valuation.py`, y documenta las decisiones de
simplificación tomadas para generalizar el modelo a un ticker arbitrario.

## 1. Arquitectura del Excel de referencia

El workbook tiene 15 hojas divididas en tres bloques:

- **Datos históricos** (`IS`, `CFS`, `CapEx`, `Segments`): cuenta de
  resultados, cash flow y capex históricos de Amazon, feed de FactSet.
- **Operating Model**: motor de proyección. Convierte los históricos en
  10 años de proyección (Revenue, EBIT, D&A, CapEx, ΔNWC) por segmento de
  negocio, mediante supuestos de % crecimiento / % sobre ventas.
- **DCF** (`Consolidated`, `North America`, `International`, `AWS`,
  `WACC`, `Shares`, `Comps`): motor de valoración propiamente dicho. Es
  un **DCF por suma de partes**: cada segmento tiene su propio DCF
  completo (UFCF, descuento, valor terminal) y `Consolidated` sencillamente
  suma los tres.

## 2. Decisión de alcance: por qué el motor Python NO segmenta

El Excel calcula un DCF independiente para North America, International y
AWS porque Amazon reporta esos tres segmentos por separado y el analista
tenía datos suficientes para modelarlos de forma diferenciada (AWS con
mayor margen y múltiplo, NA maduro y de bajo crecimiento, etc.).

Para un agente que valora **cualquier ticker** a partir de Alpha
Vantage/yfinance, no disponemos de ese desglose por segmento de forma
sistemática. La decisión de diseño es: **replicar exactamente la misma
lógica de fórmulas, pero a nivel de empresa consolidada**, tratando a la
compañía como un único "segmento".

Esto no es una pérdida de rigor: se comprobó que, cuando el modelo real
aplica el mismo peso Gordon/múltiplo de salida y el mismo múltiplo
EV/EBITDA a los tres segmentos (que es el caso en este Excel), el
resultado consolidado con la fórmula "whole-company" reproduce el precio
objetivo real de forma **exacta** (ver `tests/test_valuation.py::test_full_dcf_blended_terminal_value_matches_excel_exactly`,
$216.41/acción, igual a `Consolidated!K47`). Es una consecuencia de que
el blend Gordon/múltiplo es lineal: `blend(Σ segmentos) = Σ blend(segmento)`
cuando peso y múltiplo son uniformes.

## 3. Mapeo fórmula Excel -> función Python

### WACC / CAPM (`WACC!*` -> `unlever_beta`, `relever_beta`, `cost_of_equity`, `cost_of_debt`, `wacc`)

```
Beta desapalancada de cada comparable:
  β_u = β_l / (1 + (1 - t) × (Deuda neta / Market Cap))          WACC!I28:I30

Beta desapalancada media del sector:
  β_u_avg = AVERAGE(comparables)                                  WACC!E32

Beta re-apalancada para la empresa objetivo:
  β_l = β_u_avg × (1 + (1 - t) × (Deuda neta empresa / Market Cap empresa))   WACC!E33

Cost of Equity (CAPM):
  Re = Rf + β_l × MRP                                             WACC!F10

Cost of Debt:
  Rd = Gasto financiero anualizado / Deuda total                  WACC!F17

WACC:
  WACC = %E × Re + %D × Rd × (1 - t)                               WACC!F22
```

Nota: el modelo real usa 3 comparables fijos (AAPL, MSFT, GOOGL). En el
agente generalizado, los comparables deben venir de un set de peers del
mismo sector que el ticker analizado (Fase 3, `engine/comps.py`).

### Acciones diluidas — Treasury Stock Method (`Shares!*` -> `treasury_stock_method`, `diluted_shares_outstanding`)

```
Por cada tramo de opciones "in the money" (strike < precio actual):
  Acciones dilutivas = SUM(tramos in the money)                   Shares!E30
  Recaudado = SUM(acciones × strike, tramos in the money)         Shares!E9
  Acciones recompradas = Recaudado / Precio actual                Shares!E10
  Opciones netas dilutivas = Acciones dilutivas - Acciones recompradas   Shares!E11

Acciones diluidas totales = Básicas + Opciones netas dilutivas
                             + Otros valores dilutivos (convertibles, RSUs)   Shares!E14
```

### Flujo de caja libre desapalancado (`Consolidated!F32` -> `unlevered_fcf`)

```
UFCF = EBIT × (1 - t) + D&A - CapEx - ΔNWC
```

Idéntico a la fórmula del blueprint original (sección 2). Confirmado
exacto contra el Excel real.

### Descuento — convención stub + mid-year (`Consolidated!F35:K36` -> `discount_periods`, `pv_of_cash_flows`)

El modelo NO descuenta con periodos enteros (1, 2, 3...) sino con
**convención de mitad de año** (asume que el caja se genera de forma
uniforme durante el año, no toda al cierre), y ajusta el primer año por
un **stub period** (fracción de año que queda entre la fecha de
valoración y el cierre del primer ejercicio fiscal proyectado):

```
periodo_1 = stub / 2
periodo_2 = stub + 0.5
periodo_n = periodo_(n-1) + 1                    para n > 2

PV(flujo_1) = (flujo_1 × stub) / (1 + WACC)^periodo_1
PV(flujo_n) = flujo_n / (1 + WACC)^periodo_n       para n > 1
```

### Valor terminal (`North America!E45:K53` -> `gordon_growth_terminal_value`, `exit_multiple_terminal_value`, `blended_terminal_value`)

El modelo real combina dos métodos:

```
TV Gordon Growth = FCFF_n × (1+g) / (WACC - g)

TV Múltiplo de salida = EBITDA_terminal × Múltiplo EV/EBITDA (de Comps)

TV combinado = peso × TV_Gordon + (1 - peso) × TV_Múltiplo      (peso = 80% en NA)
```

Por defecto (`gordon_weight=1.0`) el motor usa Gordon Growth puro, que es
la metodología descrita en el blueprint original y no requiere un set de
comparables. El modo combinado es una extensión opcional cuando sí hay
comparables disponibles (Fase 3).

### Enterprise Value -> Equity Value -> Precio objetivo

```
Enterprise Value = Σ PV(UFCF) + PV(Terminal Value)
Equity Value = Enterprise Value + Caja - Deuda total
Precio objetivo = Equity Value / Acciones diluidas
```

## 4. Validación

Todos los targets de `tests/test_valuation.py` son valores calculados por
el propio Excel (extraídos con `openpyxl`, `data_only=True`), no cifras
inventadas. Esto cumple la Fase 4 del blueprint: "validar el motor contra
un caso conocido a mano" — aquí el caso conocido es el propio modelo
profesional de referencia.

## 5. Pendiente de mapear (fases futuras)

- `Operating Model`: lógica de proyección de Revenue/EBIT/D&A/CapEx/NWC a
  partir de supuestos de % crecimiento y % sobre ventas (relevante para
  `engine/ratios.py` y para decidir qué supuestos pedirá el agente al
  generar sus propias proyecciones a partir de datos de Alpha Vantage).
- `Comps`: estructura de la tabla de comparables (múltiplos EV/EBITDA,
  EV/Sales, P/E) -> `engine/comps.py`.
