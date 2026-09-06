"""Motor de valoración DCF determinista.

Traduce a Python exacto las fórmulas de "Advanced DCF.xlsx" (modelo de
referencia elaborado por un ex-banquero de JP Morgan). No es un DCF
genérico: cada función corresponde a una celda concreta del modelo.
Ver docs/METHODOLOGY.md para la trazabilidad celda -> función.

Principio de diseño: este módulo no llama a ningún LLM ni contiene
lógica de IA. Recibe únicamente números y devuelve únicamente números.
"""

import warnings
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# WACC / CAPM  (hoja "WACC")
# ---------------------------------------------------------------------------

def unlever_beta(levered_beta: float, tax_rate: float, net_debt: float, market_cap: float) -> float:
    """Beta desapalancada de un comparable. WACC!I28 = D28/(1+(1-G28)*(F28/E28))"""
    return levered_beta / (1 + (1 - tax_rate) * (net_debt / market_cap))


def relever_beta(unlevered_beta: float, tax_rate: float, net_debt: float, market_cap: float) -> float:
    """Beta re-apalancada para la empresa objetivo. WACC!E33"""
    return unlevered_beta * (1 + (1 - tax_rate) * (net_debt / market_cap))


def cost_of_equity(risk_free_rate: float, beta: float, market_risk_premium: float) -> float:
    """CAPM. WACC!F10 = Rf + beta * MRP"""
    return risk_free_rate + beta * market_risk_premium


def cost_of_debt(interest_expense: float, total_debt: float) -> float:
    """WACC!F17 = gasto financiero anualizado / deuda total.

    Auditoría (sesión 17): una empresa sin deuda (total_debt<=0) hacía
    `crashear` esta función con ZeroDivisionError -- alcanzable en modo
    "universo cacheado" (`build_peer_wacc` en app/streamlit_app.py, sin
    guarda) aunque no en el universo piloto actual (los 8 tickers tienen
    deuda). Devolver 0.0 es seguro: en `wacc()`, el peso de la deuda
    (`total_debt / (total_debt + market_cap)`) también es 0 en ese caso,
    así que este valor nunca influye en el resultado -- no es un ajuste
    silencioso de nada que importe, solo evita el crash."""
    if total_debt <= 0:
        return 0.0
    return interest_expense / total_debt


def wacc(market_cap: float, total_debt: float, cost_of_equity_: float,
         cost_of_debt_: float, tax_rate: float) -> float:
    """WACC!F22 = %E * Re + %D * Rd * (1 - t)"""
    total = market_cap + total_debt
    weight_equity = market_cap / total
    weight_debt = total_debt / total
    return weight_equity * cost_of_equity_ + weight_debt * cost_of_debt_ * (1 - tax_rate)


# ---------------------------------------------------------------------------
# Acciones diluidas — Treasury Stock Method (hoja "Shares")
# ---------------------------------------------------------------------------

@dataclass
class OptionTranche:
    shares_outstanding: float
    exercise_price: float


def treasury_stock_method(current_price: float, tranches: Sequence[OptionTranche]) -> tuple[float, float]:
    """Réplica Shares!E8:E11.

    Devuelve (opciones netas dilutivas, acciones recompradas con lo recaudado).
    Solo las tramos "in the money" (exercise_price < current_price) diluyen.
    """
    dilutive = [t for t in tranches if t.exercise_price < current_price]
    total_dilutive_shares = sum(t.shares_outstanding for t in dilutive)
    proceeds = sum(t.shares_outstanding * t.exercise_price for t in dilutive)
    shares_repurchased = proceeds / current_price if current_price else 0.0
    net_dilutive_options = total_dilutive_shares - shares_repurchased
    return net_dilutive_options, shares_repurchased


def diluted_shares_outstanding(basic_shares: float, net_dilutive_options: float,
                                other_dilutive_securities: float = 0.0) -> float:
    """Shares!E14 = acciones básicas + opciones netas dilutivas + otros valores dilutivos"""
    return basic_shares + net_dilutive_options + other_dilutive_securities


# ---------------------------------------------------------------------------
# Flujo de caja libre desapalancado (UFCF)  (hojas de segmento / Consolidated)
# ---------------------------------------------------------------------------

def unlevered_fcf(ebit: float, tax_rate: float, d_and_a: float, capex: float,
                   change_in_nwc: float) -> float:
    """UFCF = EBIT*(1-t) + D&A - CapEx - Delta NWC   (Consolidated!F32)"""
    ebiat = ebit * (1 - tax_rate)
    return ebiat + d_and_a - capex - change_in_nwc


# ---------------------------------------------------------------------------
# Descuento — convención stub + mid-year  (Consolidated!F35:K36)
# ---------------------------------------------------------------------------

def _safe_date(year: int, month: int, day: int) -> date:
    """Construye date(year, month, day), cayendo al día 28 si el día no
    existe en ese mes/año (29 de febrero en año no bisiesto)."""
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def compute_stub_fraction(fiscal_year_end_month: int, fiscal_year_end_day: int,
                           valuation_date: date) -> float:
    """Fracción del primer ejercicio fiscal proyectado que queda entre
    `valuation_date` y el próximo cierre de ejercicio (mes/día) —
    exactamente lo que `Consolidated!F35` calcula en el Excel de
    referencia con `YEARFRAC` (ahí, convención 30/360; aquí, días de
    calendario reales — ambas dan una fracción de año equivalente en la
    práctica, y esta no requiere reimplementar 30/360).

    Auditoría (sesión 15, hallazgo C1): este cálculo nunca se hacía en
    el pipeline real — todo `DCFInputs` se construía con el
    `stub_fraction` por defecto (1.0), asumiendo implícitamente que la
    fecha de valoración es siempre el 1 de enero del primer año
    proyectado. Esta función cierra ese hueco.

    Si `valuation_date` cae exactamente en el cierre de ejercicio, se
    interpreta como "ese ejercicio ya cerró" y se devuelve la fracción
    del ejercicio SIGUIENTE (1.0) — nunca 0.0, que violaría la
    restricción de `discount_periods()`.
    """
    candidate = _safe_date(valuation_date.year, fiscal_year_end_month, fiscal_year_end_day)
    if candidate <= valuation_date:
        candidate = _safe_date(valuation_date.year + 1, fiscal_year_end_month, fiscal_year_end_day)
    previous = _safe_date(candidate.year - 1, fiscal_year_end_month, fiscal_year_end_day)

    days_remaining = (candidate - valuation_date).days
    days_in_period = (candidate - previous).days
    return days_remaining / days_in_period


def discount_periods(n_years: int, stub_fraction: float = 1.0,
                      mid_year_convention: bool = True) -> list[float]:
    """Periodos de descuento (en años) para cada flujo explícito.

    stub_fraction: fracción del primer año fiscal que queda por transcurrir
    desde la fecha de valoración (1.0 si se valora a inicio de año).

    Con mid-year convention (estándar en banca de inversión, asume que el
    caja se genera de forma uniforme a lo largo del año en vez de al cierre):
      periodo_1 = stub / 2
      periodo_2 = stub + 0.5
      periodo_n = periodo_(n-1) + 1   para n > 2
    """
    if not (0 < stub_fraction <= 1):
        raise ValueError("stub_fraction debe estar en (0, 1]")
    if n_years < 1:
        return []

    periods = []
    if mid_year_convention:
        periods.append(stub_fraction / 2)
        if n_years > 1:
            periods.append(stub_fraction + 0.5)
            for _ in range(n_years - 2):
                periods.append(periods[-1] + 1)
    else:
        periods.append(stub_fraction)
        for _ in range(n_years - 1):
            periods.append(periods[-1] + 1)
    return periods


def pv_of_cash_flows(cash_flows: Sequence[float], discount_rate: float,
                      periods: Sequence[float], stub_fraction: float = 1.0) -> list[float]:
    """Valor presente de cada flujo. El primer flujo se prorratea por
    stub_fraction porque del primer año fiscal solo quedan esos meses
    por transcurrir; el resto son flujos anuales completos.
    (Consolidated!F33 vs G33:K33)
    """
    pvs = []
    for i, (cf, t) in enumerate(zip(cash_flows, periods)):
        adj_cf = cf * stub_fraction if i == 0 else cf
        pvs.append(adj_cf / (1 + discount_rate) ** t)
    return pvs


# ---------------------------------------------------------------------------
# Valor terminal  (hoja de segmento, bloque "Terminal Value")
# ---------------------------------------------------------------------------

# Margen WACC-g por debajo del cual el valor terminal de Gordon Growth se
# vuelve muy sensible a pequeños cambios de cualquiera de los dos inputs
# (regla de pulgar de la industria, no una ley matemática exacta). Frecuente
# en compañías de beta bajo (WACC bajo, p.ej. consumo defensivo) combinado
# con una tasa de crecimiento terminal fija ligada a crecimiento macro
# genérico (~2-3%) — ver docs/METHODOLOGY.md sección 7 (caso PG/JNJ real).
MIN_PRUDENT_WACC_GROWTH_SPREAD = 0.03


def gordon_growth_terminal_value(final_year_fcf: float, wacc_: float,
                                  terminal_growth_rate: float) -> float:
    """TV = FCFF_n * (1+g) / (WACC - g)

    Emite un warning (no bloquea el cálculo) si WACC-g queda por debajo de
    MIN_PRUDENT_WACC_GROWTH_SPREAD: en ese régimen el resultado es muy
    inestable y probablemente sobrevalora frente al múltiplo de salida —
    considera bajar `terminal_growth_rate` o reducir `gordon_weight` en
    favor del múltiplo de salida.
    """
    if wacc_ <= terminal_growth_rate:
        raise ValueError("WACC debe ser mayor que la tasa de crecimiento terminal (g)")
    spread = wacc_ - terminal_growth_rate
    if spread < MIN_PRUDENT_WACC_GROWTH_SPREAD:
        warnings.warn(
            f"WACC-g = {spread:.2%} está por debajo del margen prudente habitual "
            f"({MIN_PRUDENT_WACC_GROWTH_SPREAD:.0%}). El valor terminal de Gordon "
            "Growth es muy sensible en este rango y tiende a sobrevalorar frente "
            "al múltiplo de salida. Revisa terminal_growth_rate o gordon_weight.",
            stacklevel=2,
        )
    return final_year_fcf * (1 + terminal_growth_rate) / spread


def exit_multiple_terminal_value(terminal_year_ebitda: float, ev_ebitda_multiple: float) -> float:
    """TV = EBITDA terminal * múltiplo EV/EBITDA de comparables"""
    return terminal_year_ebitda * ev_ebitda_multiple


def blended_terminal_value(gordon_tv: float, exit_multiple_tv: float,
                            gordon_weight: float = 1.0) -> float:
    """Combina ambos métodos. gordon_weight=1.0 -> Gordon Growth puro
    (metodología por defecto). El modelo de referencia pondera cada
    segmento de forma distinta (p.ej. 80% Gordon / 20% múltiplo para
    North America); aquí se aplica un único peso a nivel de empresa
    porque no segmentamos el negocio para tickers arbitrarios.
    """
    return gordon_weight * gordon_tv + (1 - gordon_weight) * exit_multiple_tv


# ---------------------------------------------------------------------------
# Pipeline completo
# ---------------------------------------------------------------------------

@dataclass
class DCFInputs:
    ebit: list[float]
    tax_rate: list[float]
    d_and_a: list[float]
    capex: list[float]
    change_in_nwc: list[float]
    wacc: float
    terminal_growth_rate: float
    stub_fraction: float = 1.0
    cash: float = 0.0
    total_debt: float = 0.0
    diluted_shares: float = 1.0
    terminal_ev_ebitda_multiple: Optional[float] = None
    gordon_weight: float = 1.0

    def __post_init__(self) -> None:
        lengths = {len(self.ebit), len(self.tax_rate), len(self.d_and_a),
                   len(self.capex), len(self.change_in_nwc)}
        if len(lengths) != 1:
            raise ValueError("Todas las series de proyección deben tener la misma longitud")
        if self.diluted_shares <= 0:
            raise ValueError("diluted_shares debe ser positivo")
        # Auditoría sesión 15, hallazgo M3: nunca ocurre viniendo del
        # pipeline real (wacc siempre viene de wacc_builder.build_wacc(),
        # gordon_weight de un slider acotado en la app), pero la clase en
        # sí no lo garantizaba si se construye de forma directa -- un WACC
        # <=0 o un peso fuera de [0,1] no es un escenario válido de DCF,
        # es un error de programación que debe fallar aquí, no producir
        # un precio implícito sin sentido más adelante.
        if self.wacc <= 0:
            raise ValueError("wacc debe ser positivo")
        if not (0.0 <= self.gordon_weight <= 1.0):
            raise ValueError("gordon_weight debe estar en [0, 1]")


@dataclass
class DCFResult:
    unlevered_fcf: list[float]
    pv_unlevered_fcf: list[float]
    discount_periods: list[float]
    gordon_terminal_value: float
    exit_multiple_terminal_value: Optional[float]
    terminal_value: float
    pv_terminal_value: float
    enterprise_value: float
    equity_value: float
    implied_share_price: float


def run_dcf(inputs: DCFInputs) -> DCFResult:
    ufcf = [
        unlevered_fcf(e, t, d, c, n)
        for e, t, d, c, n in zip(inputs.ebit, inputs.tax_rate, inputs.d_and_a,
                                  inputs.capex, inputs.change_in_nwc)
    ]
    periods = discount_periods(len(ufcf), inputs.stub_fraction)
    pv_ufcf = pv_of_cash_flows(ufcf, inputs.wacc, periods, inputs.stub_fraction)

    exit_tv = None
    if inputs.terminal_ev_ebitda_multiple is not None:
        terminal_ebitda = inputs.ebit[-1] + inputs.d_and_a[-1]
        exit_tv = exit_multiple_terminal_value(terminal_ebitda, inputs.terminal_ev_ebitda_multiple)

    # Auditoría sesión 15, hallazgo M4: solo calcular Gordon Growth cuando
    # su resultado puede llegar a contar para el valor terminal. Si hay un
    # múltiplo de salida disponible Y gordon_weight=0, el componente
    # Gordon es completamente irrelevante -- calcularlo igual (como se
    # hacía antes) no solo emitía un aviso sin sentido sobre un resultado
    # descartado, sino que podía bloquear con un ValueError un DCF válido
    # (wacc<=terminal_growth_rate) que el usuario decidió deliberadamente
    # esquivar bajando el peso a 0. Si no hay múltiplo de salida, Gordon
    # es la única fuente de valor terminal posible, así que sí hace falta
    # sin importar el peso configurado.
    needs_gordon = exit_tv is None or inputs.gordon_weight > 0.0
    gordon_tv = (
        gordon_growth_terminal_value(ufcf[-1], inputs.wacc, inputs.terminal_growth_rate)
        if needs_gordon else 0.0
    )

    if exit_tv is not None:
        terminal_value = blended_terminal_value(gordon_tv, exit_tv, inputs.gordon_weight)
    else:
        terminal_value = gordon_tv

    pv_terminal_value = terminal_value / (1 + inputs.wacc) ** periods[-1]
    enterprise_value = sum(pv_ufcf) + pv_terminal_value
    equity_value = enterprise_value + inputs.cash - inputs.total_debt
    implied_share_price = equity_value / inputs.diluted_shares

    return DCFResult(
        unlevered_fcf=ufcf,
        pv_unlevered_fcf=pv_ufcf,
        discount_periods=periods,
        gordon_terminal_value=gordon_tv,
        exit_multiple_terminal_value=exit_tv,
        terminal_value=terminal_value,
        pv_terminal_value=pv_terminal_value,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        implied_share_price=implied_share_price,
    )


# ---------------------------------------------------------------------------
# Reverse DCF: expectativas implícitas del mercado
# ---------------------------------------------------------------------------
#
# Un DCF hacia delante responde "¿qué precio justifican mis supuestos?".
# Un reverse DCF responde la pregunta complementaria, igual de estándar en
# equity research: "¿qué supuesto justificaría el precio que YA cotiza el
# mercado (o el consenso de analistas)?". No es una segunda metodología ni
# una forma nueva de calcular: usa exactamente el mismo run_dcf() ya
# validado contra el Excel, resuelto al revés para una única incógnita con
# todo lo demás fijo. El motivo de construir esto es que la brecha entre
# el precio del motor (reversión a la media) y el precio de mercado no es
# un error a esconder -- es la señal más informativa que puede dar un DCF,
# y hasta ahora la herramienta solo mostraba el tamaño de la brecha (%),
# no lo que el mercado necesita creer para justificarla.

def solve_for_target_price(evaluate_price, target_price: float, lower_bound: float,
                            upper_bound: float, price_tolerance: float = 0.01,
                            max_iterations: int = 100) -> float:
    """Bisección genérica: encuentra x en [lower_bound, upper_bound] tal que
    evaluate_price(x) ~= target_price, en dólares (price_tolerance=0.01 ->
    al céntimo, mismo estándar de precisión que el resto del motor).

    Bisección pura, sin dependencias externas (scipy, etc.) -- consistente
    con el principio de "Python puro" del blueprint. Válida porque
    evaluate_price (proyección con fade -> run_dcf, o Gordon Growth ->
    run_dcf) es continua y monótona creciente en el rango relevante: no
    hace falta un método de raíces más sofisticado para esto.

    No asume que la función es creciente a ciegas: lo verifica contra los
    dos extremos y falla explícitamente (ValueError, nunca un resultado
    silenciosamente incorrecto) si no lo es, o si target_price queda fuera
    del rango alcanzable entre lower_bound y upper_bound.
    """
    price_at_lower = evaluate_price(lower_bound)
    price_at_upper = evaluate_price(upper_bound)
    if price_at_upper - price_at_lower < price_tolerance:
        raise ValueError(
            f"El precio no varía de forma creciente en el rango dado "
            f"(f({lower_bound})=${price_at_lower:,.2f}, f({upper_bound})=${price_at_upper:,.2f}) "
            "-- no se puede resolver con bisección."
        )
    if not (price_at_lower - price_tolerance <= target_price <= price_at_upper + price_tolerance):
        raise ValueError(
            f"target_price=${target_price:,.2f} está fuera del rango alcanzable variando "
            f"el supuesto entre {lower_bound} (precio ${price_at_lower:,.2f}) y "
            f"{upper_bound} (precio ${price_at_upper:,.2f}). Amplía el rango de búsqueda "
            "si el supuesto sigue siendo razonable fuera de estos límites."
        )

    lo, hi = lower_bound, upper_bound
    mid = (lo + hi) / 2
    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        price_at_mid = evaluate_price(mid)
        if abs(price_at_mid - target_price) <= price_tolerance:
            return mid
        if price_at_mid < target_price:
            lo = mid
        else:
            hi = mid
    raise RuntimeError(
        f"No convergió en {max_iterations} iteraciones de bisección "
        f"(última diferencia: ${abs(evaluate_price(mid) - target_price):,.4f})."
    )


def implied_terminal_growth_rate(inputs: "DCFInputs", target_price: float,
                                  lower_bound: float = -0.10) -> float:
    """Reverse DCF sobre la tasa de crecimiento terminal (g): con TODO lo
    demás fijo (WACC, proyección explícita, múltiplo de salida, peso
    Gordon/múltiplo), resuelve qué `terminal_growth_rate` hace que
    run_dcf() reproduzca `target_price` (típicamente el precio de mercado
    o el consenso de analistas).

    Requiere que g tenga algún efecto sobre el precio: si hay múltiplo de
    salida Y `gordon_weight<=0`, Gordon Growth no se calcula en absoluto
    (ver M4, docs/AUDIT.md) y g es irrelevante para el resultado -- no hay
    nada que resolver, y se falla explícitamente en vez de devolver un
    valor arbitrario.

    lower_bound: cota inferior de búsqueda (-10% por defecto, una
    perpetuidad en declive severo -- amplíala si de verdad hace falta). La
    cota superior es wacc menos un margen ínfimo, el único límite real que
    impone gordon_growth_terminal_value() (wacc > g estricto). Si el g
    resuelto cae en la zona de spread WACC-g estrecho, la re-evaluación
    final emite el mismo aviso que cualquier otra llamada a
    gordon_growth_terminal_value() -- no uno nuevo ni distinto.
    """
    if inputs.terminal_ev_ebitda_multiple is not None and inputs.gordon_weight <= 0.0:
        raise ValueError(
            "gordon_weight<=0 con múltiplo de salida presente: terminal_growth_rate no "
            "tiene ningún efecto sobre el precio en esta configuración (ver hallazgo M4) "
            "-- no se puede resolver."
        )
    upper_bound = inputs.wacc - 1e-4

    def price_at_growth(g: float) -> float:
        return run_dcf(replace(inputs, terminal_growth_rate=g)).implied_share_price

    with warnings.catch_warnings():
        # Los candidatos intermedios de la búsqueda no son un resultado
        # real -- solo el valor final importa para decidir si avisar de un
        # spread WACC-g estrecho. Se re-evalúa sin suprimir justo después.
        warnings.simplefilter("ignore")
        solved_g = solve_for_target_price(price_at_growth, target_price, lower_bound, upper_bound)

    price_at_growth(solved_g)
    return solved_g


# ---------------------------------------------------------------------------
# Matriz de sensibilidad WACC x g (Consolidated!N51:S57 del Excel)
# ---------------------------------------------------------------------------

@dataclass
class SensitivityMatrix:
    wacc_values: list[float]
    growth_values: list[float]
    implied_share_price: list[list[float]]  # [fila=wacc][columna=g]


def sensitivity_matrix(inputs: DCFInputs, wacc_values: Sequence[float],
                        growth_values: Sequence[float]) -> SensitivityMatrix:
    """Corre run_dcf para cada combinación de WACC (filas) x tasa de
    crecimiento terminal g (columnas), variando únicamente esos dos
    parámetros y manteniendo el resto de `inputs` fijo. Réplica de la
    Data Table de sensibilidad del Excel.

    Nota: cada combinación con wacc <= g propagará el ValueError de
    gordon_growth_terminal_value (falla explícita, no un hueco silencioso
    en la matriz) — elige rangos donde wacc > g para todas las celdas,
    como hace el Excel de referencia.
    """
    if not wacc_values or not growth_values:
        raise ValueError("wacc_values y growth_values no pueden estar vacíos")

    rows = []
    for w in wacc_values:
        row = []
        for g in growth_values:
            varied_inputs = replace(inputs, wacc=w, terminal_growth_rate=g)
            row.append(run_dcf(varied_inputs).implied_share_price)
        rows.append(row)

    return SensitivityMatrix(
        wacc_values=list(wacc_values),
        growth_values=list(growth_values),
        implied_share_price=rows,
    )
