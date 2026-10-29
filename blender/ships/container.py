"""Container ship: a gearless feeder / Panamax-style box ship with the house aft.

Knobs are documented in catalog/ships/container.json ("param_docs").
"""

import random

from . import _merchant as M

TRI_BUDGET = 200_000
FEU = (12.192, 2.438, 2.591)  # 40 ft box: length, width, height (high cube is 2.896)
BAY_GAP = 1.1                 # lashing gap between 40 ft bays
HATCH_H = 1.6                 # coaming + hatch cover above the main deck


def build(p, me3d):
    L, B = float(p["loa_m"]), float(p["beam_m"])
    if not (50 <= L <= 400):
        raise ValueError(f"loa_m {L} is outside 50..400 m for a container ship")
    form = M.merchant_form(p)
    hull = M.build_hull(me3d, p, form)
    M.build_forecastle(me3d, p, form, hull)
    house = M.build_house(me3d, p, form, hull, float(p["house_front_frac"]) * L)
    M.build_aft_deck(me3d, p, form, hull, (house["x_casing_back"] - L / 2) / 2)

    # breakwater just aft of the forecastle protects the forward stacks
    xb = form.fc_x - 1.5
    zb = form.deck_at(xb)
    me3d.MeshBuilder().box((xb, 0, zb + 3.0), (0.6, 2 * form.deck_half_breadth(xb) - 1.0, 6.0),
                           p["superstructure_color"]).to_object(
        "Deck__Breakwater", pivot=(xb, 0, zb), parent=hull)

    # bays from the house forward; cranes, if any, stand in widened gaps on the port side
    n_cranes = int(p["cranes"])
    bays, rows, tiers = int(p["bays"]), int(p["rows"]), int(p["tiers"])
    x0, x_end = house["x_front"] + 2.0, form.fc_x - 3.0
    crane_after = {round((k + 1) * bays / (n_cranes + 1)) - 1 for k in range(n_cranes)}
    layout, cranes_x, x = [], [], x0
    for i in range(bays):
        if x + FEU[0] > x_end:
            me3d.REPORT.warn(f"only {i} of {bays} bays fit between the house and the forecastle")
            break
        layout.append(x)
        x += FEU[0] + BAY_GAP
        if i in crane_after and len(cranes_x) < n_cranes:
            cranes_x.append(x + 2.0)
            x += 4.0
    palette = [me3d.material(c, f"box_{k}") for k, c in enumerate(p["container_colors"])]
    hatch_m = me3d.material(p["hatch_color"], "hatch_cover")
    rng = random.Random(int(p["seed"]))
    var = float(p["stack_variation"])
    for i, xa in enumerate(layout, start=1):
        xc = xa + FEU[0] / 2
        half = min(form.deck_half_breadth(xa), form.deck_half_breadth(xa + FEU[0]))
        r_here = max(1, min(rows, int((2 * half - 1.4) // FEU[1])))
        zd = max(form.deck_at(xa), form.deck_at(xa + FEU[0]))
        width = r_here * FEU[1]
        hm = me3d.MeshBuilder()
        hm.box_minmax((xa - 0.2, -width / 2 - 0.3, zd - 0.2), (xa + FEU[0] + 0.2, width / 2 + 0.3,
                                                                zd + HATCH_H), hatch_m)
        hatch = hm.to_object(f"Cargo__Hatch_{i:02d}", pivot=(xc, 0, zd), parent=hull)
        if tiers <= 0:
            continue
        cm = me3d.MeshBuilder()
        for r in range(r_here):
            yc = -width / 2 + (r + 0.5) * FEU[1]
            h = tiers - (1 if rng.random() < var else 0) - (1 if rng.random() < var / 3 else 0)
            for t in range(max(0, h)):
                zc = zd + HATCH_H + (t + 0.5) * FEU[2]
                cm.box((xc, yc, zc), (FEU[0] - 0.08, FEU[1] - 0.06, FEU[2] - 0.05),
                       palette[rng.randrange(len(palette))])
        if cm.faces:
            cm.to_object(f"Cargo__Bay_{i:02d}", pivot=(xc, 0, zd + HATCH_H), parent=hatch)

    # With no bays (a cleared deck) the cranes stand on the deck amidships instead of the first hatch.
    stack_top = form.deck_at(layout[0] if layout else 0.0) + HATCH_H + tiers * FEU[2]
    for k, xcr in enumerate(cranes_x, start=1):
        y = B / 2 - 3.5
        zd = form.deck_at(xcr)
        M.build_crane(me3d, f"Deck__Crane_{k}", xcr, y, zd, stack_top - zd + 2.5, 0.14 * L,
                      p["crane_color"], hull, jib_dir=-1)
