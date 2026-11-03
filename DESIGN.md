# Design tokens

Theme: Sunset. The profile is a drive through London at dusk. A violet-indigo sky at the top fades to coral and gold at the horizon. Skyline silhouettes sit in deep crimson-indigo. Lit windows are gold.

## Colour

| Token | Value | Use |
|---|---|---|
| night | #0B0F19 | Page canvas, road, river wall |
| indigo | #2E1065 | Sky top, landmark silhouettes, unlit windows |
| deep | #4C0519 | Building fill, badge background |
| crimson-dark | #9F1239 | Building edge, dividers, mid-sky |
| crimson | #DC2626 | Warm sky band |
| rose | #F43F5E | Tail lights, aircraft light, landmark outlines |
| coral | #F97316 | Horizon glow, accent line, peak marker |
| coral-soft | #FB7185 | Secondary highlights |
| amber | #F59E0B | Lit window, road marking |
| gold | #FBBF24 | Brightest window, horizon, markers |
| violet | #7C3AED | Kerb gradient mid-stop |
| text | #F8FAFC | Headings and body on dark |
| muted | #A1A1AA | Captions and counts |

`assets/career-motorway-dark.svg` is the palette source. Its sky stops use indigo, crimson-dark and rose.

## Sky gradient

Stops from top to bottom: indigo 0, crimson-dark 0.5, coral 0.82, gold 1.

## Type

- Face: `-apple-system, "Segoe UI", Helvetica, Arial, sans-serif`.
- Name 40px weight 600. Section titles 16px weight 600. Labels 12px to 13px.

## Spacing and shape

- Base unit 4px. Canvas corner radius 6px.
- Skyline: 52 columns, 13px wide, 17px pitch. Seven windows per column, 7 by 4px.

## Motion

1. Ambient loops run 4 to 12 seconds. Buildings rise once in 0.7 seconds with a 7ms stagger.
2. Animate transform and opacity only. Connectors use right angles.
3. Every SVG ends with `@media (prefers-reduced-motion: reduce)` and stops all animation. The static state stays complete.

## Contrast

Text #F8FAFC on night and deep passes WCAG AA. Muted #A1A1AA on night passes for 12px text.

## Badges

Background deep #4C0519. Icon and label #F8FAFC. Shields badges use `4C0519` and `logoColor=F8FAFC`.
