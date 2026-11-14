"""One profile per kind of model: what VR-Forces 5.1.1 needs for it.

Everything that used to branch on `domain` reads a profile instead, so supporting a new kind is a row here
plus a verification run (see `status`), not new code paths.

Sources. ACM = Adding Content to MAK ONE Applications reference manual; UG = VR-Forces 5.1.1 Users Guide;
"install" = the stock EntityLevel simulation model set and ModelData/Configuration/Definitions.
- Model format: own models are OpenFlight or FBX (ACM 14.2); the writer is the same for every kind.
- Origin: land = ground-contact plane at the yaw axis; air/space = centre of gravity; surface/subsurface =
  waterline at the centre of buoyancy (ACM 21.2.1, Table 43).
- Model definition schema: `Entity` for every stock model definition except ships and buoys (`Watercraft`,
  114 of 114 in Vehicles/Surface) and characters (`DiGuyCharacter`) (ACM 15.1; install).
- Platform file and DIS kind:domain per kind: counted in the install (the number after each row).
- Supports: where the model touches the ground (UG 66.1.3). Stock ships keep them on the waterline (144 of
  159); stock aircraft sit on their gear below the origin (down = 0 on only 4 of 190).
- Lifeforms are DI-Guy characters chosen in the .entity (ACM 25.2, UG 66.1.10): 156 of 160 humans, 10 of 10
  animals. They are not model imports.
- Buildings are cultural features: gravity aligned (UG 66.1.8), optionally embedded in the terrain with
  object geometry (UG 66.1.6-66.1.7).
- Simulated munitions have visual models and are configured like other objects (UG 66.5.1); a weapon can only
  fire what `ammunitionParams.mtl` lists, and damage comes from `detonationParams.mtl` (UG 73, 74.2.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    kind: str
    label: str
    dis_kind: int                 # first field of the 7-field DIS type
    dis_domain: int | None        # second field; None = any (munitions use it for the target domain)
    domain: str                   # legacy manifest `domain` value this kind stands for
    platform_ope: str
    category: str                 # folder under ModelData
    schema: str = "Entity"        # model definition schema
    route: str = "model"          # "model" = OpenFlight import; "diguy" = .entity picks a DI-Guy character
    origin: str = "ground"        # "ground" | "cg" | "waterline": what z = 0 means (ACM 21.2.1)
    supports: str = "ground"      # "ground" | "waterline" | "donor": how support points are set
    long_axis_forward: bool = True          # warn when the model is much wider than long
    entity_flags: dict = field(default_factory=dict)   # bool params forced in the .entity
    status: str = "untested"      # "verified" = compared with a hand import in the Simulation Object Editor


_P = Profile
PROFILES: dict[str, Profile] = {p.kind: p for p in (
    _P("ground_vehicle", "Ground vehicle", 1, 1, "land", "Ground_Vehicle.ope", "Vehicles/Wheeled"),            # 448
    _P("fixed_wing", "Fixed-wing aircraft", 1, 2, "air", "Fixed_Wing_Aircraft.ope", "Vehicles/FixedWing",
       origin="cg", long_axis_forward=False),                                                                   # 190
    _P("rotary_wing", "Helicopter", 1, 2, "air", "Rotary_Wing_Aircraft.ope", "Vehicles/RotaryWing",
       origin="cg", long_axis_forward=False),                                                                   # 83
    _P("surface_ship", "Surface ship", 1, 3, "surface", "Surface_Ship.ope", "Vehicles/Surface",
       schema="Watercraft", origin="waterline", supports="waterline", status="verified"),                       # 160
    _P("submarine", "Submarine", 1, 4, "subsurface", "Subsurface_Vehicle.ope", "Vehicles/Subsurface",
       origin="waterline", supports="donor"),                                                                   # 37
    _P("space_object", "Space object", 1, 5, "space", "Space_Object.ope", "Vehicles/Space",
       origin="cg", supports="donor", long_axis_forward=False),                                                 # 6
    _P("human", "Human", 3, 1, "lifeform", "Human.ope", "Lifeforms", schema="DiGuyCharacter", route="diguy",
       supports="donor", long_axis_forward=False),                                                              # 160
    _P("animal", "Animal", 3, 1, "lifeform", "Animal.ope", "Lifeforms/Animals", schema="DiGuyCharacter",
       route="diguy", supports="donor", long_axis_forward=False),                                               # 10
    _P("building", "Building", 5, 1, "land", "Cultural_Feature.ope", "Structures/Buildings",
       long_axis_forward=False, entity_flags={"gravity-aligned": True}),                                        # 88
    _P("static_object", "Static object", 5, 1, "land", "Cultural_Feature.ope", "Objects",
       long_axis_forward=False, entity_flags={"gravity-aligned": True}),
    _P("munition", "Munition", 2, None, "air", "Missile.ope", "Munitions/Missiles", origin="cg", supports="donor"),
)}

# munition_class -> (platform .ope, ModelData/Munitions subfolder); counts from the install
MUNITION_CLASSES = {
    "missile": ("Missile.ope", "Munitions/Missiles"),                    # 34
    "ballistic_missile": ("BallisticMissile.ope", "Munitions/Missiles"),  # 8
    "bomb": ("Bomb.ope", "Munitions/Bombs"),                             # 8
    "rocket": ("IndirectFire_Munition.ope", "Munitions/Rockets"),
    "round": ("IndirectFire_Munition.ope", "Munitions/Rounds"),          # 12 on this platform
    "torpedo": ("Subsurface_Vehicle.ope", "Munitions/Torpedo"),          # 2
    "naval_mine": ("Naval_Mine.ope", "Munitions/NavalMines"),            # 3
}

KIND_FOR_DOMAIN = {"land": "ground_vehicle", "air": "fixed_wing", "surface": "surface_ship",
                   "subsurface": "submarine", "space": "space_object", "lifeform": "human"}
KINDS = tuple(PROFILES)


class ProfileError(ValueError):
    pass


def kind_of(manifest: dict) -> str:
    """The manifest's kind; older manifests only have `domain`."""
    kind = manifest.get("kind")
    if kind:
        if kind not in PROFILES:
            raise ProfileError(f"kind must be one of {list(KINDS)}, got '{kind}'")
        return kind
    domain = manifest.get("domain")
    if domain not in KIND_FOR_DOMAIN:
        raise ProfileError(f"manifest needs 'kind' (one of {list(KINDS)})")
    return KIND_FOR_DOMAIN[domain]


def profile_for(manifest: dict) -> Profile:
    p = PROFILES[kind_of(manifest)]
    if p.kind == "munition":
        cls = manifest.get("munition_class") or "missile"
        if cls not in MUNITION_CLASSES:
            raise ProfileError(f"munition_class must be one of {sorted(MUNITION_CLASSES)}, got '{cls}'")
        ope, category = MUNITION_CLASSES[cls]
        p = Profile(**{**p.__dict__, "platform_ope": ope, "category": category})
    return p


def apply_defaults(manifest: dict) -> dict:
    """Fill what the kind decides (`kind`, `domain`, platform, ModelData folder) where the manifest is silent.
    Values written in the manifest win, so an unusual model can still override its profile."""
    p = profile_for(manifest)
    manifest.setdefault("kind", p.kind)
    manifest.setdefault("domain", p.domain)
    manifest.setdefault("platform_ope", p.platform_ope)
    manifest.setdefault("model_category_dir", p.category)
    return manifest


def dis_type_problem(manifest: dict) -> str | None:
    """A DIS type whose kind or domain does not fit the profile, as a sentence, or None."""
    p = profile_for(manifest)
    try:
        fields = [int(x) for x in str(manifest.get("dis_type", "")).split(":")]
    except ValueError:
        return None
    if len(fields) != 7:
        return None
    if fields[0] != p.dis_kind:
        return f"DIS type {manifest['dis_type']} has kind {fields[0]}, but a {p.label.lower()} is kind {p.dis_kind}"
    if p.dis_domain is not None and fields[1] != p.dis_domain:
        return f"DIS type {manifest['dis_type']} has domain {fields[1]}, but a {p.label.lower()} is domain {p.dis_domain}"
    return None
