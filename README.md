# Space Data Center Feasibility

A first-principles model of orbital AI data centers, and a report on whether they are feasible and profitable (October 2026).

The model covers:
- power balance;
- radiative cooling;
- radiation-induced computation errors;
- communications bandwidth;
- materials lifetime;
- levelised cost against ground data centers.

Everything is normalised to **one kilowatt of IT power** and solved numerically. Industry claims are checked against the physics rather than taken at face value.

- **Interactive report:** [`space-dc-feasibility.html`](space-dc-feasibility.html). Open it locally in a browser; it has an animated orbit view, a cost calculator and interactive charts.
- **Full written report:** [`report.md`](report.md), with equations, tables, figures and sources.
- **Model:** [`space_dc_model.py`](space_dc_model.py). Running it prints all tables to [`model_output.txt`](model_output.txt) and writes [`results.json`](results.json) and [`figures/`](figures).

## Verdict

| Question | Answer |
|---|---|
| Power | **Favourable.** At 650 km in a dawn–dusk sun-synchronous orbit the arrays are sunlit 96% of the time, about 6× the yearly energy per m² of a good ground site. Only cheap silicon cells are affordable at scale. |
| Cooling | **Feasible.** A radiator at 320 K rejects ~740 W/m² after Earth's infrared load, so it needs only ~28% of the array's area. Its mass (6–15 kg/m²) is the real cost. |
| Communications | **Fine for inference** (~1 Gbps per MW). Training traffic must stay inside one spacecraft or a formation under 1 km across. |
| Computation errors | **Fine for inference.** For synchronous training it is an open problem: on 16k chips at 650 km, one silent data corruption every ~3.6 min and one uncorrectable memory error every ~9 min. Altitude is capped near 700 km for commercial HBM. |
| Materials, lifetime | Not limiting over 5 years at 600–700 km. Debris, collision avoidance and re-entry mass grow with scale. |
| **Profitability** | **No** through ~2030 (2–8× the ground cost). **Marginal** (±15%) around 2035 with $200/kg launch. **At best 15–25% cheaper** at today's chip prices, because chips are ~2/3 of the cost and orbit cannot reduce it. |

Cost per delivered kW-year of compute, orbit ÷ ground:

| Scenario | Specific mass | Launch | Satellite hardware | Orbit ÷ ground |
|---|---|---|---|---|
| Today (flight-proven parts, 2026 prices) | 50 kg/kW | $3,000/kg | $2,000/kg | 7.9× |
| ~2030 (early Starship, Starlink-style production) | 30 kg/kW | $600/kg | $800/kg | 2.0× |
| ~2035 (Google's $200/kg threshold met) | 18 kg/kW | $200/kg | $400/kg | 1.15× |
| Assumptions behind the boldest public claims | 9 kg/kW | $50/kg | $150/kg | 0.84× |

A Monte Carlo over a generous 2030s range of 13 inputs gives a median of 2.4×, a 90% interval of 1.4–4.7×, and <0.1% probability that orbit is cheaper.

## Reading this for an AI-infrastructure investment thesis

This section is written for analysts and agents who want to use the study inside a broader view that AI compute demand keeps rising fast. It is analysis, not investment advice.

**1. Demand growth helps orbit only through the price and availability of ground power.**
- Orbit replaces the ~34% of ground cost that is facility, electricity and operations. It leaves the chips (~66%) untouched, and makes them slightly dearer and less productive.
- So a demand boom does not, by itself, make orbital compute competitive. It matters only if it drives the *all-in delivered price of ground power* above the break-even threshold, or makes ground power unobtainable for long enough.
- Thresholds from the model:
  - at $200/kg launch: ~$0.84/kWh for the ~2030 design and ~$0.24/kWh for the ~2035 design;
  - against today's industrial/PPA range of ~$0.05–0.12/kWh, and behind-the-meter gas or solar-plus-storage at roughly $0.08–0.15/kWh.
- The **time-to-power** form of the argument is stronger. A $30k/kW chip idle for a year waiting for power costs ~$7.6k/kW in capital, equivalent to ~$0.87/kWh. If grid interconnection and turbine queues stretch to years while launch cadence scales, this is the channel through which a demand surge could favour orbit.

**2. Where value would accrue if orbital compute happens anyway.** The rank-correlation sensitivity identifies the cost levers:
- launch price (ρ = +0.64);
- spacecraft hardware cost per kg (ρ = +0.54);
- radiator areal mass (ρ = +0.25).

Electricity price barely registers (ρ = −0.07). The implications:
- **Chip and memory vendors gain in every scenario.** The chips are the same chips in orbit; space adds demand for qualified, radiation-tolerant and hotter-running parts.
- **Launch providers** hold the single biggest lever and serve every space business, not only compute. Their outcome does not hinge on orbital data centers working out.
- **Mass manufacturers of satellite power and thermal hardware** (arrays, radiators, buses at $100–300/kg) are the enabling bottleneck.
- **Pure-play orbital data center operators** face a capped upside: ≤15–25% cost advantage at today's chip prices, and ~40% only if chips also get ~3× cheaper per watt. They also face large up-front capital, no repair, 5-year hardware life, and unproven training reliability. Their near-term business cases rest on time-to-power, niche markets (processing Earth-observation data in orbit, defence and sovereign edge) or inference, not on cheaper compute in general.
- **Externalities are a policy risk at scale:**
  - re-entry mass: ~4,000–6,000 t/yr per GW;
  - debris and collision avoidance;
  - launch emissions.

**3. Signposts that would move the conclusion** (each is measurable):
1. A ≥100 kW compute satellite operating ≥1 year with published error, thermal and failure statistics.
2. Starship commercial prices below $300/kg.
3. Satellite power-and-thermal hardware sold below ~$300/kg at >60 W/kg system level.
4. Heavy-ion latchup data and in-orbit silent-corruption rates for a datacenter accelerator.
5. A frontier-scale training run done with asynchronous, fault-tolerant methods.
6. Ground delivered-power prices for new AI capacity approaching ~$0.25/kWh, or multi-year time-to-power becoming the norm.

**4. Machine-readable outputs.** [`results.json`](results.json) holds every number in the report:

| Key | Contents |
|---|---|
| `scenarios.<name>` | mass budget, cost parts, ratio, break-even launch price, break-even ground electricity price |
| `ratio_vs_launch` | cost ratio as a function of launch price |
| `breakeven_elec_vs_launch` | parity ground power price as a function of launch price |
| `ratio_vs_it_capex` | cost ratio as a function of chip price |
| `mc` | Monte Carlo percentiles and sensitivities |
| `radiation.rates` | error rates by altitude and shielding |
| `scale` | launches and mass per GW |

## Coupling to a demand or ground-power model

```python
import space_dc_model as m
from dataclasses import replace

env, sun = m.default_context()                 # 650 km dawn-dusk orbit
d, e, rel = m.scenarios()["near_term"]         # or "today", "optimistic", "advocate"

# cost ratio under a projected ground power price (and facility capex, chip price, ...)
r = m.evaluate(d, replace(e, elec_usd_per_kWh=0.20, fac_capex_usd_per_kW=20_000), env, sun, rel)
print(r["ratio"], r["space"], r["ground"])     # orbit/ground, $ per delivered IT-kW-year

print(m.breakeven_ground_power_price(d, e, env, sun, rel))   # $/kWh at which orbit = ground
print(m.breakeven_launch(d, e, env, sun, rel))               # $/kg at which orbit = ground (None = never)
```

## Running

```bash
pip install -r requirements.txt
python3 space_dc_model.py        # ~5 s: prints all tables, writes results.json and figures/
```

All inputs live in the `Design`, `Econ` and `Reliability` dataclasses, with their sources noted inline. `scenarios()` defines the four cases; `MC_RANGES` defines the Monte Carlo range.

## Caveats

- Many source figures came from secondary reporting because primary PDFs were unreachable during the study. They are marked † in `report.md`; check them before formal citation.
- The radiation dose model is a two-component fit to published data points (±3×), not a transport calculation. Error rates scale Google's Trillium TPU proton-beam results by dose.
- Micrometeoroid and debris fluxes are order-of-magnitude estimates. Orbital assembly, insurance and spectrum costs are treated coarsely.
