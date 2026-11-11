"""Render a sample write-up from each group to an image, as a reader sees it.

The same agent and repetition from each experiment: OpenCode's first run
asked for the analysis without mention of reproducibility, and its first
run told to "Use Calkit.", which is the one run there that followed all
four reproducibility practices. Each write-up is converted from Markdown
to HTML by pandoc, with its figure embedded and a plain GitHub-like style
(``scripts/report.css``), and captured in full by headless Chromium.

Nothing comes from the system: pandoc is the one bundled with
pypandoc_binary, Chromium is the build this Playwright version pins,
installed inside the environment, the fonts are the DejaVu fonts
matplotlib ships, embedded in the page, and math is drawn by matplotlib's
mathtext with the same fonts, as SVG paths, rather than left to the
browser, which would need a math font. Each render checks which fonts
Chromium used and fails if any came from the system.
"""

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib
import pypandoc
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser, math_to_image

# Install and look for browsers inside the environment, not a shared cache
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
# The pandoc bundled with pypandoc_binary, not one found on the PATH
PANDOC = Path(pypandoc.__file__).parent / "files" / "pandoc"
os.environ["PYPANDOC_PANDOC"] = str(PANDOC)

from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CSS = REPO / "scripts" / "report.css"
SAMPLES = [
    ("runs/reproducibility/opencode/rep-1/workspaces/default/opencode.tar.gz",
     "results.md", "sample-report-unprompted.png"),
    ("runs/use-calkit/opencode/rep-1/workspaces/cli/opencode.tar.gz",
     "results.md", "sample-report-use-calkit.png"),
]
WIDTH = 920
FONTS = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
FONT_FACES = [
    ("Report Sans", "normal", "normal", "DejaVuSans.ttf"),
    ("Report Sans", "bold", "normal", "DejaVuSans-Bold.ttf"),
    ("Report Sans", "normal", "italic", "DejaVuSans-Oblique.ttf"),
    ("Report Sans", "bold", "italic", "DejaVuSans-BoldOblique.ttf"),
    ("Report Mono", "normal", "normal", "DejaVuSansMono.ttf"),
    ("Report Mono", "bold", "normal", "DejaVuSansMono-Bold.ttf"),
]
# Pixels, matching the body text in report.css
MATH_SIZE = 15
TEXT_COLOR = "#1f2328"
matplotlib.rcParams["mathtext.fontset"] = "dejavusans"
matplotlib.rcParams["svg.fonttype"] = "path"


def font_css(path: Path) -> None:
    path.write_text("".join(
        f'@font-face {{ font-family: "{family}"; font-weight: {weight}; '
        f'font-style: {style}; src: url("{(FONTS / name).as_uri()}"); }}\n'
        for family, weight, style, name in FONT_FACES
    ))


def math_html(tex: str, display: bool) -> str:
    """An equation as an inline SVG image, sitting on the text baseline."""
    expr = f"${tex.strip()}$"
    prop = FontProperties(size=MATH_SIZE)
    # At 72 dpi a point is a pixel
    width, height, depth, *_ = MathTextParser("path").parse(expr, 72, prop)
    buf = io.BytesIO()
    math_to_image(expr, buf, prop=prop, dpi=72, format="svg",
                  color=TEXT_COLOR)
    src = base64.b64encode(buf.getvalue()).decode()
    img = (f'<img class="math" alt="{tex}" '
           f'src="data:image/svg+xml;base64,{src}" '
           f'style="width: {width:.1f}px; height: {height:.1f}px; '
           f'vertical-align: {-depth:.1f}px">')
    return f'<span class="display-math">{img}</span>' if display else img


def draw_math(node):
    """Replace each Math node in pandoc's AST with its drawn equation."""
    if isinstance(node, list):
        return [draw_math(n) for n in node]
    if not isinstance(node, dict):
        return node
    if node.get("t") == "Math":
        kind, tex = node["c"]
        return {"t": "RawInline", "c": [
            "html", math_html(tex, kind["t"] == "DisplayMath")]}
    return {k: draw_math(v) for k, v in node.items()}


def render(tar_path: Path, report: str, out: Path, page) -> None:
    with TemporaryDirectory() as tmp:
        with tarfile.open(tar_path) as tar:
            tar.extractall(tmp, filter="data")
        ws = Path(tmp) / "workspace"
        html = Path(tmp) / "report.html"
        fonts = Path(tmp) / "fonts.css"
        font_css(fonts)
        ast = json.loads(pypandoc.convert_file(
            str(ws / report), "json", format="markdown+tex_math_dollars"))
        pypandoc.convert_text(
            json.dumps(draw_math(ast)), "html", format="json",
            outputfile=str(html),
            extra_args=["--standalone", "--embed-resources",
                        f"--css={fonts}", f"--css={CSS}",
                        f"--resource-path={ws}",
                        "--metadata", "pagetitle=Report"],
        )
        page.goto(html.as_uri())
        page.wait_for_load_state("networkidle")
        system = system_fonts(page)
        if system:
            raise SystemExit(f"{out.name} used system fonts: {system}")
        page.screenshot(path=str(out), full_page=True)


def system_fonts(page) -> set[str]:
    """Fonts Chromium drew any text with that aren't embedded in the page."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    root = cdp.send("DOM.getDocument", {"depth": -1})["root"]
    found = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack += node.get("children", [])
        if node.get("nodeType") != 1:
            continue
        fonts = cdp.send("CSS.getPlatformFontsForNode",
                         {"nodeId": node["nodeId"]})["fonts"]
        found |= {f["familyName"] for f in fonts if not f["isCustomFont"]}
    cdp.detach()
    return found


def main() -> None:
    if not PANDOC.is_file():
        raise SystemExit(f"pypandoc_binary's pandoc isn't at {PANDOC}")
    # Fetches the Chromium build this Playwright version pins, if missing
    subprocess.run([sys.executable, "-m", "playwright", "install",
                    "chromium"], check=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": 800},
                                device_scale_factor=2)
        for tar_path, report, name in SAMPLES:
            render(REPO / tar_path, report, REPO / "figures" / name, page)
        browser.close()


if __name__ == "__main__":
    main()
