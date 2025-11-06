# Materials App - Material Calculation Engine

## Purpose

The **materials** app provides precise volumetric calculations for cement plugging operations. It converts wellbore geometry (hole sizes, casing IDs, depths) and slurry recipes into exact material requirements: cement sacks, water barrels, and additive quantities. All calculations follow oilfield standards and account for annular excess, displacement volumes, and squeeze factors.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       MATERIALS APP                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  INPUT:  Geometry + Recipe + Operation Type                     │
│           ↓                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │  material_engine.py                  │                       │
│  │  ├─> Capacity Calculators            │                       │
│  │  │   • cylinder_capacity_bbl_per_ft  │                       │
│  │  │   • annulus_capacity_bbl_per_ft   │                       │
│  │  ├─> Volume Calculators               │                       │
│  │  │   • balanced_plug_bbl             │                       │
│  │  │   • bridge_plug_cap_bbl           │                       │
│  │  │   • squeeze_bbl                   │                       │
│  │  └─> Sacks & Materials                │                       │
│  │      • sacks_from_bbl                │                       │
│  │      • compute_sacks                 │                       │
│  │      • water_bbl_from_sacks          │                       │
│  │      • additives_totals              │                       │
│  └──────────────────────────────────────┘                       │
│           ↓                                                       │
│  OUTPUT: Sacks, Water, Additives, Displacement Fluids           │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Inputs (From)
- **Kernel App** (`apps/kernel/services/policy_kernel.py`)
  - Step geometry: hole_d_in, casing_id_in, stinger_od_in, stinger_id_in
  - Intervals: top_ft, bottom_ft, interval_ft, cap_length_ft
  - Annular excess percentages
  - Slurry recipes: cement class, density, yield, water ratio, additives

### Outputs (To)
- **Back to Kernel** (enriched step.materials object)
  - `slurry`: total_bbl, ft3, sacks, water_bbl, additives
  - `fluids`: displacement_bbl, spacer_bbl
  - `explain`: calculation breakdown for transparency

### Used By
- **All cement-bearing steps** in kernel plan output
- **UI displays** showing material requirements per operation
- **Cost estimation** modules (future)

---

## Key Constants

```python
BBL_TO_FT3 = 5.6146  # Standard barrel to cubic feet conversion
```

**Standard Oilfield Capacity Coefficient:**
- `0.000971` - Converts diameter squared (in²) to bbl/ft
- Formula: `capacity = 0.000971 × diameter_in²`

---

## Key Methods

### 1. Capacity Calculations

#### **`cylinder_capacity_bbl_per_ft(diameter_in)`**
**Purpose:** Calculate the volumetric capacity of a cylinder (pipe/casing interior) per foot of length.

**Formula:**
```
capacity = 0.000971 × diameter²
```

**Logic:**
1. Validate diameter > 0
2. Square the diameter
3. Multiply by 0.000971 constant
4. Return barrels per foot

**Example:**
```python
# 5-1/2" casing, 4.778" ID
capacity = cylinder_capacity_bbl_per_ft(4.778)
# Returns: 0.0222 bbl/ft
```

**Use Cases:**
- Inside capacity of tubing/stinger for displacement calculations
- Production casing capacity for balanced plugs

---

#### **`annulus_capacity_bbl_per_ft(hole_diameter_in, pipe_od_in)`**
**Purpose:** Calculate the annular capacity between two concentric cylinders (e.g., hole vs pipe, casing vs tubing).

**Formula:**
```
capacity = 0.000971 × (hole_diameter² - pipe_od²)
```

**Logic:**
1. Validate hole_diameter > 0 and pipe_od ≥ 0
2. Calculate delta = hole² - pipe²
3. If delta ≤ 0, return 0.0 (pipe too large for hole)
4. Multiply delta by 0.000971
5. Return barrels per foot

**Example:**
```python
# 8.5" open hole, 2.875" tubing
capacity = annulus_capacity_bbl_per_ft(8.5, 2.875)
# Returns: 0.0620 bbl/ft
```

**Use Cases:**
- Cement volumes in annular space
- Casing-to-hole cement calculations
- Tubing-to-casing annulus

---

### 2. Volume Calculations by Operation Type

#### **`balanced_plug_bbl(interval_ft, annulus_cap_bbl_per_ft, pipe_id_cap_bbl_per_ft, annular_excess)`**
**Purpose:** Calculate cement volumes for a balanced plug operation (cement in annulus + inside pipe simultaneously).

**What is a Balanced Plug?**
A balanced plug places cement both inside the work string and in the annulus, creating a continuous cement barrier across the interval. The cement inside the pipe provides weight to balance the cement in the annulus, preventing uncontrolled fluid movement.

**Logic:**
1. **Calculate annular volume:**
   - `annular_bbl = interval_ft × annulus_cap × (1 + annular_excess)`
   - Excess accounts for washouts, irregular hole, safety margin
2. **Calculate inside pipe volume:**
   - `inside_bbl = interval_ft × pipe_id_cap`
   - No excess (pipe interior is predictable)
3. **Total volume:**
   - `total_bbl = annular_bbl + inside_bbl`
4. Return dictionary with breakdown

**Example:**
```python
# 100 ft interval, 0.05 bbl/ft annulus, 0.02 bbl/ft inside, 40% excess
result = balanced_plug_bbl(100, 0.05, 0.02, 0.4)
# Returns: {
#   'annular_bbl': 7.0,    # 100 × 0.05 × 1.4
#   'inside_bbl': 2.0,     # 100 × 0.02
#   'total_bbl': 9.0
# }
```

**Use Cases:**
- Deep production zone plugs
- Formation isolation plugs via balanced method

---

#### **`bridge_plug_cap_bbl(cap_length_ft, casing_id_in, stinger_od_in, annular_excess)`**
**Purpose:** Calculate cement volume for a cap placed above a bridge plug or CIBP (Cast Iron Bridge Plug).

**What is a Bridge Plug Cap?**
After setting a mechanical bridge plug (CIBP), regulations require a cement cap above it. The cap provides redundant isolation and prevents plug movement. Typical lengths: 20-100 ft.

**Logic:**
1. Calculate annular capacity between casing ID and stinger OD
2. Multiply by cap length
3. Apply annular excess factor
4. Return total barrels

**Formula:**
```
cap_bbl = cap_length_ft × annulus_capacity(casing_id, stinger_od) × (1 + excess)
```

**Example:**
```python
# 100 ft cap in 5.5" casing (4.778" ID), 2.875" stinger, 40% excess
result = bridge_plug_cap_bbl(100, 4.778, 2.875, 0.4)
# annular_cap = 0.000971 × (4.778² - 2.875²) = 0.0143 bbl/ft
# total = 100 × 0.0143 × 1.4 = 2.0 bbl
```

**Use Cases:**
- CIBP caps per TAC §3.14(g)(3)
- Cement above retrievable bridge plugs

---

#### **`squeeze_bbl(interval_ft, casing_id_in, stinger_od_in, squeeze_factor)`**
**Purpose:** Calculate cement volume for squeeze operations (forcing cement under pressure into perforations or voids).

**What is a Squeeze?**
A squeeze operation pumps cement at high pressure to fill perforations, fractures, or channeled annular space. The squeeze_factor accounts for cement entering formation pores and irregular spaces beyond the calculated annular volume.

**Logic:**
1. Calculate base annular capacity
2. Multiply by interval length
3. Apply squeeze_factor (typically 1.5x to 2.0x)
4. Return base and total volumes

**Formula:**
```
base_bbl = interval_ft × annulus_capacity(casing_id, stinger_od)
total_bbl = base_bbl × squeeze_factor
```

**Example:**
```python
# 50 ft perforation interval, 5.5" casing, 2.875" stinger, 1.5x squeeze
result = squeeze_bbl(50, 4.778, 2.875, 1.5)
# base = 50 × 0.0143 = 0.715 bbl
# total = 0.715 × 1.5 = 1.07 bbl
```

**Use Cases:**
- Perforation squeezes
- Channeled annulus repairs
- Lost circulation zones

---

### 3. Sacks & Materials Conversion

#### **`sacks_from_bbl(total_bbl, yield_ft3_per_sk, rounding)`**
**Purpose:** Convert cement volume (barrels) to number of sacks based on slurry yield.

**What is Slurry Yield?**
Yield is the volume of mixed slurry produced per sack of cement. Typical Class H: 1.18 ft³/sack. Yield varies with water ratio and additives.

**Logic:**
1. Convert barrels to cubic feet: `ft3 = total_bbl × 5.6146`
2. Calculate raw sacks: `raw = ft3 / yield_ft3_per_sk`
3. Apply rounding mode:
   - **"ceil"**: Round up (conservative, always sufficient)
   - **"floor"**: Round down (minimal cost)
   - **"nearest"**: Round to nearest (default, 0.5 rounds up)
4. Return integer sacks

**Example:**
```python
# 10 bbl cement, 1.18 ft³/sk yield, nearest rounding
sacks = sacks_from_bbl(10, 1.18, "nearest")
# ft3 = 10 × 5.6146 = 56.146
# raw = 56.146 / 1.18 = 47.58
# nearest = 48 sacks
```

**Rounding Strategies:**
- **ceil**: Field operations (never run short)
- **floor**: Cost optimization (exact calculations)
- **nearest**: Balanced approach (RegulAgent default)

---

#### **`water_bbl_from_sacks(sacks, water_gal_per_sk)`**
**Purpose:** Calculate water volume required to mix the cement.

**Logic:**
1. Multiply sacks by water gallons per sack
2. Convert gallons to barrels (÷ 42)
3. Return water barrels

**Formula:**
```
water_bbl = (sacks × water_gal_per_sk) / 42
```

**Example:**
```python
# 50 sacks, 5.2 gal/sack water ratio
water = water_bbl_from_sacks(50, 5.2)
# water = (50 × 5.2) / 42 = 6.19 bbl
```

**Use Cases:**
- Water truck dispatch planning
- Mixing calculations at wellsite

---

#### **`additives_totals(sacks, additives)`**
**Purpose:** Calculate total quantity of each additive required.

**Additives Format:**
```python
additives = [
    {"name": "Retarder", "rate": 0.5},   # lbs per sack
    {"name": "Fluid Loss", "rate": 0.3}
]
```

**Logic:**
1. For each additive in recipe:
2. Multiply rate (lbs/sack) by total sacks
3. Accumulate into dictionary keyed by additive name
4. Return totals dictionary

**Example:**
```python
additives = [
    {"name": "Retarder", "rate": 0.5},
    {"name": "Dispersant", "rate": 0.2}
]
totals = additives_totals(50, additives)
# Returns: {
#   "Retarder": 25.0,    # 50 × 0.5
#   "Dispersant": 10.0   # 50 × 0.2
# }
```

---

#### **`compute_sacks(total_bbl, recipe, rounding)`**
**Purpose:** Master function that computes sacks, water, and additives from volume and recipe.

**Parameters:**
- `total_bbl`: Cement volume needed
- `recipe`: SlurryRecipe dataclass (cement class, density, yield, water ratio, additives)
- `rounding`: "nearest" | "ceil" | "floor"

**Logic:**
1. Call `sacks_from_bbl()` to get sack count
2. Calculate ft³ from barrels
3. Call `water_bbl_from_sacks()` for water volume
4. Call `additives_totals()` for additive quantities
5. Build explain dictionary with recipe parameters
6. Return VolumeBreakdown dataclass

**Returns (VolumeBreakdown):**
```python
VolumeBreakdown(
    total_bbl=10.0,
    sacks=48,
    ft3=56.146,
    water_bbl=6.19,
    additives={"Retarder": 24.0, "Dispersant": 9.6},
    explain={
        "yield_ft3_per_sk": 1.18,
        "water_gal_per_sk": 5.2,
        "rounding_mode": "nearest"
    }
)
```

**Example:**
```python
recipe = SlurryRecipe(
    recipe_id="class_h_neat_15_8",
    cement_class="H",
    density_ppg=15.8,
    yield_ft3_per_sk=1.18,
    water_gal_per_sk=5.2,
    additives=[{"name": "Retarder", "rate": 0.5}]
)

result = compute_sacks(10.0, recipe, rounding="nearest")
# Returns complete VolumeBreakdown with all materials
```

**Use Cases:**
- Called by kernel for every cement-bearing step
- Provides complete materials list for purchasing/logistics

---

### 4. Fluid Calculations

#### **`spacer_bbl_for_interval(interval_ft, annulus_cap_bbl_per_ft, min_bbl, spacer_multiple, contact_minutes, pump_rate_bpm)`**
**Purpose:** Calculate spacer/preflush volume for squeeze operations.

**What is Spacer?**
Spacer is a chemical wash pumped ahead of cement to:
- Clean mud residue from casing walls
- Improve cement bonding
- Prevent mud contamination of cement

**Logic (takes maximum of three methods):**

1. **Minimum Volume Method:**
   - Always pump at least `min_bbl` (typically 5 bbl)
   
2. **Interval Coverage Method:**
   - `volume = spacer_multiple × interval_ft × annulus_cap`
   - Typical multiple: 1.5x (covers interval 1.5 times)
   
3. **Contact Time Method** (if provided):
   - `volume = contact_minutes × pump_rate_bpm`
   - Ensures minimum contact time at target depth
   - Example: 10 min contact @ 2 bpm = 20 bbl

4. Return the maximum of all methods

**Example:**
```python
# 100 ft interval, 0.05 bbl/ft annulus, 5 bbl min, 1.5x multiple
spacer = spacer_bbl_for_interval(100, 0.05, 5.0, 1.5)
# Method 1: 5.0 bbl (min)
# Method 2: 1.5 × 100 × 0.05 = 7.5 bbl
# Returns: 7.5 bbl (maximum)
```

**With Contact Time:**
```python
spacer = spacer_bbl_for_interval(100, 0.05, 5.0, 1.5, 
                                  contact_minutes=10, pump_rate_bpm=2)
# Method 1: 5.0 bbl
# Method 2: 7.5 bbl
# Method 3: 10 × 2 = 20 bbl
# Returns: 20 bbl (maximum)
```

---

#### **`balanced_displacement_bbl(interval_ft, pipe_id_cap_bbl_per_ft, margin_bbl)`**
**Purpose:** Calculate displacement fluid volume for balanced plug operations.

**What is Displacement?**
After pumping cement, displacement fluid (water or mud) is pumped to push cement out of the work string and into position. Too much displacement = cement pushed past target. Too little = cement left in pipe.

**Logic:**
1. Calculate volume inside work string for the interval
2. Add safety margin (typically 0-2 bbl)
3. Return total displacement

**Formula:**
```
displacement = (interval_ft × pipe_id_cap) + margin_bbl
```

**Example:**
```python
# 100 ft interval, 2.875" tubing ID (0.02 bbl/ft), 1 bbl margin
disp = balanced_displacement_bbl(100, 0.02, 1.0)
# Returns: (100 × 0.02) + 1.0 = 3.0 bbl
```

**Critical for:**
- Balanced plug placement accuracy
- Preventing over-displacement
- Ensuring full interval coverage

---

### 5. Advanced Calculations

#### **`integrate_annulus_over_segments(segments, annular_excess)`**
**Purpose:** Calculate total cement volume across multiple depth intervals with changing geometry (piecewise integration).

**When is this needed?**
- Open hole to cased hole transitions
- Multiple casing strings at different depths
- Varying hole sizes across formation changes

**Segments Format:**
```python
segments = [
    (top_ft, bottom_ft, hole_d_in, pipe_od_in),
    (2000, 2500, 8.5, 2.875),      # Open hole section
    (2500, 3000, 5.5, 2.875)       # Cased section
]
```

**Logic:**
1. For each segment:
   - Calculate length = bottom - top
   - Calculate annular capacity for that geometry
   - Calculate volume = length × capacity × (1 + excess)
2. Sum all segment volumes
3. Return total barrels

**Example:**
```python
segments = [
    (2000, 2500, 8.5, 2.875),  # 500 ft open hole
    (2500, 3000, 4.778, 2.875) # 500 ft cased hole
]

total = integrate_annulus_over_segments(segments, 0.4)
# Segment 1: 500 × 0.062 × 1.4 = 43.4 bbl
# Segment 2: 500 × 0.0143 × 1.4 = 10.0 bbl
# Returns: 53.4 bbl
```

**Use Cases:**
- Complex wellbore profiles
- Intermediate casing transitions
- Formation-specific geometry changes

---

## Data Classes

### **`SlurryRecipe`**
**Purpose:** Encapsulate cement slurry specifications.

**Fields:**
```python
@dataclass
class SlurryRecipe:
    recipe_id: str                # "class_h_neat_15_8"
    cement_class: str             # "H", "A", "G"
    density_ppg: float            # 15.8 (pounds per gallon)
    yield_ft3_per_sk: float       # 1.18 (cubic feet per sack)
    water_gal_per_sk: float       # 5.2 (gallons per sack)
    additives: List[Dict]         # [{"name": "Retarder", "rate": 0.5}]
```

**Common Recipes:**
- **Class H Neat, 15.8 ppg:** yield 1.18 ft³/sk, water 5.2 gal/sk
- **Class A, 15.6 ppg:** yield 1.15 ft³/sk, water 5.19 gal/sk
- **Class G, 15.8 ppg:** yield 1.15 ft³/sk, water 5.0 gal/sk

---

### **`VolumeBreakdown`**
**Purpose:** Complete material requirements for a cement operation.

**Fields:**
```python
@dataclass
class VolumeBreakdown:
    total_bbl: float              # 10.0
    sacks: int                    # 48
    ft3: float                    # 56.146
    water_bbl: float              # 6.19
    additives: Dict[str, float]   # {"Retarder": 24.0}
    explain: Dict[str, float]     # Recipe parameters used
```

---

## Testing

### Test Files:
- **`test_material_engine.py`** - Unit tests for all calculation methods
- **`test_material_engine_scenarios.py`** - Integration scenarios with realistic wellbore profiles

**Run tests:**
```bash
docker exec regulagent_web python manage.py test apps.materials.tests
```

**Coverage:**
- Capacity calculations (cylinder, annulus)
- Volume calculations (balanced, cap, squeeze)
- Sacks conversion with all rounding modes
- Water and additive calculations
- Edge cases (zero volumes, invalid inputs)
- Piecewise segment integration

---

## Integration Points

### Called By:
- **`apps/kernel/services/policy_kernel.py`** → `_compute_materials_for_steps()`
  - Imports all capacity and volume functions
  - Uses SlurryRecipe and compute_sacks for every cement step

### Provides To:
- **Kernel plan output** → `step.materials` object
- **UI displays** → Shopping list for materials
- **Cost estimation** → Pricing calculations (future)

---

## Common Calculations by Step Type

### Surface Casing Shoe Plug
```python
# Cased-hole annulus calculation
capacity = annulus_capacity_bbl_per_ft(casing_id_in=4.778, stinger_od_in=2.875)
# 100 ft interval, 40% excess, +10% per 1000 ft (TAC factor)
volume = 100 × capacity × 1.4 × 1.1  # TAC factor for 1 kft
sacks = sacks_from_bbl(volume, yield=1.18, rounding="nearest")
```

### CIBP Cap
```python
# 100 ft cap above bridge plug
result = bridge_plug_cap_bbl(
    cap_length_ft=100,
    casing_id_in=4.778,
    stinger_od_in=2.875,
    annular_excess=0.4
)
breakdown = compute_sacks(result['total_bbl'], recipe)
```

### Open-Hole Cement Plug
```python
# 200 ft plug in 8.5" open hole
capacity = annulus_capacity_bbl_per_ft(hole_diameter_in=8.5, pipe_od_in=2.875)
volume = 200 × capacity × 2.0  # 100% excess for open hole
```

### Perforation Squeeze
```python
# 50 ft perforated interval, 1.5x squeeze factor
result = squeeze_bbl(
    interval_ft=50,
    casing_id_in=4.778,
    stinger_od_in=2.875,
    squeeze_factor=1.5
)
# Optional spacer
spacer = spacer_bbl_for_interval(50, capacity, min_bbl=5.0, spacer_multiple=1.5)
```

---

## Error Handling

All functions validate inputs and raise `ValueError` for:
- Negative or zero dimensions
- Pipe OD larger than hole/casing ID
- Invalid intervals (bottom > top)
- Negative volumes or factors

**Example:**
```python
try:
    capacity = annulus_capacity_bbl_per_ft(5.5, 6.0)  # Pipe too large!
except ValueError as e:
    # Returns 0.0 (clamped), no exception raised
    # But delta ≤ 0 check returns 0.0
```

---

## File Structure

```
apps/materials/
├── services/
│   ├── __init__.py
│   └── material_engine.py    # All calculation methods
└── tests/
    ├── test_material_engine.py
    └── test_material_engine_scenarios.py
```

---

## Key Formulas Reference

### Capacity
```
Cylinder:  0.000971 × diameter²
Annulus:   0.000971 × (outer² - inner²)
```

### Volume Conversions
```
1 barrel = 42 gallons = 5.6146 cubic feet
1 sack cement ≈ 94 lbs (1 cubic foot)
```

### Excess Factors
```
Open hole:     100% (2.0x multiplier)
Cased, long:   100% (interval ≥ 200 ft)
Cased, short:   50% (interval < 200 ft)
Squeeze:       50-100% (via squeeze_factor 1.5-2.0x)
```

### TAC §3.14(d)(11) Excess
```
multiplier = 1 + (0.1 × ceil(interval_ft / 1000))
Example: 2500 ft → 3 kft units → 1.3x
```

---

## Example: Complete Shoe Plug Calculation

```python
from apps.materials.services.material_engine import (
    annulus_capacity_bbl_per_ft,
    compute_sacks,
    SlurryRecipe
)

# 1. Define geometry
casing_id = 4.778    # 5-1/2" casing, 15.5 ppf
stinger_od = 2.875   # 2-7/8" tubing
interval_ft = 100    # ±50 ft around shoe

# 2. Calculate capacity
capacity = annulus_capacity_bbl_per_ft(casing_id, stinger_od)
# Returns: 0.0143 bbl/ft

# 3. Apply excess (40% for cased hole)
excess = 0.4
volume_base = interval_ft * capacity * (1 + excess)
# = 100 × 0.0143 × 1.4 = 2.0 bbl

# 4. Apply TAC factor (+10% per 1000 ft)
kft_units = int((interval_ft + 999) // 1000)  # = 1
tac_factor = 1.0 + (0.1 * kft_units)  # = 1.1
volume_total = volume_base * tac_factor
# = 2.0 × 1.1 = 2.2 bbl

# 5. Define recipe
recipe = SlurryRecipe(
    recipe_id="class_h_neat_15_8",
    cement_class="H",
    density_ppg=15.8,
    yield_ft3_per_sk=1.18,
    water_gal_per_sk=5.2,
    additives=[{"name": "Retarder", "rate": 0.5}]
)

# 6. Compute materials
breakdown = compute_sacks(volume_total, recipe, rounding="nearest")

# 7. Results
print(f"Sacks: {breakdown.sacks}")           # 11
print(f"Water: {breakdown.water_bbl:.2f}")   # 1.36 bbl
print(f"Retarder: {breakdown.additives.get('Retarder')}")  # 5.5 lbs
```

---

## Future Enhancements

1. **Recipe Library** - Database of common slurry designs by region/formation
2. **Cost Integration** - Real-time pricing from vendor APIs
3. **Optimization Engine** - Suggest recipe to minimize cost while meeting specs
4. **Batch Mixing** - Split large jobs into multiple cement unit loads
5. **Quality Control** - Flag unusual sack counts or water ratios

---

## Maintenance Notes

- **Add new operation types:** Create new volume calculator function
- **Update standard recipes:** Modify SlurryRecipe defaults in kernel
- **Change excess factors:** Update policy YAML geometry_defaults
- **Industry coefficient updates:** Modify 0.000971 constant if standards change (unlikely)

---

## Questions / Support

For questions about material calculations:
1. Review test files for calculation examples
2. Verify geometry units (all dimensions in inches, depths in feet)
3. Check excess factors are appropriate for hole type
4. Confirm recipe yield matches cement supplier specs

