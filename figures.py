"""Static figures (PNG) for the Markdown report. Called from space_dc_model.main()."""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")

# validated categorical palette (light surface) -- fixed order, never cycled
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE_RAMP = ["#86b6ef", "#3987e5", "#256abf", "#104281"]
SURF, INK, INK2, MUTED, GRID, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SCEN = {"today": ("Today (flight-proven, 2026 prices)", C[0]),
        "near_term": ("Near term (~2030)", C[1]),
        "optimistic": ("Optimistic (~2035)", C[2]),
        "advocate": ("Advocate assumptions", C[6])}

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": BASE, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10.5,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
    "legend.frameon": False, "legend.labelcolor": INK2, "lines.linewidth": 2, "lines.solid_capstyle": "round",
    "font.family": "DejaVu Sans",
})


def _save(fig, name):
    os.makedirs(FIG, exist_ok=True)
    fig.savefig(os.path.join(FIG, name), dpi=160, bbox_inches="tight")
    plt.close(fig)


def thermal(out):
    t = out["thermal"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    T = np.array(t["T"])
    ax.plot(T, t["q_ideal"], color=MUTED, lw=1.5, label="Ideal (no Earth/albedo load)")
    ax.plot(T, t["q_radial"], color=C[0], label="Radial (one face sees Earth)")
    ax.plot(T, t["q_along"], color=C[1], label="Along-track (both faces see the limb)")
    ax.axhline(t["array_W_m2_EOL"], color=C[2], lw=1.5)
    ax.text(262, t["array_W_m2_EOL"] + 25, f"Solar array electrical output, EOL ≈ {t['array_W_m2_EOL']:.0f} W/m²",
            color=INK2, fontsize=9)
    ax.axvspan(305, 335, color=C[0], alpha=0.07, lw=0)
    ax.text(306, 1900, "practical radiator\nband for GPUs", color=INK2, fontsize=9, va="top")
    ax.set_xlim(260, 400)
    ax.set_ylim(0, 2400)
    ax.set_xlabel("Radiator mean temperature (K)")
    ax.set_ylabel("Net heat rejected (W per m² of two-sided panel)")
    ax.set_title("Radiator heat rejection vs temperature, 650 km dawn-dusk orbit")
    ax.legend(loc="upper left", fontsize=9)
    _save(fig, "fig_thermal.png")


def mass(out):
    keys = [("m_array", "Solar array"), ("m_radiator", "Radiator + loop"), ("m_it", "Compute (IT)"),
            ("m_battery", "Battery"), ("m_shield", "Shielding"), ("m_struct", "Structure, harness")]
    names = [n for n in ("today", "near_term", "optimistic", "advocate") if n in out["scenarios"]]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    y = np.arange(len(names))[::-1]
    left = np.zeros(len(names))
    for i, (k, lab) in enumerate(keys):
        v = np.array([out["scenarios"][n]["node"][k] for n in names])
        ax.barh(y, v, left=left, height=0.5, color=C[i], label=lab, edgecolor=SURF, linewidth=2)
        left += v
    for yy, tot in zip(y, left):
        ax.text(tot + 0.6, yy, f"{tot:.1f} kg/kW", va="center", color=INK, fontsize=9.5)
    ax.set_yticks(y, [SCEN[n][0] for n in names])
    ax.set_xlabel("Mass per kW of IT power (kg/kW)")
    ax.set_xlim(0, max(left) * 1.22)
    ax.grid(axis="y", visible=False)
    ax.set_title("Mass budget per kW of compute")
    ax.legend(ncol=3, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.45, -0.2))
    _save(fig, "fig_mass.png")


def heat_pump(out):
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for i, (rk, v) in enumerate(sorted(out["heat_pump"].items(), key=lambda kv: float(kv[0]))):
        ax.plot(v["Th"], v["kg_per_kW"], color=BLUE_RAMP[i], label=f"{rk} kg/m² radiator")
        ax.plot(v["T_opt"], v["m_opt"], "o", ms=7, color=BLUE_RAMP[i], mec=SURF, mew=2)
    ax.set_xlabel("Heat-pump hot-side (radiator) temperature, K   [cold side = 320 K]")
    ax.set_ylabel("Total specific mass (kg/kW)")
    ax.set_title("Does a heat pump pay? Only if radiators are heavy")
    ax.legend(fontsize=9)
    _save(fig, "fig_heatpump.png")


def cost(out):
    names = [n for n in ("today", "near_term", "optimistic", "advocate") if n in out["scenarios"]]
    short = {"today": "Orbit: today", "near_term": "Orbit: ~2030", "optimistic": "Orbit: ~2035",
             "advocate": "Orbit: advocate"}
    labels = ["Ground (reference)"] + [short[n] for n in names]
    comps = [("IT", "Compute hardware"), ("facility", "Facility / spacecraft"), ("energy", "Electricity"),
             ("launch", "Launch"), ("opex", "Opex"), ("financing", "Financing during build")]
    rows = [out["scenarios"]["near_term"]["ground_parts"]]
    for n in names:
        p = dict(out["scenarios"][n]["space_parts"])
        p["facility"] = p.pop("spacecraft")
        rows.append(p)
    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    y = np.arange(len(rows))[::-1]
    left = np.zeros(len(rows))
    for i, (k, lab) in enumerate(comps):
        v = np.array([r.get(k, 0.0) for r in rows]) / 1000
        ax.barh(y, v, left=left, height=0.55, color=C[i], label=lab, edgecolor=SURF, linewidth=2)
        left += v
    for yy, tot in zip(y, left):
        ax.text(tot + 1.2, yy, f"${tot:.1f}k", va="center", color=INK, fontsize=9.5)
    ax.set_yticks(y, labels)
    ax.set_xscale("linear")
    ax.set_xlim(0, max(left) * 1.12)
    ax.set_xlabel("$ thousand per delivered IT-kW-year (levelised)")
    ax.grid(axis="y", visible=False)
    ax.set_title("What a kW-year of compute costs, by component")
    ax.legend(fontsize=8.5, ncol=3, loc="upper center", bbox_to_anchor=(0.42, -0.2))
    _save(fig, "fig_cost.png")


def ratio_launch(out):
    r = out["ratio_vs_launch"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for n in ("today", "near_term", "optimistic", "advocate"):
        if n in r:
            ax.plot(r["launch"], r[n], color=SCEN[n][1], label=SCEN[n][0])
    ax.axhline(1, color=INK2, lw=1)
    ax.text(2500, 0.93, "parity with a ground data center", color=INK2, fontsize=9, ha="right", va="top")
    for x, lab in ((3000, "Falcon 9\nprice"), (900, "Starship\ncost est.\n2026"), (200, "Suncatcher\nthreshold"),
                   (50, "advocate")):
        ax.axvline(x, color=GRID, lw=1)
        ax.text(x * 1.06, 9.3, lab, fontsize=8, color=MUTED, va="top")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(0.5, 10)
    ax.set_yticks([0.5, 1, 2, 3, 5, 10], ["0.5", "1", "2", "3", "5", "10"])
    ax.set_xlabel("Launch price to orbit ($/kg)")
    ax.set_ylabel("Space / ground cost per delivered kW-yr")
    ax.set_title("Cost ratio vs launch price")
    ax.legend(fontsize=8.5, loc="upper left", bbox_to_anchor=(0.0, 0.88))
    _save(fig, "fig_ratio_launch.png")


def breakeven_map(out):
    b = out["breakeven_map"]
    X, Y = np.meshgrid(b["sc_usd_per_kg"], b["kg_per_kW"])
    Z = np.array(b["launch"])
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bounds = [0.5, 50, 100, 200, 500, 1000, 2000, 1e7]
    cols = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#5598e7", "#256abf", "#104281"]
    cf = ax.contourf(X, Y, np.where(Z <= 0, np.nan, Z), levels=bounds, colors=cols)
    ax.contourf(X, Y, np.where(Z <= 0, 1, np.nan), levels=[0, 2], colors=["#f0efec"])
    cb = fig.colorbar(cf, ax=ax, ticks=bounds[:-1], pad=0.02)
    cb.ax.set_yticklabels(["$0", "$50", "$100", "$200", "$500", "$1000", "$2000+"], fontsize=8, color=INK2)
    cb.set_label("break-even launch price ($/kg)", color=INK2)
    cb.outline.set_visible(False)
    for n, mk in (("near_term", "o"), ("optimistic", "s"), ("advocate", "D")):
        sc = out["scenarios"][n]
        ax.plot(sc["econ"]["sc_usd_per_kg"], sc["node"]["kg_per_kW"], mk, ms=8, color=SCEN[n][1], mec=SURF, mew=2)
        ax.annotate(SCEN[n][0].split(" (")[0], (sc["econ"]["sc_usd_per_kg"], sc["node"]["kg_per_kW"]),
                    xytext=(8, 4), textcoords="offset points", fontsize=8.5, color=INK)
    ax.text(1500, 40, "space never wins,\neven with free launch", color=INK2, fontsize=9, ha="center")
    ax.set_xscale("log")
    ax.set_xlabel("Spacecraft hardware cost, excluding compute ($/kg)")
    ax.set_ylabel("System specific mass (kg per kW IT)")
    ax.set_title("Launch price needed for parity (near-term reliability & ground costs)")
    ax.grid(False)
    _save(fig, "fig_breakeven_map.png")


def elec_breakeven(out):
    r = out["breakeven_elec_vs_launch"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for n in ("today", "near_term", "optimistic", "advocate"):
        if n in r:
            ax.plot(r["launch"], r[n], color=SCEN[n][1], label=SCEN[n][0])
    ax.axhspan(0.04, 0.12, color=C[2], alpha=0.10, lw=0)
    ax.text(12, 0.075, "today's industrial / PPA / on-site gas range", color=INK2, fontsize=8.5, va="center")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Launch price to orbit ($/kg)")
    ax.set_ylabel("Ground electricity price for parity ($/kWh)")
    ax.set_title("How expensive must ground power get before orbit wins?")
    ax.legend(fontsize=8.5, loc="upper left")
    _save(fig, "fig_elec_breakeven.png")


def mc_fig(out, mc):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.8), gridspec_kw=dict(width_ratios=[1.1, 1]))
    lr = np.log10(mc["ratio"])
    a1.hist(lr, bins=60, color=C[0], edgecolor=SURF, linewidth=0.6)
    a1.axvline(0, color=INK2, lw=1)
    ticks = [0.5, 1, 2, 3, 5, 10]
    a1.set_xticks(np.log10(ticks), [str(t) for t in ticks])
    a1.set_xlabel("Space / ground cost ratio (log scale)")
    a1.set_ylabel("Monte Carlo samples")
    p = out["mc"]["percentiles"]
    a1.set_title(f"Median {p['p50']:.2f}x, 90% interval {p['p5']:.2f}–{p['p95']:.2f}x")
    a1.grid(axis="x", visible=False)
    s = sorted(out["mc"]["spearman"].items(), key=lambda kv: abs(kv[1]))
    a2.barh(range(len(s)), [v for _, v in s], color=[C[1] if v > 0 else C[0] for _, v in s], height=0.6)
    a2.set_yticks(range(len(s)), [k for k, _ in s], fontsize=8)
    a2.axvline(0, color=BASE, lw=1)
    a2.set_xlabel("Spearman rank correlation with cost ratio")
    a2.set_title("What drives the answer")
    a2.grid(axis="y", visible=False)
    fig.tight_layout()
    _save(fig, "fig_montecarlo.png")


def it_capex(out):
    r = out["ratio_vs_it_capex"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = np.array(r["it_capex"]) / 1000
    for n in ("today", "near_term", "optimistic", "advocate"):
        if n in r:
            ax.plot(x, r[n], color=SCEN[n][1], label=SCEN[n][0])
    ax.axhline(1, color=INK2, lw=1)
    ax.axvspan(24, 32, color=C[0], alpha=0.07, lw=0)
    ax.text(24.4, 12, "GB200/GB300\nNVL72 today", fontsize=8.5, color=INK2)
    ax.set_yscale("log")
    ax.set_ylim(0.4, 20)
    ax.set_yticks([0.5, 1, 2, 5, 10, 20], ["0.5", "1", "2", "5", "10", "20"])
    ax.set_xlabel("Compute hardware capex ($ thousand per kW of IT)")
    ax.set_ylabel("Space / ground cost ratio")
    ax.set_title("Chip price amplifies whichever side has cheaper infrastructure")
    ax.legend(fontsize=8.5, loc="upper right")
    _save(fig, "fig_it_capex.png")


def goodput(out):
    g = out["goodput"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for i, (k, v) in enumerate(g["curves"].items()):
        ax.plot(g["N"], v, color=BLUE_RAMP[i], label=f"interrupt rate ×{k} vs ground")
    ax.set_xscale("log")
    ax.set_ylim(0, 1)
    ax.set_xlabel("GPUs in one synchronous job")
    ax.set_ylabel("Goodput (useful fraction of time)")
    ax.set_title("Checkpoint/restart goodput (Young–Daly optimum)")
    ax.legend(fontsize=9, loc="lower left")
    _save(fig, "fig_goodput.png")


def dose(out):
    if "radiation" not in out:
        return
    r = out["radiation"]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for i, (alt, v) in enumerate(r["dose_depth"].items()):
        ax.plot(r["depth_mm"], v, color=C[i], label=f"{alt} km SSO")
    ax.set_yscale("log")
    ax.set_xlabel("Aluminium shielding thickness (mm)")
    ax.set_ylabel("Ionising dose, rad(Si) per year")
    ax.set_title("Total ionising dose behind shielding")
    for y, lab in r.get("thresholds", []):
        ax.axhline(y, color=GRID, lw=1)
        ax.text(0.6, y * 1.1, lab, ha="left", fontsize=8, color=INK2)
    ax.legend(fontsize=9)
    _save(fig, "fig_dose.png")


def make_all(out, mc):
    thermal(out)
    mass(out)
    heat_pump(out)
    cost(out)
    ratio_launch(out)
    breakeven_map(out)
    elec_breakeven(out)
    mc_fig(out, mc)
    goodput(out)
    dose(out)
    it_capex(out)
