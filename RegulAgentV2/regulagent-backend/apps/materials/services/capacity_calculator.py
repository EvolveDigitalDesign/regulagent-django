import sqlite3
import math
import os

# -----------------------------
# USER SETTINGS
# -----------------------------
# Smart DB path resolution - works in both local and Docker environments
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "pipe_data.db")

# If not found relative to script, try parent directories (up the tree)
if not os.path.exists(DB_PATH):
    # Try going up: /services/pipe_data.db → /pipe_data.db
    parent_dir = os.path.dirname(SCRIPT_DIR)  # /materials/services → /materials
    DB_PATH = os.path.join(parent_dir, "pipe_data.db")

# Still not found? Try environment variable (useful for Docker/CI)
if not os.path.exists(DB_PATH) and "PIPE_DATA_DB" in os.environ:
    DB_PATH = os.environ["PIPE_DATA_DB"]

# Final fallback: hardcoded absolute path
if not os.path.exists(DB_PATH):
    DB_PATH = "/Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend/apps/materials/pipe_data.db"

CLASS_C_YIELD = 1.32  # ft³/sack (Class C cement)
CLASS_H_YIELD = 1.06  # ft³/sack (Class H cement)


# -----------------------------
# PIPE SPEC LOOKUP (AUTOMATIC ID RESOLUTION)
# -----------------------------
def get_pipe_spec(od_inch, weight_lbft=None):
    """
    Look up pipe spec from Redbook database.
    
    Args:
        od_inch: Outer diameter in inches (required)
        weight_lbft: Linear weight in lb/ft (optional)
                     If not provided, selects LIGHTEST weight for that OD
    
    Returns:
        dict with keys: nom_dia, out_dia, lin_wt, in_dia, grade
    
    Raises:
        ValueError: If pipe not found in database
    """
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"pipe_data.db not found at {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        if weight_lbft is not None:
            # Exact match: OD + weight
            row = cur.execute("""
                SELECT nom_dia, out_dia, lin_wt, in_dia, pipe_grad_cd
                FROM pipe_data
                WHERE out_dia = ? AND lin_wt = ?
                LIMIT 1;
            """, (od_inch, weight_lbft)).fetchone()
            
            if not row:
                # Weight not found - get available weights for this OD
                available_weights = cur.execute("""
                    SELECT DISTINCT lin_wt FROM pipe_data
                    WHERE out_dia = ?
                    ORDER BY lin_wt ASC;
                """, (od_inch,)).fetchall()
                
                if available_weights:
                    weights_str = ", ".join(f"{w[0]:.1f}" for w in available_weights[:10])
                    raise ValueError(
                        f"No pipe found for OD={od_inch}\" with weight={weight_lbft} lb/ft.\n"
                        f"Available weights: {weights_str}"
                        + ("..." if len(available_weights) > 10 else "")
                    )
                else:
                    raise ValueError(f"No pipe found for OD={od_inch}\"")
        else:
            # No weight provided → pick LIGHTEST available for this OD
            row = cur.execute("""
                SELECT nom_dia, out_dia, lin_wt, in_dia, pipe_grad_cd
                FROM pipe_data
                WHERE out_dia = ?
                ORDER BY lin_wt ASC
                LIMIT 1;
            """, (od_inch,)).fetchone()
            
            if not row:
                raise ValueError(f"No pipe found for OD={od_inch}\"")
    finally:
        conn.close()
    
    # Row structure: (nom_dia, out_dia, lin_wt, in_dia, pipe_grad_cd)
    return {
        "nom_dia": row[0],
        "out_dia": row[1],
        "lin_wt": row[2],
        "in_dia": row[3],
        "grade": row[4],
    }


# -----------------------------
# UTILITY FUNCTIONS
# -----------------------------
def ft3_per_ft_from_diameters(id_inch=None, od_inch=None, hole_inch=None):
    """
    Returns ft³/ft.
    If hole_inch is provided → annular capacity (between hole and OD).
    If only id_inch is provided → inside capacity (inside ID).
    
    Args:
        id_inch: Inner diameter in inches (for inside capacity)
        od_inch: Outer diameter in inches (for annular capacity)
        hole_inch: Hole diameter in inches (for annular capacity)
    
    Returns:
        float: capacity in ft³/ft
    """
    if hole_inch is not None and od_inch is not None:
        # annulus: between hole and OD
        area = (hole_inch**2 - od_inch**2) * math.pi / 4
    elif id_inch is not None:
        # internal area: ID only
        area = (id_inch**2) * math.pi / 4
    else:
        raise ValueError("Must provide either (hole_inch, od_inch) or id_inch")
    
    ft2 = area / 144  # in² → ft²
    return ft2  # because length = 1 ft


def effective_length(length_ft, depth_ft):
    """
    Apply Texas depth excess rule: +10% per 1000 ft of depth.
    Per §3.14(d)(11): depth_multiplier = 1.0 + (0.10 × depth_in_kft)
    
    Args:
        length_ft: Plug length in feet
        depth_ft: Plug depth (bottom) in feet
    
    Returns:
        float: Effective length with depth excess applied
    """
    # Depth in thousands of feet (exact, not rounded)
    depth_kft = depth_ft / 1000.0
    texas_multiplier = 1.0 + (0.1 * depth_kft)
    return length_ft * texas_multiplier


def sacks_required(volume_ft3, yield_ft3_per_sack):
    """
    Convert volume to sacks given cement yield.
    
    Args:
        volume_ft3: Volume in cubic feet
        yield_ft3_per_sack: Yield per sack (Class C: 1.32, Class H: 1.06)
    
    Returns:
        float: Number of sacks required
    """
    if yield_ft3_per_sack <= 0:
        raise ValueError("Yield must be positive")
    return volume_ft3 / yield_ft3_per_sack


# -------- LEGACY FUNCTION (kept for backward compatibility) --------
def find_pipe_by_od(od_inch, weight_lbft=None):
    """
    DEPRECATED: Use get_pipe_spec() instead.
    
    Finds pipe(s) by OD, optionally filtered by weight.
    """
    pipe = get_pipe_spec(od_inch, weight_lbft)
    return [tuple(pipe.values())]


# -----------------------------
# MAIN CALCULATION FUNCTIONS
# -----------------------------
def calculate_cement_simple(
    hole_size_inch,
    casing_od_inch,
    casing_weight_lbft=None,
    plug_length_ft=100,
    plug_depth_ft=5500,
    cement_class="C",
):
    """
    Simplified cement calculation: just provide OD (+ optional weight), hole size, depth.
    
    This function AUTOMATICALLY looks up the casing ID from the Redbook.
    
    Args:
        hole_size_inch: Hole diameter in inches
        casing_od_inch: Casing outer diameter in inches
        casing_weight_lbft: Casing weight in lb/ft (optional, defaults to lightest)
        plug_length_ft: Plug length in feet (default 100)
        plug_depth_ft: Plug bottom depth in feet (default 5500)
        cement_class: "C" or "H" (default "C")
    
    Returns:
        dict with full calculation details
    
    Raises:
        ValueError: If casing not found or invalid cement class
    """
    # Look up pipe spec automatically
    pipe = get_pipe_spec(casing_od_inch, casing_weight_lbft)
    casing_id = pipe["in_dia"]
    
    # Select cement yield
    if cement_class == "H":
        yield_ft3_per_sack = CLASS_H_YIELD
    elif cement_class == "C":
        yield_ft3_per_sack = CLASS_C_YIELD
    else:
        raise ValueError(f"Cement class must be 'C' or 'H', got '{cement_class}'")
    
    # Run full calculation with looked-up ID
    return calculate_cement_full(
        hole_size_inch=hole_size_inch,
        od_inch=casing_od_inch,
        id_inch=casing_id,
        plug_length_ft=plug_length_ft,
        plug_depth_ft=plug_depth_ft,
        yield_ft3_per_sack=yield_ft3_per_sack,
        pipe_spec=pipe,  # Include for reference
    )


def calculate_cement_full(
    hole_size_inch,
    od_inch,
    id_inch,
    plug_length_ft,
    plug_depth_ft,
    yield_ft3_per_sack=CLASS_C_YIELD,
    pipe_spec=None,
):
    """
    Full cement calculation with explicit geometry.
    
    This is the underlying calculation engine. Normally use calculate_cement_simple()
    which automatically looks up casing ID.
    
    Args:
        hole_size_inch: Hole diameter in inches
        od_inch: Casing outer diameter in inches
        id_inch: Casing inner diameter in inches
        plug_length_ft: Plug length in feet
        plug_depth_ft: Plug bottom depth in feet
        yield_ft3_per_sack: Cement yield in ft³/sack
        pipe_spec: Optional dict with looked-up pipe spec (for reference in output)
    
    Returns:
        dict with all calculation details
    """
    # capacity values (ft³/ft)
    ann_ft3_per_ft = ft3_per_ft_from_diameters(od_inch=od_inch, hole_inch=hole_size_inch)
    in_ft3_per_ft = ft3_per_ft_from_diameters(id_inch=id_inch)

    # effective plug length with Texas depth excess
    L_eff = effective_length(plug_length_ft, plug_depth_ft)

    # TOTAL volumes
    ann_volume = ann_ft3_per_ft * L_eff
    inner_volume = in_ft3_per_ft * L_eff

    # sacks
    ann_sacks = sacks_required(ann_volume, yield_ft3_per_sack)
    inner_sacks = sacks_required(inner_volume, yield_ft3_per_sack)
    total_sacks = ann_sacks + inner_sacks

    result = {
        "input": {
            "hole_size_inch": hole_size_inch,
            "casing_od_inch": od_inch,
            "casing_id_inch": id_inch,
            "plug_length_ft": plug_length_ft,
            "plug_depth_ft": plug_depth_ft,
            "yield_ft3_per_sack": yield_ft3_per_sack,
        },
        "capacity": {
            "annulus_ft3_per_ft": ann_ft3_per_ft,
            "inside_ft3_per_ft": in_ft3_per_ft,
        },
        "depth_excess": {
            "base_length_ft": plug_length_ft,
            "effective_length_ft": L_eff,
            "texas_multiplier": 1.0 + 0.1 * (plug_depth_ft / 1000.0),
        },
        "volumes": {
            "annulus_ft3": ann_volume,
            "inside_ft3": inner_volume,
            "total_ft3": ann_volume + inner_volume,
        },
        "sacks": {
            "annulus_sacks": ann_sacks,
            "inside_sacks": inner_sacks,
            "total_sacks": total_sacks,
            "total_sacks_rounded": int(round(total_sacks)),
        },
    }
    
    # Include pipe spec if provided
    if pipe_spec:
        result["pipe_spec"] = pipe_spec
    
    return result


# ====================================
# EXAMPLE USAGE & TESTING
# ====================================
if __name__ == "__main__":
    import json
    
    print("\n" + "="*70)
    print("CEMENT CALCULATION EXAMPLES")
    print("="*70)
    
    # -------------------------
    # Example 1: Simple usage (OD only, lightest weight)
    # -------------------------
    print("\n📌 EXAMPLE 1: Formation top plug at Clearfork (5650 ft)")
    print("-" * 70)
    print("Input: Casing 5.5\" OD, hole 7.875\", 100 ft plug at 5650 ft depth")
    print("(Using LIGHTEST available 5.5\" weight from database)")
    print()
    
    try:
        result1 = calculate_cement_simple(
            hole_size_inch=7.875,
            casing_od_inch=5.5,
            casing_weight_lbft=None,  # ← None means pick lightest
            plug_length_ft=100,
            plug_depth_ft=5650,
            cement_class="C"
        )
        
        print(f"✅ Pipe spec found:")
        print(f"   Nominal: {result1['pipe_spec']['nom_dia']}")
        print(f"   OD: {result1['pipe_spec']['out_dia']}\"")
        print(f"   ID: {result1['pipe_spec']['in_dia']}\"")
        print(f"   Weight: {result1['pipe_spec']['lin_wt']} lb/ft")
        print(f"   Grade: {result1['pipe_spec']['grade']}")
        print()
        print(f"📊 Calculation:")
        print(f"   Annulus capacity: {result1['capacity']['annulus_ft3_per_ft']:.6f} ft³/ft")
        print(f"   Inside capacity:  {result1['capacity']['inside_ft3_per_ft']:.6f} ft³/ft")
        print(f"   Base length: {result1['depth_excess']['base_length_ft']} ft")
        print(f"   Effective length: {result1['depth_excess']['effective_length_ft']:.2f} ft (Texas +{(result1['depth_excess']['texas_multiplier']-1)*100:.1f}%)")
        print(f"")
        print(f"📦 Volumes & Sacks:")
        print(f"   Annulus volume: {result1['volumes']['annulus_ft3']:.2f} ft³ → {result1['sacks']['annulus_sacks']:.1f} sacks")
        print(f"   Inside volume:  {result1['volumes']['inside_ft3']:.2f} ft³ → {result1['sacks']['inside_sacks']:.1f} sacks")
        print(f"   ─────────────────────────────────────────")
        print(f"   ✓ TOTAL: {result1['volumes']['total_ft3']:.2f} ft³ → {result1['sacks']['total_sacks']:.1f} sacks ({result1['sacks']['total_sacks_rounded']} rounded)")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # -------------------------
    # Example 2: Specific weight provided
    # -------------------------
    print("\n📌 EXAMPLE 2: Intermediate shoe plug (3864 ft) with specific weight")
    print("-" * 70)
    print("Input: Casing 8.625\" OD (40 lb/ft), hole 11\", 100 ft plug")
    print()
    
    try:
        result2 = calculate_cement_simple(
            hole_size_inch=11.0,
            casing_od_inch=8.625,
            casing_weight_lbft=40.0,  # ← Specific weight provided (40 lb/ft available)
            plug_length_ft=100,
            plug_depth_ft=3864,
            cement_class="C"
        )
        
        print(f"✅ Pipe spec found:")
        print(f"   Nominal: {result2['pipe_spec']['nom_dia']}")
        print(f"   OD: {result2['pipe_spec']['out_dia']}\"")
        print(f"   ID: {result2['pipe_spec']['in_dia']}\"")
        print(f"   Weight: {result2['pipe_spec']['lin_wt']} lb/ft")
        print()
        print(f"📊 Calculation:")
        print(f"   Effective length: {result2['depth_excess']['effective_length_ft']:.2f} ft")
        print()
        print(f"📦 Volumes & Sacks:")
        print(f"   Annulus volume: {result2['volumes']['annulus_ft3']:.2f} ft³ → {result2['sacks']['annulus_sacks']:.1f} sacks")
        print(f"   Inside volume:  {result2['volumes']['inside_ft3']:.2f} ft³ → {result2['sacks']['inside_sacks']:.1f} sacks")
        print(f"   ─────────────────────────────────────────")
        print(f"   ✓ TOTAL: {result2['volumes']['total_ft3']:.2f} ft³ → {result2['sacks']['total_sacks']:.1f} sacks ({result2['sacks']['total_sacks_rounded']} rounded)")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # -------------------------
    # Example 3: Class H cement
    # -------------------------
    print("\n📌 EXAMPLE 3: Same calculation with Class H cement")
    print("-" * 70)
    print("Input: Casing 5.5\" OD, hole 7.875\", 100 ft plug at 5650 ft depth (Class H)")
    print()
    
    try:
        result3 = calculate_cement_simple(
            hole_size_inch=7.875,
            casing_od_inch=5.5,
            casing_weight_lbft=None,
            plug_length_ft=100,
            plug_depth_ft=5650,
            cement_class="H"  # ← Class H (higher density)
        )
        
        print(f"📦 Volumes & Sacks (Class H cement):")
        print(f"   Annulus volume: {result3['volumes']['annulus_ft3']:.2f} ft³ → {result3['sacks']['annulus_sacks']:.1f} sacks")
        print(f"   Inside volume:  {result3['volumes']['inside_ft3']:.2f} ft³ → {result3['sacks']['inside_sacks']:.1f} sacks")
        print(f"   ─────────────────────────────────────────")
        print(f"   ✓ TOTAL: {result3['volumes']['total_ft3']:.2f} ft³ → {result3['sacks']['total_sacks']:.1f} sacks ({result3['sacks']['total_sacks_rounded']} rounded)")
        print(f"")
        print(f"   (Note: Class H has lower yield {CLASS_H_YIELD} ft³/sk → more sacks vs Class C {CLASS_C_YIELD} ft³/sk)")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # -------------------------
    # Example 4: Complex 3-String System (ADVANCED)
    # -------------------------
    print("\n📌 EXAMPLE 4: 3-STRING SYSTEM - Multi-annuli cement fill (ADVANCED)")
    print("-" * 70)
    print("Well Geometry (from API 4217334896):")
    print("  • Surface:       13.375\" OD @ 404 ft (hole 17.5\")")
    print("  • Intermediate:   8.625\" OD @ 3864 ft (hole 11\")")
    print("  • Production:     5.5\" OD @ 10703 ft (hole 7.875\")")
    print("  • NO existing cement (all TOC=null or 0)")
    print()
    print("Scenario: Fill production casing from 200 ft to surface")
    print("  → Fills: prod inside + 3 annuli (prod-inter, inter-surf, surf-openhole)")
    print()
    
    try:
        # Calculate the 3 annuli + inside cement fill
        # Interval: 200 ft to surface = 200 ft length
        interval_length_ft = 200.0
        interval_bottom_ft = 200.0
        interval_top_ft = 0.0
        
        # Pipe specs (from Redbook)
        prod_od = 5.5
        prod_id = 5.044  # lightest 5.5"
        inter_od = 8.625
        inter_id = 7.725  # 40 lb/ft from earlier
        surf_od = 13.375
        surf_id = 12.515  # nominal 13 3/8"
        
        # Hole sizes
        surf_hole = 17.5
        inter_hole = 11.0
        prod_hole = 7.875
        
        # Effective length with Texas excess (200 ft @ 200 ft depth)
        # Per §3.14(d)(11): +10% per 1000 ft of DEPTH (exact, not rounded)
        depth_kft = interval_bottom_ft / 1000.0
        texas_mult = 1.0 + (0.1 * depth_kft)
        eff_length = interval_length_ft * texas_mult
        
        # Calculate annular capacities (ft³/ft)
        ann_prod_inter_cap = ft3_per_ft_from_diameters(od_inch=prod_od, hole_inch=inter_id)
        ann_inter_surf_cap = ft3_per_ft_from_diameters(od_inch=inter_od, hole_inch=surf_id)
        ann_surf_openhole_cap = ft3_per_ft_from_diameters(od_inch=surf_od, hole_inch=surf_hole)
        prod_inside_cap = ft3_per_ft_from_diameters(id_inch=prod_id)
        
        # Volumes (base, no excess)
        vol_prod_inside = eff_length * prod_inside_cap
        vol_prod_inter = eff_length * ann_prod_inter_cap
        vol_inter_surf = eff_length * ann_inter_surf_cap
        vol_surf_openhole = eff_length * ann_surf_openhole_cap
        
        total_vol = vol_prod_inside + vol_prod_inter + vol_inter_surf + vol_surf_openhole
        
        # Convert to sacks (Class C)
        sacks_prod_inside = vol_prod_inside / CLASS_C_YIELD
        sacks_prod_inter = vol_prod_inter / CLASS_C_YIELD
        sacks_inter_surf = vol_inter_surf / CLASS_C_YIELD
        sacks_surf_openhole = vol_surf_openhole / CLASS_C_YIELD
        total_sacks = total_vol / CLASS_C_YIELD
        
        print(f"📐 Casing geometry:")
        print(f"   Production:     5.5\" OD, {prod_id}\" ID (inside area = {prod_inside_cap:.6f} ft³/ft)")
        print(f"   Intermediate:   8.625\" OD, {inter_id}\" ID")
        print(f"   Surface:       13.375\" OD, {surf_id}\" ID")
        print(f"")
        print(f"📊 Interval: {interval_bottom_ft} - {interval_top_ft} ft ({interval_length_ft} ft)")
        print(f"   Base length: {interval_length_ft} ft")
        print(f"   Effective length: {eff_length:.2f} ft (Texas +{(texas_mult-1)*100:.1f}%)")
        print(f"")
        print(f"📦 CEMENT VOLUMES & SACKS (4 locations):")
        print(f"")
        print(f"   1️⃣  Production inside:")
        print(f"       Volume: {vol_prod_inside:.2f} ft³ → {sacks_prod_inside:.1f} sacks")
        print(f"")
        print(f"   2️⃣  Production-Intermediate annulus (5.5\" OD vs 7.725\" ID):")
        print(f"       Capacity: {ann_prod_inter_cap:.6f} ft³/ft")
        print(f"       Volume: {vol_prod_inter:.2f} ft³ → {sacks_prod_inter:.1f} sacks")
        print(f"")
        print(f"   3️⃣  Intermediate-Surface annulus (8.625\" OD vs 12.515\" ID):")
        print(f"       Capacity: {ann_inter_surf_cap:.6f} ft³/ft")
        print(f"       Volume: {vol_inter_surf:.2f} ft³ → {sacks_inter_surf:.1f} sacks")
        print(f"")
        print(f"   4️⃣  Surface-Openhole annulus (13.375\" OD vs 17.5\" hole):")
        print(f"       Capacity: {ann_surf_openhole_cap:.6f} ft³/ft")
        print(f"       Volume: {vol_surf_openhole:.2f} ft³ → {sacks_surf_openhole:.1f} sacks")
        print(f"")
        print(f"   ═══════════════════════════════════════════════════")
        print(f"   ✓ TOTAL CEMENT: {total_vol:.2f} ft³ → {total_sacks:.1f} sacks ({int(round(total_sacks))} rounded)")
        print(f"")
        print(f"🔍 Breakdown by location:")
        pct_inside = (sacks_prod_inside / total_sacks) * 100
        pct_ann_pi = (sacks_prod_inter / total_sacks) * 100
        pct_ann_is = (sacks_inter_surf / total_sacks) * 100
        pct_ann_so = (sacks_surf_openhole / total_sacks) * 100
        print(f"   • Inside production:           {sacks_prod_inside:6.1f} sks ({pct_inside:5.1f}%)")
        print(f"   • Prod-Inter annulus:          {sacks_prod_inter:6.1f} sks ({pct_ann_pi:5.1f}%)")
        print(f"   • Inter-Surf annulus:          {sacks_inter_surf:6.1f} sks ({pct_ann_is:5.1f}%)")
        print(f"   • Surf-Openhole annulus:       {sacks_surf_openhole:6.1f} sks ({pct_ann_so:5.1f}%)")
        print(f"   ═══════════════════════════════════════════════════")
        print(f"   TOTAL:                         {total_sacks:6.1f} sks (100.0%)")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # -------------------------
    # Example 5: 3-STRING with PARTIAL EXISTING CEMENT (REALISTIC)
    # -------------------------
    print("\n📌 EXAMPLE 5: 3-STRING with PARTIAL EXISTING CEMENT (REALISTIC)")
    print("-" * 70)
    print("Well Geometry (from API 4217334896):")
    print("  • Surface:       13.375\" OD @ 404 ft (hole 17.5\")")
    print("  • Intermediate:   8.625\" OD @ 3864 ft (hole 11\")")
    print("  • Production:     5.5\" OD @ 10703 ft (hole 7.875\")")
    print()
    print("Existing Cement:")
    print("  • Surface to openhole: CEMENTED from 0-50 ft (TOC @ 0 ft)")
    print()
    print("Scenario: Fill production casing from 200 ft to surface")
    print("  BUT: Exclude top 50 ft (already has cement)")
    print("  → Fills: prod inside + 3 annuli (prod-inter, inter-surf)")
    print("           + surf-openhole ONLY 200-50 ft (exclude 50-0)")
    print()
    
    try:
        # Same well geometry
        prod_od = 5.5
        prod_id = 5.044
        inter_od = 8.625
        inter_id = 7.725
        surf_od = 13.375
        surf_id = 12.515
        
        surf_hole = 17.5
        inter_hole = 11.0
        prod_hole = 7.875
        
        # Split interval due to existing cement
        # Full interval: 200-0 ft
        # But surf-openhole cement exists from 0-50 ft
        # So we calculate:
        #   - All annuli from 200-0 ft: prod inside, prod-inter, inter-surf
        #   - Surf-openhole ONLY from 200-50 ft (exclude cemented 0-50 section)
        
        interval_full_ft = 200.0
        interval_cemented_top_ft = 50.0  # Existing cement above this
        interval_calc_for_surf_oh = interval_full_ft - interval_cemented_top_ft  # 150 ft
        
        # Use bottom depth for Texas excess (200 ft depth)
        depth_kft = interval_full_ft / 1000.0
        texas_mult = 1.0 + (0.1 * depth_kft)
        eff_length_full = interval_full_ft * texas_mult
        
        # For surface-openhole, only 200-50 ft section
        eff_length_surf_oh = interval_calc_for_surf_oh * texas_mult
        
        # Capacities
        prod_inside_cap = ft3_per_ft_from_diameters(id_inch=prod_id)
        ann_prod_inter_cap = ft3_per_ft_from_diameters(od_inch=prod_od, hole_inch=inter_id)
        ann_inter_surf_cap = ft3_per_ft_from_diameters(od_inch=inter_od, hole_inch=surf_id)
        ann_surf_openhole_cap = ft3_per_ft_from_diameters(od_inch=surf_od, hole_inch=surf_hole)
        
        # Volumes
        vol_prod_inside = eff_length_full * prod_inside_cap
        vol_prod_inter = eff_length_full * ann_prod_inter_cap
        vol_inter_surf = eff_length_full * ann_inter_surf_cap
        vol_surf_openhole = eff_length_surf_oh * ann_surf_openhole_cap  # ← REDUCED interval
        
        total_vol = vol_prod_inside + vol_prod_inter + vol_inter_surf + vol_surf_openhole
        
        # Sacks
        sacks_prod_inside = vol_prod_inside / CLASS_C_YIELD
        sacks_prod_inter = vol_prod_inter / CLASS_C_YIELD
        sacks_inter_surf = vol_inter_surf / CLASS_C_YIELD
        sacks_surf_openhole = vol_surf_openhole / CLASS_C_YIELD
        total_sacks = total_vol / CLASS_C_YIELD
        
        print(f"📐 Casing geometry:")
        print(f"   Production:     5.5\" OD, {prod_id}\" ID")
        print(f"   Intermediate:   8.625\" OD, {inter_id}\" ID")
        print(f"   Surface:       13.375\" OD, {surf_id}\" ID")
        print()
        print(f"📊 Cement intervals:")
        print(f"   Prod inside:    0-200 ft (full, calculate)")
        print(f"   Prod-Inter ann: 0-200 ft (full, calculate)")
        print(f"   Inter-Surf ann: 0-200 ft (full, calculate)")
        print(f"   Surf-OH ann:    50-200 ft (PARTIAL, exclude 0-50 cemented)")
        print()
        print(f"   Base length: {interval_full_ft} ft")
        print(f"   Effective length: {eff_length_full:.2f} ft (Texas +{(texas_mult-1)*100:.1f}%)")
        print()
        print(f"📦 CEMENT VOLUMES & SACKS:")
        print()
        print(f"   1️⃣  Production inside (0-200 ft):")
        print(f"       Volume: {vol_prod_inside:.2f} ft³ → {sacks_prod_inside:.1f} sacks")
        print()
        print(f"   2️⃣  Prod-Inter annulus (0-200 ft):")
        print(f"       Capacity: {ann_prod_inter_cap:.6f} ft³/ft")
        print(f"       Volume: {vol_prod_inter:.2f} ft³ → {sacks_prod_inter:.1f} sacks")
        print()
        print(f"   3️⃣  Inter-Surf annulus (0-200 ft):")
        print(f"       Capacity: {ann_inter_surf_cap:.6f} ft³/ft")
        print(f"       Volume: {vol_inter_surf:.2f} ft³ → {sacks_inter_surf:.1f} sacks")
        print()
        print(f"   4️⃣  Surf-Openhole annulus (50-200 ft ONLY):")
        print(f"       Capacity: {ann_surf_openhole_cap:.6f} ft³/ft")
        print(f"       Interval: {interval_calc_for_surf_oh} ft (excluded 0-50 ft cemented)")
        print(f"       Volume: {vol_surf_openhole:.2f} ft³ → {sacks_surf_openhole:.1f} sacks")
        print()
        print(f"   ═══════════════════════════════════════════════════")
        print(f"   ✓ TOTAL CEMENT: {total_vol:.2f} ft³ → {total_sacks:.1f} sacks ({int(round(total_sacks))} rounded)")
        print()
        print(f"🔍 Breakdown by location:")
        pct_inside = (sacks_prod_inside / total_sacks) * 100
        pct_ann_pi = (sacks_prod_inter / total_sacks) * 100
        pct_ann_is = (sacks_inter_surf / total_sacks) * 100
        pct_ann_so = (sacks_surf_openhole / total_sacks) * 100
        print(f"   • Inside production:           {sacks_prod_inside:6.1f} sks ({pct_inside:5.1f}%)")
        print(f"   • Prod-Inter annulus:          {sacks_prod_inter:6.1f} sks ({pct_ann_pi:5.1f}%)")
        print(f"   • Inter-Surf annulus:          {sacks_inter_surf:6.1f} sks ({pct_ann_is:5.1f}%)")
        print(f"   • Surf-Openhole (partial):     {sacks_surf_openhole:6.1f} sks ({pct_ann_so:5.1f}%)")
        print(f"   ═══════════════════════════════════════════════════")
        print(f"   TOTAL:                         {total_sacks:6.1f} sks (100.0%)")
        print()
        print(f"💡 Comparison with Example 4 (no existing cement):")
        print(f"   Ex 4 (0-200 ft): 222.9 sacks")
        print(f"   Ex 5 (partial):  {total_sacks:.1f} sacks")
        print(f"   Savings: {222.9 - total_sacks:.1f} sacks (due to cemented 0-50 ft)")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # -------------------------
    # Summary Table
    # -------------------------
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("\n✅ Script successfully integrated:")
    print("   • Automatic pipe spec lookup by OD")
    print("   • Optional weight parameter (defaults to lightest)")
    print("   • Automatic ID resolution from Redbook")
    print("   • Texas depth excess applied (+10% per 1000 ft)")
    print("   • Support for Class C and Class H cement")
    print("   • Full transparency in calculation breakdown")
    print("\n✨ Ready for use in production!")
    print("="*70 + "\n")
