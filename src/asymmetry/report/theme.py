"""Shared design tokens for the published pages.

One source of truth for the palette, so the daily brief and the specification scan cannot
drift apart. The rule that drives the colour choices: in a trading interface **red and
green are data** — they mean direction and risk — so neither can also be the brand accent.
The accent is therefore a steel cyan used only for structure, never for a value.

Both themes are defined at token level: the media query handles the OS preference and the
explicit ``data-theme`` attributes let the viewer's toggle win in either direction.
"""

TOKENS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ground:#F4F6F8; --surface:#FFFFFF; --raised:#FAFBFC;
  --line:#DDE3EA; --line-strong:#C3CCD6;
  --text:#111820; --text-dim:#4B586A; --muted:#6E7C8E;
  --accent:#22697F; --accent-soft:#E2F0F5;
  --pos:#12805A; --neg:#C0392F; --caution:#9A6A12;
  --pos-soft:#E3F4EC; --neg-soft:#FBE9E7; --caution-soft:#FAF0DC;
  --shadow:0 1px 2px rgba(16,26,38,.06),0 4px 16px rgba(16,26,38,.05);
  --sans:ui-sans-serif,-apple-system,"Segoe UI Variable Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Mono","SF Mono","Roboto Mono",Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#0A0D12; --surface:#121820; --raised:#182029;
  --line:#232D3A; --line-strong:#33404F;
  --text:#E6EBF2; --text-dim:#A2AEBF; --muted:#7C8899;
  --accent:#62B6D9; --accent-soft:#12303B;
  --pos:#3FBF87; --neg:#F0736B; --caution:#DFAA4B;
  --pos-soft:#0F2A20; --neg-soft:#2E1614; --caution-soft:#2B2211;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 20px rgba(0,0,0,.3);
}}
:root[data-theme="dark"]{
  --ground:#0A0D12; --surface:#121820; --raised:#182029;
  --line:#232D3A; --line-strong:#33404F;
  --text:#E6EBF2; --text-dim:#A2AEBF; --muted:#7C8899;
  --accent:#62B6D9; --accent-soft:#12303B;
  --pos:#3FBF87; --neg:#F0736B; --caution:#DFAA4B;
  --pos-soft:#0F2A20; --neg-soft:#2E1614; --caution-soft:#2B2211;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 20px rgba(0,0,0,.3);
}
:root[data-theme="light"]{
  --ground:#F4F6F8; --surface:#FFFFFF; --raised:#FAFBFC;
  --line:#DDE3EA; --line-strong:#C3CCD6;
  --text:#111820; --text-dim:#4B586A; --muted:#6E7C8E;
  --accent:#22697F; --accent-soft:#E2F0F5;
  --pos:#12805A; --neg:#C0392F; --caution:#9A6A12;
  --pos-soft:#E3F4EC; --neg-soft:#FBE9E7; --caution-soft:#FAF0DC;
  --shadow:0 1px 2px rgba(16,26,38,.06),0 4px 16px rgba(16,26,38,.05);
}
body{margin:0;background:var(--ground);color:var(--text);
  font-family:var(--sans);font-size:15px;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""
