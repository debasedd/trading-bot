"""
inspect_dropdown.py — Lihat DOM dropdown sungguhan di browser.

Selama ini selector CSS ditulis berdasarkan tebakan nama kelas, dan dua
kali tebakan itu meleset. Skrip ini membuka dashboard di Chromium headless,
menunggu React selesai merender, lalu dumping struktur dropdown beserta
computed style setiap elemennya.

That's the only way to know which selector actually matches.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "data_store" / "dropdown_dom.txt"
_lines = []


def say(*a):
    _lines.append(" ".join(str(x) for x in a))


PROBE = """
() => {
  const dd = document.querySelector('.symbol-select');
  if (!dd) return {error: '.symbol-select not found'};

  const out = {rootTag: dd.tagName, rootClass: dd.className, html: dd.outerHTML};

  const describe = (sel) => {
    const el = (sel === '.symbol-select') ? dd : dd.querySelector(sel);
    if (!el) return {sel, found: false};
    const cs = getComputedStyle(el);
    return {
      sel, found: true,
      tag: el.tagName,
      className: el.className,
      background: cs.backgroundColor,
      color: cs.color,
      fontSize: cs.fontSize,
      fontFamily: cs.fontFamily.slice(0, 40),
      border: cs.borderTopWidth + ' ' + cs.borderTopStyle + ' ' + cs.borderTopColor,
      height: cs.height,
    };
  };

  out.targets = [
    '.symbol-select',
    '.dash-dropdown-trigger', '.dash-dropdown-value',
    '.dash-dropdown-placeholder', '.dash-dropdown-trigger-icon',
    '.dash-dropdown-wrapper', '.dash-dropdown-content',
  ].map(describe);

  return out;
}
"""

MATCH = """
() => {
  const dd = document.querySelector('.symbol-select');
  if (!dd) return {error: 'no .symbol-select'};
  const all = dd.querySelectorAll('*');
  const classes = new Set();
  all.forEach(e => String(e.className || '').split(/\\s+/)
    .forEach(c => c && classes.add(c)));
  return {descendantClasses: [...classes].sort()};
}
"""


def main() -> int:
    try:
        urllib.request.urlopen("http://127.0.0.1:8050", timeout=5)
    except Exception as e:
        print(f"Dashboard tidak jalan: {e}")
        print("Jalankan `python run.py` lebih dulu.")
        return 2

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        # `networkidle` tidak pernah tercapai: dashboard poll tiap 500ms.
        page.goto("http://127.0.0.1:8050", wait_until="domcontentloaded")
        # Beri React waktu merender dropdown dari state awal.
        page.wait_for_selector(".symbol-select", timeout=30000)
        page.wait_for_timeout(2000)

        info = page.evaluate(PROBE)
        say("=" * 70)
        say("DOM DROPDOWN AKTUAL")
        say("=" * 70)
        if info.get("error"):
            say("  ERROR:", info["error"])
            browser.close()
            _write()
            return 2

        say(f"  root tag   : {info['rootTag']}")
        say(f"  root class : {info['rootClass']!r}")
        say("")
        say(f"  HTML (dipotong 900):")
        say("  " + info["html"][:900].replace("><", ">\n  <"))
        say("")
        say("-" * 70)
        say("  COMPUTED STYLE PER TARGET")
        say("-" * 70)
        for t in info["targets"]:
            if not t.get("found"):
                say(f"  {t['sel']:<32} TIDAK ADA")
                continue
            say(f"  {t['sel']}")
            say(f"      class={t['className']!r}")
            say(f"      bg={t['background']}  color={t['color']}")
            say(f"      font={t['fontSize']} / {t['fontFamily']}")
            say(f"      border={t['border']}  height={t['height']}")
        say("")

        m = page.evaluate(MATCH)
        say("-" * 70)
        say("  SEMUA KELAS DI DALAM .symbol-select")
        say("-" * 70)
        for c in m.get("descendantClasses", []):
            say("   ", c)

        # Screenshot elemen dropdown itu langsung, beserta panelnya.
        for sel, name in (
            ("#hud-chart-symbol-select", "dropdown_shot.png"),
        ):
            try:
                el = page.query_selector(sel)
                if el:
                    el.screenshot(path=str(OUT.parent / name))
                    say("")
                    say(f"  screenshot: data_store/{name}")
            except Exception as e:
                say("  screenshot gagal:", e)

        browser.close()

    _write()
    print(f"Laporan: {OUT}")
    return 0


def _write():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
