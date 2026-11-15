"""PBR texture baking for built ships (plan Step 2): procedural finishes, atlases and UVs.

``finishes`` maps materials to finishes and builds their Cycles node graphs; ``atlas`` groups faces
into Deck / Hull / Superstructure / Details atlases and lays out the shared ``BakeUV`` map. The CLI
that ties them together is ``blender/bake_textures.py``. The name mapping and page arithmetic are
plain Python (no ``bpy``) so they can be unit tested outside Blender.
"""
