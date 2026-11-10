# Nutanix product icons

From the Nutanix product icon set (`Nutanix Product Icons 2026-09-24 05_12_15/`, kept outside this
repo): the colour, icon-only variant of each product
(`Product-Icons-Digital-<Product>-Color.svg`), with the viewBox cropped to a square around the drawing. Licence:
Nutanix brand assets, for use in Nutanix-related tooling under the Nutanix brand guidelines; not for redistribution
outside the product.

**Generated — do not edit by hand.** Run, from the repo root:

```bash
core/.venv/bin/python scripts/render-product-icons.py "<Nutanix Product Icons folder>"
```

These are the same SVG files as `core/ntnxvision_core/exporters/icons/<id>.svg` (the script writes both, plus the
core's PNGs). Ids: `prism`, `nci`, `ncm`, `ndb`, `nus`, `nc2`, `nkp`, `nai`. Each file holds only filled `<path>`
elements in the original colours (ink `#131313`, purple `#7855FA`), drawn on white.

## How the app uses them

`src/renderer/views/diagrams/productIcons.tsx` bundles them (`import.meta.glob(..., { query: '?raw', eager: true })`),
parses each viewBox and its paths once, and draws them as React SVG elements (no innerHTML, no `data:` URL):

- the Diagrams canvas, for scene nodes of kind `glyph` (`data.product`): a product badge on a box, or the icon of a
  placed "Nutanix products" card;
- the Add picker's swatch for catalog icons with a `product` (on the swatch's white tile, so the ink stays visible in
  the dark theme).
