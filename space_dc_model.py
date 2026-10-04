#!/usr/bin/env python3
"""
space_dc_model.py -- a first-principles feasibility model for orbital AI data centers.

The model is deliberately simple: every quantity is normalised to ONE KILOWATT OF
IT POWER (electrical power delivered to accelerators + their servers + network),
so that a "data center" of any size is just N copies of that kilowatt plus scale
effects we discuss separately (launch cadence, debris cross-section, comms).

Sections
  1. Orbit & environment ......... sunlit fraction, Earth IR/albedo view factors, drag
  2. Power & thermal ............. solar array, radiator, optional heat pump (optimised)
  3. Mass budget ................. kg per kW of IT, by subsystem
  4. Radiation & reliability ..... TID, SEU per GPU, checkpoint goodput, attrition
  5. Communications .............. bandwidth needed vs available
  6. Economics ................... levelised cost of a delivered IT-kW-year, space vs ground
  7. Uncertainty ................. Monte Carlo + rank sensitivity
  8. Scale ....................... launches, re-entry mass, debris exposure

Run:  python3 space_dc_model.py            -> prints all tables, writes figures/ and results.json
All inputs live in the dataclasses below with their provenance noted inline.
Units: SI unless the name says otherwise (_kW, _usd, _yr, ...).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict, replace

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")

# ---------------------------------------------------------------- constants
SIGMA = 5.670374e-8      # Stefan-Boltzmann, W m^-2 K^-4
S0 = 1361.0              # solar constant at 1 AU, W m^-2 (TSI, ~1361 +/- 0.5)
R_E = 6371.0e3           # mean Earth radius, m
MU_E = 3.986004418e14    # GM_earth, m^3 s^-2
J2 = 1.08263e-3
OLR = 239.0              # global-mean outgoing longwave radiation, W m^-2 (CERES ~239-240)
ALBEDO = 0.30            # Bond albedo
G0 = 9.80665
YEAR = 365.25 * 86400.0
HOURS_PER_YEAR = 8766.0


# =========================================================================
# 1. ORBIT & ENVIRONMENT
# =========================================================================
def sso_inclination(h):
    """Sun-synchronous inclination (rad) for circular altitude h (m), from J2 precession."""
    a = R_E + h
    n = math.sqrt(MU_E / a**3)
    omega_dot = 2 * math.pi / (365.2422 * 86400)          # required nodal rate, rad/s
    cos_i = -omega_dot / (1.5 * J2 * (R_E / a) ** 2 * n)
    return math.acos(cos_i)


def beta_angle_series(h, ltan_hours=18.0, n_days=365):
    """Solar beta angle (rad) over a year for an SSO with given local time of ascending node."""
    inc = sso_inclination(h)
    eps = math.radians(23.44)
    days = np.arange(n_days)
    lam = np.radians(280.46 + 0.9856474 * days)            # mean solar ecliptic longitude (good to ~2 deg)
    # sun unit vector in ECI
    sun = np.stack([np.cos(lam), np.cos(eps) * np.sin(lam), np.sin(eps) * np.sin(lam)], 1)
    ra_sun = np.arctan2(sun[:, 1], sun[:, 0])
    raan = ra_sun + math.radians(15.0 * (ltan_hours - 12.0))  # node tracks the sun
    # orbit normal
    nrm = np.stack([np.sin(inc) * np.sin(raan), -np.sin(inc) * np.cos(raan),
                    np.full_like(raan, math.cos(inc))], 1)
    return np.arcsin(np.clip(np.sum(nrm * sun, 1), -1, 1))


def eclipse_fraction(h, beta):
    """Fraction of a circular orbit spent in Earth's (cylindrical) shadow."""
    beta = np.abs(np.asarray(beta))
    a = R_E + h
    beta_star = math.asin(R_E / a)
    arg = np.sqrt(h**2 + 2 * R_E * h) / (a * np.cos(np.minimum(beta, beta_star)))
    f = np.where(beta < beta_star, np.arccos(np.clip(arg, -1, 1)) / math.pi, 0.0)
    return f


def sunlit_stats(h, ltan_hours=18.0):
    b = beta_angle_series(h, ltan_hours)
    f = eclipse_fraction(h, b)
    period = 2 * math.pi * math.sqrt((R_E + h) ** 3 / MU_E)
    return dict(mean_eclipse_frac=float(f.mean()), max_eclipse_min=float(f.max() * period / 60),
                days_with_eclipse=int((f > 0).sum()), beta_min_deg=float(np.degrees(np.abs(b).min())),
                period_min=period / 60)


def _fib_sphere(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = math.pi * (1 + 5**0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)


_EARTH_PTS = None


def earth_view_factors(h, beta_deg, orientation, n_orbit=24, n_pts=300_000):
    """Orbit-averaged Earth IR view factor F and albedo factor Fa for BOTH faces of a flat radiator.

    The radiator is kept edge-on to the Sun (its normal is always perpendicular to the Sun
    vector; that is the whole point of a sun-shielded radiator).  `orientation` picks which
    in-plane direction the normal is rotated towards: 'radial' (one face looks at Earth, the
    other at the zenith) or 'along' (normal along the velocity vector, both faces see the limb).
    Earth is a Lambertian sphere; the integral F = (1/pi) Int cos(t_plate) cos(t_earth)/d^2 dA
    is done by Monte-Carlo quadrature over a Fibonacci grid on the sphere.
    Irradiance on a face = OLR*F (infrared) + ALBEDO*S0*Fa (reflected sunlight).
    """
    global _EARTH_PTS
    if _EARTH_PTS is None or len(_EARTH_PTS) != n_pts:
        _EARTH_PTS = _fib_sphere(n_pts) * R_E
    P = _EARTH_PTS
    dA = 4 * math.pi * R_E**2 / len(P)
    b = math.radians(beta_deg)
    sun = np.array([math.cos(b), math.sin(b), 0.0])
    acc = np.zeros((2, 2))
    lit_count = 0
    for u in np.linspace(0, 2 * math.pi, n_orbit, endpoint=False):
        s = (R_E + h) * np.array([math.sin(u), 0.0, math.cos(u)])
        proj = s @ sun
        lit = not (proj < 0 and np.linalg.norm(s - proj * sun) < R_E)
        lit_count += lit
        base = s / np.linalg.norm(s) if orientation == "radial" else np.array([math.cos(u), 0.0, -math.sin(u)])
        n = base - (base @ sun) * sun
        n /= np.linalg.norm(n)
        d = s - P
        dist = np.linalg.norm(d, axis=1)
        dh = d / dist[:, None]
        cos_e = np.einsum("ij,ij->i", P / R_E, dh)
        cz = np.clip((P / R_E) @ sun, 0, None)
        for k, nn in enumerate((n, -n)):
            cos_p = -dh @ nn
            m = (cos_e > 0) & (cos_p > 0)
            w = cos_e[m] * cos_p[m] / dist[m] ** 2 * dA / math.pi
            acc[k, 0] += w.sum()
            acc[k, 1] += (w * cz[m]).sum() * lit
    acc /= n_orbit
    return dict(F=acc[:, 0].tolist(), Fa=acc[:, 1].tolist(), sunlit=lit_count / n_orbit)


# Mean-activity thermospheric density (Vallado, "Fundamentals of Astrodynamics", exponential
# model built on CIRA-72).  Solar-maximum densities at 500-800 km are ~3-10x higher.
_RHO_TABLE = [  # (base alt km, rho0 kg/m^3, scale height km)
    (450, 1.585e-12, 60.828), (500, 6.967e-13, 63.822), (600, 1.454e-13, 71.835),
    (700, 3.614e-14, 88.667), (800, 1.170e-14, 124.64), (900, 5.245e-15, 181.05),
    (1000, 3.019e-15, 268.00), (1100, 1.825e-15, 268.00)]


def air_density(h, solar_factor=1.0):
    hk = h / 1e3
    base = [r for r in _RHO_TABLE if r[0] <= hk][-1]
    return solar_factor * base[1] * math.exp(-(hk - base[0]) / base[2])


def drag_dv_per_year(h, area_to_mass, cd=2.2, solar_factor=1.0):
    """Delta-v (m/s per year) to cancel drag on a body with projected-area/mass = area_to_mass."""
    v = math.sqrt(MU_E / (R_E + h))
    return 0.5 * air_density(h, solar_factor) * v**2 * cd * area_to_mass * YEAR


# =========================================================================
# 2-3. POWER, THERMAL AND MASS (per kW of IT load)
# =========================================================================
@dataclass
class Design:
    """Engineering inputs for one orbital compute node. Defaults = 'near-term credible' (c. 2030)."""
    altitude_m: float = 650e3
    # --- power ---
    cell_eff: float = 0.22            # BOL cell efficiency. Silicon (Starlink-style, ~$0.5/W cells) ~20-24%;
                                      # III-V triple junction ~30-33% but $100-450/W -> unaffordable at GW scale
    packing: float = 0.88             # cell area / array area
    pointing_cos: float = 0.99        # sun tracking error / cosine loss
    array_temp_loss: float = 0.10     # efficiency loss at ~60-80 C operating temp vs 28 C rating
    degradation_per_yr: float = 0.025 # power fade per year; Si ~2.5%/yr on orbit, III-V ~1-1.5%/yr
    pmad_eff: float = 0.92            # power management & distribution: regulation, conversion to 48/12V
    array_W_per_kg: float = 150.0     # array-level specific power incl. deployment structure (ROSA-class 100-150)
    bus_overhead: float = 0.08        # ADCS, avionics, comms, pumps, heaters as fraction of IT power
    battery_Wh_per_kg: float = 180.0  # cell->pack level Li-ion
    ride_through_eclipse: bool = False  # True: batteries carry full IT load in eclipse; False: throttle IT
    # --- thermal ---
    T_rad: float = 320.0              # radiator mean surface temperature, K (chip Tj ~ 360 K minus ~40 K of drops)
    eps_ir: float = 0.88              # IR emissivity (white paint / silvered FEP ~0.8-0.9)
    alpha_solar: float = 0.20         # solar absorptance (EOL; rises from ~0.1 with UV/AO darkening)
    fin_eff: float = 0.85             # fin efficiency / temperature non-uniformity factor
    rad_kg_per_m2: float = 6.0        # radiator panel + embedded loop + share of pumps/manifolds, per m^2 of PANEL
    rad_orientation: str = "radial"
    # --- compute & structure ---
    it_kg_per_kW: float = 6.0         # stripped server mass (boards, HBM, cold plates, PSU, NICs) per kW IT
    shield_kg_per_kW: float = 1.5     # ~10 mm Al-eq enclosure (27 kg/m^2) around densely packed boards
    struct_frac: float = 0.15         # primary structure, harness, mechanisms (fraction of subtotal)
    lifetime_yr: float = 5.0


@dataclass
class EnvLoads:
    F: tuple = (0.0, 0.82)        # IR view factor per face (computed in main(); placeholders here)
    Fa: tuple = (0.0, 0.07)       # albedo factor per face


def radiator_flux(T, d: Design, env: EnvLoads):
    """Net heat rejected per m^2 of two-sided radiator panel (W/m^2)."""
    q = 0.0
    for F, Fa in zip(env.F, env.Fa):
        q += d.eps_ir * SIGMA * T**4 - d.eps_ir * OLR * F - d.alpha_solar * ALBEDO * S0 * Fa
    return d.fin_eff * q


def sink_temperature(d: Design, env: EnvLoads):
    """Radiator temperature at which net rejection is zero (the effective environment)."""
    absorbed = sum(d.eps_ir * OLR * F + d.alpha_solar * ALBEDO * S0 * Fa for F, Fa in zip(env.F, env.Fa))
    return (absorbed / (2 * d.eps_ir * SIGMA)) ** 0.25


def array_area_power(d: Design):
    """Electrical W per m^2 of array at end of life (EOL) at the bus."""
    bol = S0 * d.cell_eff * d.packing * d.pointing_cos * (1 - d.array_temp_loss)
    eol = bol * (1 - d.degradation_per_yr) ** d.lifetime_yr
    return bol, eol


def array_equilibrium_temp(d: Design, alpha=0.92, eps_f=0.85, eps_b=0.85, F_back=0.0):
    """Solar array operating temperature: absorbed sunlight minus electricity, radiated from both faces."""
    q_in = alpha * S0 - S0 * d.cell_eff * d.packing * (1 - d.array_temp_loss)
    return (q_in / (SIGMA * (eps_f + eps_b))) ** 0.25


def size_node(d: Design, env: EnvLoads, eclipse_frac=0.0, eclipse_max_min=0.0, heat_pump_Th=None,
              hp_carnot_frac=0.5, hp_kg_per_kW=5.0):
    """Size the power and thermal subsystems for 1 kW of IT. Returns dict of areas, masses (kg/kW).

    Optional heat pump lifts heat from T_rad (cold side) to heat_pump_Th, costing work
    W = Q / COP,  COP = hp_carnot_frac * T_c / (T_h - T_c).  Both Q and W must then be radiated.
    """
    P_it = 1000.0
    P_bus = d.bus_overhead * P_it
    W_hp = 0.0
    T_reject = d.T_rad
    if heat_pump_Th is not None and heat_pump_Th > d.T_rad:
        cop = hp_carnot_frac * d.T_rad / (heat_pump_Th - d.T_rad)
        W_hp = (P_it + P_bus) / cop
        T_reject = heat_pump_Th
    P_load = P_it + P_bus + W_hp                     # electrical load at the bus
    # Eclipse: either batteries carry the load, or IT throttles and only the bus is carried.
    if d.ride_through_eclipse:
        P_ecl = P_load
    else:
        P_ecl = P_bus
    # Array must supply load during sun + recharge eclipse energy (charge eff ~0.9)
    f = eclipse_frac
    P_array = (P_load * (1 - f) + P_ecl * f / 0.9) / max(1 - f, 1e-9) / d.pmad_eff
    bol, eol = array_area_power(d)
    A_array = P_array / eol
    m_array = P_array / d.array_W_per_kg * (bol / eol)   # array specific power quoted at BOL
    # Battery sized for the worst-case eclipse of the year, 30% depth of discharge for cycle life
    E_batt = P_ecl * eclipse_max_min / 60 / 0.3            # Wh nameplate
    m_batt = E_batt / d.battery_Wh_per_kg
    # Heat to reject: all electrical power that reaches the IT + bus ends up as heat (+ pump work)
    Q = P_load / d.pmad_eff                          # load heat + power-conversion losses
    q = radiator_flux(T_reject, d, env)
    A_rad = Q / q
    m_rad = A_rad * d.rad_kg_per_m2
    m_hp = W_hp / 1000 * hp_kg_per_kW
    m_it = d.it_kg_per_kW
    sub = m_array + m_batt + m_rad + m_hp + m_it + d.shield_kg_per_kW
    m_struct = d.struct_frac * sub
    total = sub + m_struct
    return dict(P_array_BOL_W=P_array * bol / eol, A_array_m2=A_array, A_rad_m2=A_rad, q_rad_W_m2=q,
                W_heatpump_W=W_hp, m_array=m_array, m_battery=m_batt, m_radiator=m_rad, m_heatpump=m_hp,
                m_it=m_it, m_shield=d.shield_kg_per_kW, m_struct=m_struct, m_total=total,
                kg_per_kW=total, W_per_kg_system=1000 / total)


def optimise_heat_pump(d: Design, env: EnvLoads, carnot_frac=0.5, hp_kg_per_kW=5.0):
    """Find the hot-side temperature that minimises total mass. Returns (T_opt, mass_opt, mass_no_hp)."""
    base = size_node(d, env)["kg_per_kW"]
    res = minimize_scalar(lambda Th: size_node(d, env, heat_pump_Th=Th, hp_carnot_frac=carnot_frac,
                                               hp_kg_per_kW=hp_kg_per_kW)["kg_per_kW"],
                          bounds=(d.T_rad + 0.5, d.T_rad + 250), method="bounded")
    return float(res.x), float(res.fun), base


# =========================================================================
# 4. RADIATION & RELIABILITY
# =========================================================================
@dataclass
class Reliability:
    # Ground baseline from Meta Llama-3 405B run: 419 unexpected interruptions in 54 days on
    # 16,384 H100 GPUs  ->  per-GPU interrupt MTBF = 16384*54/419 days ~ 5.8 years.
    gpu_interrupt_mtbf_yr_ground: float = 16384 * 54 / 419 / 365.25
    space_interrupt_multiplier: float = 3.0   # SEFIs / uncorrectable upsets add to ground causes (uncertain 1-10x)
    checkpoint_write_s: float = 30.0          # async/in-memory checkpoint stall
    restart_s: float = 300.0                  # detect, reschedule, reload (calibrated: gives ~90% goodput for Llama-3 run)
    hw_afr_ground: float = 0.03               # annualised hard failure rate of a GPU node (incl. HBM, NIC, PSU)
    space_afr_multiplier: float = 1.5         # radiation (TID/SEL), thermal cycling, no burn-in of replacements
    spares_frac: float = 0.0                  # extra GPUs flown as cold/hot spares


def young_daly_goodput(n_gpus, mtbf_gpu_s, delta_s, restart_s):
    """Fraction of wall-clock doing useful work for a synchronous job on n_gpus with optimal checkpointing.

    System MTBF M = mtbf_gpu/n. Optimal interval tau = sqrt(2 delta M) (Young 1974);
    waste ~ delta/tau + (tau/2 + restart)/M.
    """
    M = mtbf_gpu_s / n_gpus
    tau = math.sqrt(2 * delta_s * M)
    waste = delta_s / tau + (tau / 2 + restart_s) / M
    return max(0.0, 1 - waste), tau, M


def mean_surviving_fraction(afr, years, spares_frac=0.0):
    """Average capacity over life when failed units are never replaced (exponential failures).

    With spares s, capacity is capped at 1 until survivors drop below 1/(1+s).
    """
    lam = -math.log(1 - afr) if afr < 1 else 10.0
    t = np.linspace(0, years, 2001)
    cap = np.minimum(1.0, (1 + spares_frac) * np.exp(-lam * t))
    return float(np.trapezoid(cap, t) / years)


# --- Total ionising dose: a phenomenological two-component fit, NOT a transport calculation. -------------
# Anchors (all for high-inclination LEO): Suncatcher model ~150 rad(Si)/yr at 650 km behind ~10 mm Al-eq;
# Shields-1 measurement 75.6 rad/yr at 500 km (thick Z-shield); SPENVIS-type polar LEO ~6 krad/yr behind 1 mm Al;
# 800 km SSO CubeSat analyses <~1.9 krad/yr behind a few mm.  Trapped-proton dose (SAA) grows ~e-fold per
# ~120 km of altitude in 500-1000 km and is only weakly shielded; outer-belt/horn electron dose is steeply
# attenuated by the first few mm.  Treat outputs as +/- factor 3.
def tid_rad_per_yr(h_km, al_mm):
    proton = 150.0 * math.exp((h_km - 650.0) / 120.0) * (10.0 / max(al_mm, 0.5)) ** 0.35
    electron = 5000.0 * math.exp(-(max(al_mm, 0.5) - 1.0) / 0.9) * (1.0 if h_km >= 500 else 0.7)
    return proton + electron


# Proton-beam results for a modern accelerator (Google Trillium TPU v6e + HBM, 67 MeV protons, Suncatcher 2025):
RAD_PER_SDC = 17.0          # one silent data corruption per ~14.4-20 rad(Si) per chip (workload dependent)
RAD_PER_HBM_UECC = 44.0     # one uncorrectable HBM ECC error per ~44 rad(Si)
RAD_PER_SEFI = 5000.0       # one functional interrupt (crash/hang) per ~5 krad(Si)
HBM_ONSET_RAD = 2000.0      # HBM irregularities begin; no hard failure up to 15 krad on one chip


def radiation_event_rates(h_km, al_mm):
    """Per-chip event rates per year, scaling beam cross-sections by dose (valid when protons dominate, >~5 mm)."""
    D = tid_rad_per_yr(h_km, al_mm)
    return dict(dose_rad_yr=D, sdc_per_yr=D / RAD_PER_SDC, uecc_per_yr=D / RAD_PER_HBM_UECC,
                sefi_per_yr=D / RAD_PER_SEFI, years_to_hbm_onset=HBM_ONSET_RAD / D)


def interrupt_multiplier(h_km, al_mm, uecc_interrupts=True, ground_rate_per_yr=None):
    """Ratio of per-GPU job-interrupt rate in orbit to the ground rate (Llama-3: 0.173 per GPU-year)."""
    g = ground_rate_per_yr or 419 / (16384 * 54 / 365.25)
    r = radiation_event_rates(h_km, al_mm)
    extra = r["sefi_per_yr"] + (r["uecc_per_yr"] if uecc_interrupts else 0.0)
    return (g + extra) / g


def seu_per_gpu_day(bits, sigma_cm2_per_bit, flux_cm2_s):
    """Upsets per day for a memory of `bits` with per-bit cross-section in an integral flux."""
    return bits * sigma_cm2_per_bit * flux_cm2_s * 86400


# =========================================================================
# 6. ECONOMICS
# =========================================================================
@dataclass
class Econ:
    # --- shared ---
    it_capex_usd_per_kW: float = 30_000.0     # accelerators+servers+network per kW of IT (GB200 NVL72 ~$3.4M / 120-132 kW)
    discount_rate: float = 0.08
    it_life_yr: float = 5.0                   # economic life of accelerators
    # --- ground ---
    fac_capex_usd_per_kW: float = 15_000.0    # shell+power+liquid cooling per kW IT (JLL $11.3M/MW shell; AI liquid $20M+/MW)
    fac_life_yr: float = 15.0
    pue: float = 1.2
    elec_usd_per_kWh: float = 0.08            # US industrial avg 8.6-9.3 c/kWh (EIA 2026); solar PPA ~6 c; solar+storage ~8 c
    ground_opex_frac: float = 0.03            # staff, maintenance, network as fraction of capex per yr
    ground_availability: float = 0.99
    # --- space ---
    launch_usd_per_kg: float = 600.0          # price to operating orbit; early commercial Starship (2030 guess)
    sc_usd_per_kg: float = 800.0              # spacecraft hardware (non-IT) at mass production; Starlink V2-mini ~$1000/kg, V3 ~$650/kg (est.)
    array_usd_per_W: float = 0.0              # extra cost of cells if not inside sc_usd_per_kg (0 = included)
    space_it_premium: float = 0.10            # qualification, screening, conformal coat, custom boards
    space_opex_frac: float = 0.02             # ops, ground segment, insurance, spectrum, as fraction of capex per yr
    comms_usd_per_kW: float = 500.0           # optical terminals / gateways share per kW
    deploy_delay_yr: float = 0.5              # capital outstanding before revenue (build+launch+commission)


def crf(r, n):
    """Capital recovery factor: annual payment per $ of capex over n years at rate r."""
    return r / (1 - (1 + r) ** -n) if r > 0 else 1 / n


def ground_cost_per_kWyr(e: Econ, util_ground=1.0):
    """Annualised cost of one *delivered* IT-kW-year on the ground, and its breakdown."""
    it = e.it_capex_usd_per_kW * crf(e.discount_rate, e.it_life_yr)
    fac = e.fac_capex_usd_per_kW * crf(e.discount_rate, e.fac_life_yr)
    energy = e.pue * e.elec_usd_per_kWh * HOURS_PER_YEAR * e.ground_availability
    opex = e.ground_opex_frac * (e.it_capex_usd_per_kW + e.fac_capex_usd_per_kW)
    tot = it + fac + energy + opex
    deliv = e.ground_availability * util_ground
    return tot / deliv, dict(IT=it / deliv, facility=fac / deliv, energy=energy / deliv, opex=opex / deliv)


def space_cost_per_kWyr(e: Econ, kg_per_kW, it_kg_per_kW, availability, goodput=1.0, sc_life=None):
    """Annualised cost of one delivered IT-kW-year in orbit.

    availability = sunlit/throttle factor x mean surviving fraction (no repair).
    The whole spacecraft (power+thermal+bus) is amortised over the satellite life, not 15 yr.
    """
    L = sc_life or e.it_life_yr
    nonit = kg_per_kW - it_kg_per_kW
    it_c = e.it_capex_usd_per_kW * (1 + e.space_it_premium)
    hw = nonit * e.sc_usd_per_kg + e.comms_usd_per_kW
    launch = kg_per_kW * e.launch_usd_per_kg
    capex = it_c + hw + launch
    capex *= (1 + e.discount_rate) ** e.deploy_delay_yr          # interest during construction
    k = crf(e.discount_rate, L)
    opex = e.space_opex_frac * capex
    deliv = availability * goodput
    parts = dict(IT=it_c * k, spacecraft=hw * k, launch=launch * k, opex=opex,
                 financing=(capex - (it_c + hw + launch)) * k)
    tot = sum(parts.values())
    return tot / deliv, {kk: v / deliv for kk, v in parts.items()}


# =========================================================================
# scenarios
# =========================================================================
def scenarios():
    """Four coherent scenarios (Design, Econ, Reliability).

    today      : flight-proven parts at 2026 prices (Falcon 9, ROSA-class arrays, ISS-lineage radiators)
    near_term  : ~2030, early commercial Starship, Starlink-V3-class mass production
    optimistic : ~2035, Suncatcher's $200/kg threshold met, lightweight deployables, purpose-built hardware
    advocate   : the assumptions implicit in the boldest public claims ($50/kg, ~10 kg/kW all-in, $150/kg bus)
    """
    today = (Design(array_W_per_kg=100, rad_kg_per_m2=10.0, it_kg_per_kW=10.0, T_rad=315, shield_kg_per_kW=2.0,
                    bus_overhead=0.10),
             Econ(launch_usd_per_kg=3000, sc_usd_per_kg=2000, space_it_premium=0.15, comms_usd_per_kW=1000),
             Reliability(space_afr_multiplier=2.0))
    near = (Design(), Econ(), Reliability())
    optimistic = (Design(array_W_per_kg=250, rad_kg_per_m2=4.0, it_kg_per_kW=4.5, T_rad=330, degradation_per_yr=0.02,
                         struct_frac=0.12, shield_kg_per_kW=1.0, bus_overhead=0.06),
                  Econ(launch_usd_per_kg=200, sc_usd_per_kg=400, space_it_premium=0.05, comms_usd_per_kW=300),
                  Reliability(space_afr_multiplier=1.25))
    advocate = (Design(cell_eff=0.24, array_W_per_kg=500, rad_kg_per_m2=2.0, it_kg_per_kW=3.0, T_rad=340,
                       degradation_per_yr=0.015, struct_frac=0.08, shield_kg_per_kW=0.5, bus_overhead=0.05),
                Econ(launch_usd_per_kg=50, sc_usd_per_kg=150, space_it_premium=0.0, comms_usd_per_kW=100,
                     space_opex_frac=0.01),
                Reliability(space_afr_multiplier=1.0))
    return {"today": today, "near_term": near, "optimistic": optimistic, "advocate": advocate}




# =========================================================================
# 5. COMMUNICATIONS
# =========================================================================
def inference_downlink_gbps_per_MW(tok_s_per_gpu=5000, kW_per_gpu=1.8, bytes_per_token=6, overhead=10):
    """Ground-link bandwidth for serving text tokens from 1 MW of IT (Gbps).

    tok/s/GPU ~ 4-13k on Blackwell (InferenceMAX / MLPerf v5.1); 4 B/token text, ~6 with streaming framing;
    `overhead` multiplies for prompts/context upload, tool calls, protocol.
    """
    gpus = 1000 / kW_per_gpu
    return gpus * tok_s_per_gpu * bytes_per_token * 8 * overhead / 1e9


def dp_allreduce_tbps(params, step_s, bytes_per_grad=2):
    """Bandwidth across one data-parallel cut for a ring all-reduce of the full gradient each step."""
    return 2 * params * bytes_per_grad * 8 / step_s / 1e12


def optical_link_rx_fraction(D_t, D_r, L, wavelength=1.55e-6):
    """Received/transmitted power for a diffraction-limited Gaussian-ish beam (capped at 1 in the near field)."""
    return min(1.0, (math.pi * D_t * D_r / (4 * wavelength * L)) ** 2)


# =========================================================================
# 7. UNCERTAINTY: Monte Carlo over a 2030s 'plausible world'
# =========================================================================
MC_RANGES = {  # name: (kind, lo, hi)   log = log-uniform.  A deliberately generous "2030s" envelope.
    "launch_usd_per_kg": ("log", 100, 3000),
    "sc_usd_per_kg": ("log", 300, 3000),
    "array_W_per_kg": ("lin", 100, 400),
    "rad_kg_per_m2": ("lin", 3, 12),
    "T_rad": ("lin", 305, 345),
    "it_kg_per_kW": ("lin", 3, 10),
    "it_capex_usd_per_kW": ("lin", 20_000, 40_000),
    "fac_capex_usd_per_kW": ("lin", 10_000, 25_000),
    "elec_usd_per_kWh": ("lin", 0.05, 0.15),
    "hw_afr_ground": ("lin", 0.02, 0.06),
    "space_afr_multiplier": ("lin", 1.0, 3.0),
    "discount_rate": ("lin", 0.06, 0.12),
    "lifetime_yr": ("lin", 4, 7),
}


def infra_cost_per_kWh(parts, ground=False):
    """'Power + cooling + housing' cost per delivered IT-kWh: everything except the compute hardware itself.

    This is the quantity behind the claim that 'energy is cheaper in space'.
    """
    keys = ("facility", "energy", "opex") if ground else ("spacecraft", "launch", "opex", "financing")
    return sum(parts[k] for k in keys) / HOURS_PER_YEAR


def evaluate(d: Design, e: Econ, env: EnvLoads, sun: dict, rel: Reliability):
    """Full chain for one parameter set. Returns dict with mass, costs, ratio."""
    node = size_node(d, env, eclipse_frac=sun["mean_eclipse_frac"], eclipse_max_min=sun["max_eclipse_min"])
    sunlit = 1.0 if d.ride_through_eclipse else 1 - sun["mean_eclipse_frac"]
    surv = mean_surviving_fraction(rel.hw_afr_ground * rel.space_afr_multiplier, d.lifetime_yr, rel.spares_frac)
    avail_space = sunlit * surv * 0.99
    e2 = replace(e, it_life_yr=d.lifetime_yr)
    cs, ps = space_cost_per_kWyr(e2, node["kg_per_kW"] + rel.spares_frac * d.it_kg_per_kW,
                                 d.it_kg_per_kW * (1 + rel.spares_frac), avail_space)
    if rel.spares_frac:
        extra = rel.spares_frac * e.it_capex_usd_per_kW * (1 + e.space_it_premium) * crf(e.discount_rate, d.lifetime_yr)
        cs += extra / avail_space
        ps["IT"] += extra / avail_space
    e3 = replace(e2, ground_opex_frac=e.ground_opex_frac)
    cg, pg = ground_cost_per_kWyr(e3)
    return dict(node=node, space=cs, ground=cg, ratio=cs / cg, space_parts=ps, ground_parts=pg,
                avail_space=avail_space, surviving=surv)


def monte_carlo(env, sun, n=20000, seed=1):
    rng = np.random.default_rng(seed)
    X = {}
    for k, (kind, lo, hi) in MC_RANGES.items():
        u = rng.random(n)
        X[k] = np.exp(np.log(lo) + u * (np.log(hi) - np.log(lo))) if kind == "log" else lo + u * (hi - lo)
    ratio = np.empty(n)
    space = np.empty(n)
    ground = np.empty(n)
    for i in range(n):
        p = {k: float(v[i]) for k, v in X.items()}
        d = Design(array_W_per_kg=p["array_W_per_kg"], rad_kg_per_m2=p["rad_kg_per_m2"], T_rad=p["T_rad"],
                   it_kg_per_kW=p["it_kg_per_kW"], lifetime_yr=p["lifetime_yr"])
        e = Econ(launch_usd_per_kg=p["launch_usd_per_kg"], sc_usd_per_kg=p["sc_usd_per_kg"],
                 it_capex_usd_per_kW=p["it_capex_usd_per_kW"], fac_capex_usd_per_kW=p["fac_capex_usd_per_kW"],
                 elec_usd_per_kWh=p["elec_usd_per_kWh"], discount_rate=p["discount_rate"])
        rel = Reliability(hw_afr_ground=p["hw_afr_ground"], space_afr_multiplier=p["space_afr_multiplier"])
        r = evaluate(d, e, env, sun, rel)
        ratio[i], space[i], ground[i] = r["ratio"], r["space"], r["ground"]
    sens = {k: float(spearmanr(v, ratio)[0]) for k, v in X.items()}
    return dict(ratio=ratio, space=space, ground=ground, X=X, spearman=sens)


# =========================================================================
# break-even solvers
# =========================================================================
def breakeven_launch(d, e, env, sun, rel, lo=0.0, hi=50_000.0):
    """Launch price ($/kg) at which space == ground. None if space loses even at $0/kg."""
    f = lambda c: evaluate(d, replace(e, launch_usd_per_kg=c), env, sun, rel)["ratio"] - 1
    if f(lo) > 0:
        return None
    if f(hi) < 0:
        return hi
    return brentq(f, lo, hi)


def breakeven_ground_power_price(d, e, env, sun, rel):
    """Ground electricity price ($/kWh, delivered) that makes ground cost == space cost."""
    r = evaluate(d, e, env, sun, rel)
    f = lambda p: ground_cost_per_kWyr(replace(e, elec_usd_per_kWh=p, it_life_yr=d.lifetime_yr))[0] - r["space"]
    return brentq(f, -5.0, 50.0)   # negative => orbit wins even against free ground electricity


# =========================================================================
# 8. SCALE
# =========================================================================
def scale_numbers(kg_per_kW, GW, payload_t=100.0):
    mass_t = kg_per_kW * GW * 1e6 / 1e3
    return dict(mass_t=mass_t, launches=mass_t / payload_t)




def default_context(h=650e3, orientation="radial"):
    """(EnvLoads, sun-stats) for a dawn-dusk SSO at altitude h -- the inputs evaluate() needs.

    Example (couple to an external ground-power-price forecast):
        env, sun = default_context()
        d, e, rel = scenarios()["near_term"]
        evaluate(d, replace(e, elec_usd_per_kWh=0.20), env, sun, rel)["ratio"]
    """
    sun = sunlit_stats(h)
    beta_mean = float(np.degrees(np.abs(beta_angle_series(h))).mean())
    vf = earth_view_factors(h, beta_mean, orientation)
    return EnvLoads(F=tuple(vf["F"]), Fa=tuple(vf["Fa"])), sun


# =========================================================================
# main: compute everything, print, plot
# =========================================================================
def _fmt(x, nd=0):
    return f"{x:,.{nd}f}"


def main(make_figs=True, n_mc=20000):
    out = {}
    h = 650e3
    sun = sunlit_stats(h)
    out["sunlit"] = sun
    out["sunlit_by_alt"] = {int(a / 1e3): sunlit_stats(a) for a in (500e3, 650e3, 800e3, 1000e3, 1200e3)}
    print("\n== 1. Orbit (dawn-dusk SSO, %d km) ==" % (h / 1e3))
    for a, v in out["sunlit_by_alt"].items():
        print(f"  {a:>5} km: mean eclipse {100 * v['mean_eclipse_frac']:.1f}% of time, longest {v['max_eclipse_min']:.1f} min,"
              f" eclipse season {v['days_with_eclipse']} d/yr")
    beta_mean = float(np.degrees(np.abs(beta_angle_series(h))).mean())
    vf = {o: earth_view_factors(h, beta_mean, o) for o in ("radial", "along")}
    out["view_factors"] = {o: {"F": v["F"], "Fa": v["Fa"]} for o, v in vf.items()}
    out["beta_mean_deg"] = beta_mean
    for o, v in vf.items():
        print(f"  radiator {o:6s}: IR view factor per face {np.round(v['F'], 3)}, albedo factor {np.round(v['Fa'], 3)}")
    env = EnvLoads(F=tuple(vf["radial"]["F"]), Fa=tuple(vf["radial"]["Fa"]))
    env_along = EnvLoads(F=tuple(vf["along"]["F"]), Fa=tuple(vf["along"]["Fa"]))

    # ---- thermal fundamentals
    d0 = Design()
    bol, eol = array_area_power(d0)
    T_arr = array_equilibrium_temp(d0)
    Ts = np.linspace(260, 400, 141)
    out["thermal"] = dict(T=Ts.tolist(), q_radial=[radiator_flux(T, d0, env) for T in Ts],
                          q_along=[radiator_flux(T, d0, env_along) for T in Ts],
                          q_ideal=[2 * d0.eps_ir * SIGMA * T**4 * d0.fin_eff for T in Ts],
                          array_W_m2_BOL=bol, array_W_m2_EOL=eol, array_T_K=T_arr,
                          sink_T_radial=sink_temperature(d0, env), sink_T_along=sink_temperature(d0, env_along),
                          q_design_radial=radiator_flux(d0.T_rad, d0, env),
                          q_design_along=radiator_flux(d0.T_rad, d0, env_along))
    print("\n== 2. Power & thermal fundamentals ==")
    print(f"  array electrical output: {bol:.0f} W/m^2 BOL, {eol:.0f} W/m^2 EOL; array runs at {T_arr - 273.15:.0f} C")
    print(f"  radiator net flux at {d0.T_rad:.0f} K: radial {out['thermal']['q_design_radial']:.0f} W/m^2, "
          f"along-track {out['thermal']['q_design_along']:.0f} W/m^2 (two-sided panel)")
    print(f"  effective sink temperature: radial {out['thermal']['sink_T_radial']:.0f} K, along {out['thermal']['sink_T_along']:.0f} K")
    node0 = size_node(d0, env, sun["mean_eclipse_frac"], sun["max_eclipse_min"])
    A_over_m_along = node0["A_rad_m2"] / node0["kg_per_kW"]
    drag = {int(a / 1e3): dict(ram_mean=drag_dv_per_year(a, A_over_m_along),
                               ram_solar_max=drag_dv_per_year(a, A_over_m_along, solar_factor=5),
                               edge_on_mean=drag_dv_per_year(a, 0.05 * A_over_m_along))
            for a in (500e3, 550e3, 650e3, 800e3, 1000e3)}
    out["drag"] = dict(A_over_m=A_over_m_along, by_alt=drag)
    print(f"  drag dv (m/s/yr) with radiator facing ram, A/m={A_over_m_along:.3f} m^2/kg (mean / solar max):",
          {k: (round(v['ram_mean'], 1), round(v['ram_solar_max'], 1)) for k, v in drag.items()})

    # ---- scenarios
    print("\n== 3. Mass budget per kW of IT ==")
    out["scenarios"] = {}
    for name, (d, e, rel) in scenarios().items():
        r = evaluate(d, e, env, sun, rel)
        be = breakeven_launch(d, e, env, sun, rel)
        pbe = breakeven_ground_power_price(d, e, env, sun, rel)
        zero = evaluate(d, replace(e, launch_usd_per_kg=0, sc_usd_per_kg=0, comms_usd_per_kW=0), env, sun, rel)
        n = r["node"]
        out["scenarios"][name] = dict(design=asdict(d), econ=asdict(e), rel=asdict(rel), node=n, space=r["space"],
                                      ground=r["ground"], ratio=r["ratio"], space_parts=r["space_parts"],
                                      ground_parts=r["ground_parts"], avail_space=r["avail_space"],
                                      surviving=r["surviving"], breakeven_launch=be, breakeven_elec=pbe,
                                      ratio_free_launch_and_bus=zero["ratio"],
                                      infra_kWh_space=infra_cost_per_kWh(r["space_parts"]),
                                      infra_kWh_ground=infra_cost_per_kWh(r["ground_parts"], ground=True),
                                      usd_per_gpu_hr_space=r["space"] * 1.8 / HOURS_PER_YEAR,
                                      usd_per_gpu_hr_ground=r["ground"] * 1.8 / HOURS_PER_YEAR)
        print(f"  [{name}] total {n['kg_per_kW']:.1f} kg/kW  (array {n['m_array']:.1f}, radiator {n['m_radiator']:.1f}, "
              f"IT {n['m_it']:.1f}, battery {n['m_battery']:.2f}, shield {n['m_shield']:.1f}, struct {n['m_struct']:.1f})"
              f"  | array {n['A_array_m2']:.2f} m^2/kW, radiator {n['A_rad_m2']:.2f} m^2/kW ({n['q_rad_W_m2']:.0f} W/m^2)")
    print("\n== 6. Economics: cost per delivered IT-kW-year ==")
    for name, s in out["scenarios"].items():
        be = s["breakeven_launch"]
        print(f"  [{name}] space ${_fmt(s['space'])} vs ground ${_fmt(s['ground'])}  ratio {s['ratio']:.2f}  "
              f"| break-even launch: {'none' if be is None else '$' + _fmt(be) + '/kg'}  "
              f"| break-even ground elec ${s['breakeven_elec']:.2f}/kWh  "
              f"| ratio if launch & bus free: {s['ratio_free_launch_and_bus']:.2f}")
        print(f"      power+cooling+housing per IT-kWh: space ${s['infra_kWh_space']:.3f} vs ground ${s['infra_kWh_ground']:.3f};"
              f"  per GPU-hour (1.8 kW all-in): space ${s['usd_per_gpu_hr_space']:.2f} vs ground ${s['usd_per_gpu_hr_ground']:.2f}")
        print("      space parts:", {k: _fmt(v) for k, v in s["space_parts"].items()})
        print("      ground parts:", {k: _fmt(v) for k, v in s["ground_parts"].items()})
    g = out["scenarios"]["near_term"]["ground_parts"]
    out["ground_shares"] = {k: v / sum(g.values()) for k, v in g.items()}
    print("  ground cost shares:", {k: f"{100 * v:.0f}%" for k, v in out["ground_shares"].items()})

    # ---- heat pump study
    hp = {}
    for rk in (3, 6, 10, 15):
        dd = replace(d0, rad_kg_per_m2=rk)
        Th = np.linspace(d0.T_rad, d0.T_rad + 160, 81)
        m = [size_node(dd, env, sun["mean_eclipse_frac"], sun["max_eclipse_min"],
                       heat_pump_Th=t if t > d0.T_rad else None, hp_carnot_frac=0.5, hp_kg_per_kW=3.0)["kg_per_kW"]
             for t in Th]
        hp[rk] = dict(Th=Th.tolist(), kg_per_kW=m, T_opt=float(Th[int(np.argmin(m))]), m_opt=float(min(m)), m0=m[0])
    out["heat_pump"] = hp
    print("\n== 2b. Heat pump: optimum hot-side temperature (50% of Carnot, 3 kg/kW pump) ==")
    for rk, v in hp.items():
        print(f"  radiator {rk:>2} kg/m^2: T_opt {v['T_opt']:.0f} K, mass {v['m_opt']:.1f} vs {v['m0']:.1f} kg/kW without")

    # ---- radiator temperature sweep
    Tsw = np.linspace(290, 370, 41)
    nodes = [size_node(replace(d0, T_rad=t), env, sun["mean_eclipse_frac"], sun["max_eclipse_min"]) for t in Tsw]
    out["T_sweep"] = dict(T=Tsw.tolist(), kg_per_kW=[n["kg_per_kW"] for n in nodes], A_rad=[n["A_rad_m2"] for n in nodes])

    # ---- ratio and break-even electricity vs launch price
    Ls = np.geomspace(10, 10_000, 61)
    out["ratio_vs_launch"] = {"launch": Ls.tolist()}
    out["breakeven_elec_vs_launch"] = {"launch": Ls.tolist()}
    for name, (d, e, rel) in scenarios().items():
        out["ratio_vs_launch"][name] = [evaluate(d, replace(e, launch_usd_per_kg=c), env, sun, rel)["ratio"] for c in Ls]
        out["breakeven_elec_vs_launch"][name] = [breakeven_ground_power_price(d, replace(e, launch_usd_per_kg=c),
                                                                              env, sun, rel) for c in Ls]

    # ---- break-even map over (spacecraft $/kg, system kg/kW), near-term everything else
    dN, eN, rN = scenarios()["near_term"]
    refN = evaluate(dN, eN, env, sun, rN)
    sc_grid = np.geomspace(100, 5000, 60)
    kg_grid = np.linspace(5, 50, 61)
    BE = np.zeros((len(kg_grid), len(sc_grid)))
    for i, kk in enumerate(kg_grid):
        for j, scp in enumerate(sc_grid):
            def f(c, kk=kk, scp=scp):
                cs, _ = space_cost_per_kWyr(replace(eN, sc_usd_per_kg=scp, launch_usd_per_kg=c,
                                                    it_life_yr=dN.lifetime_yr), kk, dN.it_kg_per_kW,
                                            refN["avail_space"])
                return cs / refN["ground"] - 1
            BE[i, j] = 0.0 if f(0) > 0 else brentq(f, 0, 1e6)
    out["breakeven_map"] = dict(sc_usd_per_kg=sc_grid.tolist(), kg_per_kW=kg_grid.tolist(), launch=BE.tolist())

    # ---- what if accelerators get much cheaper per watt (energy share rises)?
    capex_grid = np.linspace(5_000, 40_000, 36)
    out["ratio_vs_it_capex"] = {"it_capex": capex_grid.tolist()}
    for name, (d, e, rel) in scenarios().items():
        out["ratio_vs_it_capex"][name] = [evaluate(d, replace(e, it_capex_usd_per_kW=c), env, sun, rel)["ratio"]
                                          for c in capex_grid]

    # ---- radiation
    print("\n== 4. Radiation & reliability ==")
    depth = np.linspace(0.5, 20, 40)
    out["radiation"] = dict(depth_mm=depth.tolist(),
                            dose_depth={str(a): [tid_rad_per_yr(a, x) for x in depth] for a in (500, 650, 800, 1000)},
                            thresholds=[(HBM_ONSET_RAD / 5, "HBM onset spread over 5 yr (400 rad/yr)")])
    rates = {}
    for a in (500, 650, 800, 1000):
        for x in (3, 10):
            r = radiation_event_rates(a, x)
            r["interrupt_mult_uecc_crash"] = interrupt_multiplier(a, x, True)
            r["interrupt_mult_uecc_handled"] = interrupt_multiplier(a, x, False)
            rates[f"{a}km_{x}mm"] = r
            print(f"  {a:>4} km, {x:>2} mm Al: {r['dose_rad_yr']:7.0f} rad/yr | SDC {r['sdc_per_yr']:6.1f}/chip-yr | "
                  f"HBM UECC {r['uecc_per_yr']:6.1f} | SEFI {r['sefi_per_yr']:.3f} | HBM onset in {r['years_to_hbm_onset']:5.1f} yr"
                  f" | interrupt x{r['interrupt_mult_uecc_crash']:.0f} (UECC=crash) / x{r['interrupt_mult_uecc_handled']:.1f}")
    out["radiation"]["rates"] = rates
    r650 = rates["650km_10mm"]
    print(f"  16k-chip cluster @650 km/10 mm: one SDC every {3600 * 24 * 365.25 / (16384 * r650['sdc_per_yr']) / 60:.1f} min,"
          f" one HBM UECC every {3600 * 24 * 365.25 / (16384 * r650['uecc_per_yr']) / 60:.1f} min")
    mtbf_s = Reliability().gpu_interrupt_mtbf_yr_ground * YEAR
    Ns = np.geomspace(100, 1e6, 41)
    mults = (1, 3, 10, 30)
    out["goodput"] = dict(N=Ns.tolist(), curves={str(k): [young_daly_goodput(N, mtbf_s / k, 30.0, 300.0)[0] for N in Ns]
                                                 for k in mults})
    for mult in (1, r650["interrupt_mult_uecc_handled"], 3, 10, r650["interrupt_mult_uecc_crash"]):
        gp, tau, M = young_daly_goodput(16384, mtbf_s / mult, 30.0, 300.0)
        print(f"  16k-GPU job, interrupts x{mult:.1f}: system MTBF {M / 3600:.2f} h, checkpoint every {tau / 60:.0f} min,"
              f" goodput {gp:.3f}")
    out["goodput_16k"] = {f"{m:.1f}": young_daly_goodput(16384, mtbf_s / m, 30.0, 300.0)[0]
                          for m in (1, r650["interrupt_mult_uecc_handled"], 3, 10, r650["interrupt_mult_uecc_crash"])}
    surv = {f"{afr:.2f}": [mean_surviving_fraction(afr, L) for L in (3, 5, 7)] for afr in (0.02, 0.05, 0.1, 0.2)}
    out["surviving"] = surv
    print("  mean surviving capacity over 3/5/7 yr without repair:", {k: [round(x, 3) for x in v] for k, v in surv.items()})

    # ---- comms
    print("\n== 5. Communications ==")
    inf = inference_downlink_gbps_per_MW()
    ar = dp_allreduce_tbps(1e12, 10.0)
    out["comms"] = dict(inference_gbps_per_MW=inf, allreduce_tbps_1T_10s=ar,
                        rx_frac={str(int(L)): optical_link_rx_fraction(0.1, 0.1, L) for L in (200, 1e3, 1e4, 1e5, 1e6, 5e6)},
                        near_field_m=0.1**2 / 1.55e-6)
    print(f"  text inference to ground: {inf:.2f} Gbps per MW (incl. 10x overhead) -> 1 GW = {inf:.2f} Tbps")
    print(f"  data-parallel all-reduce, 1T params, 10 s step: {ar:.1f} Tbps per cut")
    print("  10-cm to 10-cm optical link captured fraction:", {k: f"{v:.1e}" for k, v in out["comms"]["rx_frac"].items()})

    # ---- scale
    print("\n== 8. Scale ==")
    sc = {}
    for name in out["scenarios"]:
        n = out["scenarios"][name]["node"]
        k = n["kg_per_kW"]
        sc[name] = {str(G): dict(scale_numbers(k, G), area_km2=(n["A_array_m2"] + n["A_rad_m2"]) * G * 1e6 / 1e6)
                    for G in (0.1, 1, 10, 100)}
        print(f"  [{name}] 1 GW = {sc[name]['1']['mass_t']:,.0f} t = {sc[name]['1']['launches']:,.0f} launches of 100 t,"
              f" {sc[name]['1']['area_km2']:.1f} km^2 of array+radiator; 100 GW = {sc[name]['100']['launches']:,.0f} launches")
    out["scale"] = sc

    # ---- Monte Carlo
    print("\n== 7. Monte Carlo (n=%d) ==" % n_mc)
    mc = monte_carlo(env, sun, n=n_mc)
    q = np.percentile(mc["ratio"], [5, 25, 50, 75, 95])
    hist, edges = np.histogram(np.log10(mc["ratio"]), bins=60)
    out["mc"] = dict(n=n_mc, percentiles=dict(zip(["p5", "p25", "p50", "p75", "p95"], q.tolist())),
                     p_space_cheaper=float((mc["ratio"] < 1).mean()), spearman=mc["spearman"],
                     hist=hist.tolist(), hist_edges=edges.tolist(), ranges=MC_RANGES)
    for thr in (300, 150):
        m = mc["X"]["launch_usd_per_kg"] < thr
        out["mc"][f"p_space_cheaper_given_launch_lt_{thr}"] = float((mc["ratio"][m] < 1).mean())
    print("  space/ground cost ratio percentiles:", {k: round(v, 2) for k, v in out["mc"]["percentiles"].items()})
    print(f"  P(space cheaper) = {out['mc']['p_space_cheaper']:.3f};  given launch<$300/kg: "
          f"{out['mc']['p_space_cheaper_given_launch_lt_300']:.3f};  given launch<$150/kg: "
          f"{out['mc']['p_space_cheaper_given_launch_lt_150']:.3f}")
    print("  Spearman rank correlation with ratio:",
          {k: round(v, 2) for k, v in sorted(mc["spearman"].items(), key=lambda kv: -abs(kv[1]))})

    with open(os.path.join(HERE, "results.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)
    if make_figs:
        import figures
        figures.make_all(out, mc)
    return out, mc


if __name__ == "__main__":
    main()
