# CSTR reference model

All calculation states use SI units: K, Pa, m³, s, mol/m³, J and W. Display adapters may convert to °C, bar and kg/h at the boundary.

## Reactions

The reference CSTR uses parallel irreversible first-order reactions:

`A → B` (desired), `r₁ = k₁ C_A`

`A → C` (side reaction), `r₂ = k₂ C_A`

Each rate coefficient follows Arrhenius kinetics, `kᵢ = k0ᵢ exp(-Eaᵢ / (R T))`. Consumption of A is `rA = -(r₁ + r₂)`.

## Dynamic balances

For a constant-volume well-mixed vessel with liquid volumetric feed `F`:

`dC_A/dt = F/V(C_A,in - C_A) - r₁ - r₂`

`dC_B/dt = -F/V C_B + r₁`

`dC_C/dt = -F/V C_C + r₂`

`ρ Cp V dT/dt = ρ Cp F (T_in - T) + (-ΔH₁ r₁ - ΔH₂ r₂)V + UA(T_cool - T)`

The cooling term is negative when coolant is colder than the contents. `UA` is expressed in W/K.

Reported metrics are conversion `X = 1 - C_A/C_A,in`, yield `Y_B = C_B/C_A,in`, and selectivity `S_B = C_B/(C_B+C_C)`; definitions are protected against a zero denominator.

## Known case

For an isothermal first-order CSTR with no side reaction, steady-state conversion must equal `X = kτ/(1+kτ)`, where `τ=V/F`. The physics tests assert this numerical solution against the analytical result, as well as zero-reaction and conservation limits.

## Assumptions and validation limit

This is a lumped, perfectly mixed, constant-density demonstrator. It does not model phase change, pressure dynamics, catalyst deactivation or relief systems. It is not a plant-validated process safety model.

