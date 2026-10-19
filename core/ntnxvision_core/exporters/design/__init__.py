"""Report sections of the architecture diagrams, one module per diagram type.

`content._Builder.design_sections` imports ``design.<type id>`` when a registered type
(`diagrams.TYPES`) applies to the chapter being built, and calls its
``build(builder, chapter, dtype) -> bool``: the module adds the section (figure parts,
design summary, supporting tables) and returns whether it did. The modules import
`exporters.content` for its blocks, so they are imported at use, never at load.
"""
