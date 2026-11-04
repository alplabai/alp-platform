#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Alp Lab AB
"""Generate the SVG diagrams under docs/images/.

Run `python docs/images/gen_diagrams.py` after changing a diagram; the SVGs are
committed. Each SVG carries its own light/dark palette through
`prefers-color-scheme`, so it reads on both GitHub themes.
"""
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent

STYLE = """<style>
  .bg{fill:#ffffff;stroke:#d0d7de;stroke-width:1}
  .t{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:#1f2328}
  .q{fill:#59636e}
  .h{font-weight:600}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .box{fill:#ffffff;stroke:#8c959f;stroke-width:1.25}
  .acc{fill:#ddf4ff;stroke:#0969da;stroke-width:2}
  .own{fill:#ffffff;stroke:#0969da;stroke-width:1.25}
  .rec{fill:#f6f8fa;stroke:#8c959f;stroke-width:1;stroke-dasharray:4 3}
  .hw{fill:#dafbe1;stroke:#1a7f37;stroke-width:1.25}
  .warn{fill:#fff1e5;stroke:#bc4c00;stroke-width:1.5}
  .ln{stroke:#8c959f;stroke-width:1.25;fill:none}
  .lnacc{stroke:#0969da;stroke-width:1.5;fill:none}
  .band1{fill:#0969da;fill-opacity:.22;stroke:#0969da;stroke-width:1.25}
  .band2{fill:#bc4c00;fill-opacity:.16;stroke:#bc4c00;stroke-width:1.25}
  .grid{stroke:#d0d7de;stroke-width:1}
  .ah{fill:#8c959f}
  .ahacc{fill:#0969da}
  @media (prefers-color-scheme: dark){
    .bg{fill:#0d1117;stroke:#30363d}
    .t{fill:#e6edf3} .q{fill:#9198a1}
    .box{fill:#161b22;stroke:#656c76}
    .acc{fill:#0d2a4a;stroke:#4493f8}
    .own{fill:#161b22;stroke:#4493f8}
    .rec{fill:#151b23;stroke:#656c76}
    .hw{fill:#0f2e1a;stroke:#3fb950}
    .warn{fill:#341a00;stroke:#db6d28}
    .ln{stroke:#656c76} .lnacc{stroke:#4493f8}
    .band1{fill:#4493f8;stroke:#4493f8}
    .band2{fill:#db6d28;stroke:#db6d28}
    .grid{stroke:#30363d}
    .ah{fill:#656c76} .ahacc{fill:#4493f8}
  }
</style>"""

DEFS = """<defs>
  <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker>
  <marker id="aa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="ahacc" d="M0 0L10 5L0 10z"/></marker>
</defs>"""


def svg(w, h, title, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{escape(title)}">\n<title>{escape(title)}</title>\n{STYLE}\n{DEFS}\n'
            f'<rect class="bg" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12"/>\n'
            + "\n".join(body) + "\n</svg>\n")


def text(x, y, s, size=13, cls="", anchor="start"):
    return (f'<text class="t {cls}" x="{x}" y="{y}" font-size="{size}" '
            f'text-anchor="{anchor}">{escape(s)}</text>')


def rect(x, y, w, h, cls="box", rx=8):
    return f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/>'


def line(d, cls="ln", marker="a"):
    m = f' marker-end="url(#{marker})"' if marker else ""
    return f'<path class="{cls}" d="{d}"{m}/>'


# ---------------------------------------------------------------- 1. stack
def stack():
    W, b = 960, []
    b.append(text(24, 36, "One platform version pins a tested set of components", 18, "h"))
    b.append(text(24, 58, "Example: Alp Platform 27.2.0 (versions illustrative)", 13, "q"))
    # platform bar
    b += [rect(24, 76, 912, 56, "acc"),
          text(480, 101, "Alp Platform 27.2.0", 17, "h", "middle"),
          text(480, 121, "CalVer · platform.yaml · tag platform-v27.2.0", 12, "q", "middle")]
    # owned components
    b.append(text(24, 162, "OWNED — exact version pinned, each keeps its own SemVer", 11.5, "q h"))
    comps = [("alp-sdk", "0.18.0", "C API · metadata", "meta-alp-sdk layer"),
             ("tan-cli", "0.8.0", "build + flash CLI", "tan platform use"),
             ("alp-sdk-vscode", "0.8.0", "VS Code extension", "status bar + warnings"),
             ("gd32-bridge-firmware", "0.3.1", "supervisor bridge", "wire protocol 0.7"),
             ("cc3501e-bridge-firmware", "0.9.2", "Wi-Fi / BLE bridge", "GPE stamp > floor")]
    cw, gap, y0 = 168, 18, 174
    for i, (n, v, l1, l2) in enumerate(comps):
        x = 24 + i * (cw + gap)
        cx = x + cw / 2
        b += [rect(x, y0, cw, 104, "own"),
              text(cx, y0 + 26, n, 12.5 if len(n) < 20 else 11, "h", "middle"),
              text(cx, y0 + 50, v, 16, "h mono", "middle"),
              text(cx, y0 + 74, l1, 11.5, "q", "middle"),
              text(cx, y0 + 92, l2, 11.5, "q", "middle")]
    # recorded
    yr = 304
    b.append(text(24, yr, "RECORDED — come in through an owned component, cross-checked by the gate", 11.5, "q h"))
    recs = [("Zephyr", "v4.4.1"), ("hal_alif", "v2.3.0"), ("Yocto", "kirkstone · scarthgap"),
            ("Renesas BSP", "v6.30"), ("Renesas AI SDK", "7.1")]
    for i, (n, v) in enumerate(recs):
        x = 24 + i * (cw + gap)
        b += [rect(x, yr + 12, cw, 50, "rec"),
              text(x + cw / 2, yr + 33, n, 12, "h", "middle"),
              text(x + cw / 2, yr + 52, v, 11.5, "mono q", "middle")]
    # validated hw
    yh = 400
    b.append(text(24, yh, "VALIDATED HARDWARE — bench-run with exactly this set", 11.5, "q h"))
    b += [rect(24, yh + 12, 260, 44, "hw"),
          text(154, yh + 39, "E1M-AEN801  2626-R2", 13, "h mono", "middle"),
          text(300, yh + 39, "rev = EEPROM hw_rev form <board_datecode>-<rev key>, printed as on the PCB", 12, "q")]
    return svg(W, 476, "One platform version pins a tested set of components", b)


# ---------------------------------------------------------------- 2. version anatomy
def anatomy():
    W, b = 760, []
    b.append(text(24, 36, "Reading a platform version", 18, "h"))
    cw, x0, y = 26.4, 120, 118  # 44 px monospace advance is about 0.6 em
    b.append(f'<text class="t h mono" x="{x0}" y="{y}" font-size="44">27.2.1-rc1</text>')
    for lab, a, n in [("YY", 0, 2), ("M", 3, 1), ("MICRO", 5, 1), ("rcN", 6, 4)]:
        xa, xb = x0 + a * cw + 2, x0 + (a + n) * cw - 2
        b += [line(f"M{xa} {y + 14}H{xb}", "lnacc", None),
              text((xa + xb) / 2, y + 36, lab, 13, "h", "middle")]
    notes = [("YY", "27 = 2027"), ("M", "never zero-padded: 27.2, not 27.02"),
             ("MICRO", "fixes inside one line"), ("rcN", "sorts before the GA it leads to")]
    for i, (k, v) in enumerate(notes):
        yy = 200 + i * 24
        b += [text(120, yy, k, 13, "h mono"), text(190, yy, v, 13, "q")]
    b += [rect(24, 306, 712, 50, "rec"),
          text(40, 330, "Git tag", 12, "h"), text(110, 330, "platform-v27.2.1-rc1", 13, "mono"),
          text(40, 348, "Line", 12, "h"), text(110, 348, "27.2  — a bare line resolves to its newest GA MICRO", 12, "q")]
    return svg(W, 380, "Reading a platform version", b)


# ---------------------------------------------------------------- 3. release flow
def flow():
    W, b = 960, []
    b.append(text(24, 36, "Components release first; the platform tag comes last", 18, "h"))
    steps = [("1  Freeze", ["pick component", "versions"]),
             ("2  Manifest", ["platform.yaml on", "line/YY.M, tag -rc1"]),
             ("3  Manifest gate", ["releases exist, SDK", "bounds, GPE floor"]),
             ("4  Bench", ["HIL on every", "validated hw_rev"]),
             ("5  Promote", ["rc to GA, gate", "runs again"]),
             ("6  Tag + publish", ["platform-vYY.M.0", "release + assets"])]
    bw, gap, y0, bh = 136, 20, 70, 76
    xs = [24 + i * (bw + gap) for i in range(len(steps))]
    for i, (h, ls) in enumerate(steps):
        x = xs[i]
        cls = "acc" if i == 5 else ("warn" if i == 2 else "box")
        b += [rect(x, y0, bw, bh, cls), text(x + bw / 2, y0 + 26, h, 13, "h", "middle")]
        for j, l in enumerate(ls):
            b.append(text(x + bw / 2, y0 + 47 + j * 16, l, 11.5, "q", "middle"))
        if i:
            b.append(line(f"M{xs[i - 1] + bw} {y0 + bh / 2}H{x}"))
    # fail loop from gate and bench back to manifest
    yb = y0 + bh
    b += [line(f"M{xs[2] + bw / 2} {yb}V{yb + 40}H{xs[1] + bw / 2}V{yb + 4}"),
          line(f"M{xs[3] + bw / 2} {yb}V{yb + 40}H{xs[2] + bw / 2}", "ln", None),
          text((xs[1] + xs[3]) / 2 + bw / 2, yb + 58, "a failed check or bench run goes back to the manifest", 11.5, "q", "middle")]
    b += [rect(24, 236, 912, 52, "warn"),
          text(40, 258, "CC3501E: the gate refuses gpe_stamp ≤ gpe_floor.", 13, "h"),
          text(40, 277, "Flashing a GPE stamp at or below what a part has seen permanently bricks the bridge.", 12.5, "")]
    return svg(W, 312, "Components release first; the platform tag comes last", b)


# ---------------------------------------------------------------- 4. how tools consume it
def consumers():
    W, b = 960, []
    b.append(text(24, 36, "One pin in the workspace; every tool reads the same file", 18, "h"))
    # source
    b += [rect(24, 70, 220, 70, "acc"),
          text(134, 96, "alplabai/alp-platform", 13, "h", "middle"),
          text(134, 116, "release asset platform.yaml", 11.5, "q", "middle"),
          text(134, 132, "or --manifest FILE", 11.5, "q", "middle")]
    b += [rect(320, 70, 220, 70, "own"),
          text(430, 96, "tan platform use 27.2", 13, "h mono", "middle"),
          text(430, 116, "validates, writes the pin", 11.5, "q", "middle"),
          text(430, 132, "never flashes firmware", 11.5, "q", "middle")]
    b += [rect(616, 70, 320, 70, "hw"),
          text(776, 96, "<west topdir>/.alp/platform.yaml", 13, "h mono", "middle"),
          text(776, 118, "the pin: byte-identical to the asset", 11.5, "q", "middle")]
    b += [line("M244 105H320"), line("M540 105H616")]
    # readers
    rd = [("alp.lock", ["platform: version +", "manifestSha256", "verify reports drift"]),
          ("tan doctor", ["data.platform", "per-component status", "match / mismatch / unknown"]),
          ("VS Code extension", ["Alp Platform 27.2.0", "in the status bar", "off-manifest warning"])]
    for i, (h, ls) in enumerate(rd):
        x = 216 + i * 248
        b += [rect(x, 210, 224, 96, "box"), text(x + 112, 236, h, 13, "h", "middle")]
        for j, l in enumerate(ls):
            b.append(text(x + 112, 258 + j * 17, l, 11.5, "q", "middle"))
    b += [line("M776 140V170H328V210"), line("M576 170V210"), line("M776 170H824V210")]
    b.append(line("M688 258H712", "lnacc", "aa"))
    b.append(text(700, 330, "the extension reads tan doctor; it never parses the manifest", 11.5, "q", "middle"))
    return svg(W, 350, "One pin in the workspace; every tool reads the same file", b)


# ---------------------------------------------------------------- 5. support windows
def support():
    W, b = 960, []
    b.append(text(24, 36, "Quarterly lines, each supported for about six months", 18, "h"))
    months = ["Feb 27", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan 28", "Feb", "Mar", "Apr", "May"]
    x0, x1, n = 176, 936, len(months) - 1
    X = lambda m: x0 + (x1 - x0) * m / n
    for i, m in enumerate(months):
        b += [f'<line class="grid" x1="{X(i)}" x2="{X(i)}" y1="72" y2="262"/>',
              text(X(i), 64, m, 11, "q", "middle")]
    lines = [("27.2", 0), ("27.5", 3), ("27.8", 6), ("27.11", 9), ("28.2", 12)]
    for k, (name, s) in enumerate(lines):
        y = 92 + k * 36
        b.append(text(24, y + 5, f"Alp Platform {name}", 13, "h"))
        e1, e2 = min(s + 3, n), min(s + 6, n)
        b.append(f'<rect class="band1" x="{X(s)}" y="{y - 9}" width="{X(e1) - X(s)}" height="18" rx="4"/>')
        if e2 > e1:
            b.append(f'<rect class="band2" x="{X(e1)}" y="{y - 9}" width="{X(e2) - X(e1)}" height="18" rx="4"/>')
    b += [f'<rect class="band1" x="176" y="284" width="28" height="14" rx="3"/>',
          text(212, 296, "latest line: all MICRO fixes", 12),
          f'<rect class="band2" x="420" y="284" width="28" height="14" rx="3"/>',
          text(456, 296, "previous line: security and brick-risk fixes only", 12)]
    b.append(text(24, 326, "Proposed cadence; an unused line can be skipped. Dates are illustrative.", 11.5, "q"))
    return svg(W, 344, "Quarterly lines, each supported for about six months", b)


if __name__ == "__main__":
    for name, fn in [("platform-stack", stack), ("version-anatomy", anatomy), ("release-flow", flow),
                     ("platform-consumers", consumers), ("support-windows", support)]:
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print("wrote", name)
