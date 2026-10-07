"""Build a portable HTML preview from the static site's source files.

The generated file embeds the synthetic fixture, styles, application and images.
It makes no company-search, evidence or judging calls. Optional Google Fonts
remain remote CSS imports; the supplied system stacks work without the network.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

WEBSITE = Path(__file__).resolve().parents[1]
DIST = WEBSITE / "dist"
CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(['\"])([^'\"]+)\1\s*\)?([^;]*);", re.I)
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", re.I)
JS_EXPORT = re.compile(
    r"^export\s+((?:async\s+)?(?:function|class|const|let|var)\s+([\w$]+))", re.M
)
JS_IMPORT = re.compile(r"^import\s+\{([^}]+)\}\s+from\s+(['\"])\./data\.js\2\s*;?\s*$", re.M)


def local_file(reference: str, directory: Path) -> Path:
    """Resolve only project assets, including references from imported CSS."""
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"Expected a local project asset: {reference}")
    path = (directory / parsed.path).resolve()
    if not path.is_relative_to(WEBSITE):
        raise ValueError(f"Asset leaves the website directory: {reference}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def data_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def flatten_css(path: Path, remote: list[str], stack: tuple[Path, ...] = ()) -> str:
    if path in stack:
        raise ValueError(f"Circular CSS import: {path}")
    source = path.read_text(encoding="utf-8")

    def expand(match: re.Match[str]) -> str:
        reference, media = match.group(2), match.group(3).strip()
        if urlsplit(reference).scheme or reference.startswith("//"):
            if match.group(0) not in remote:
                remote.append(match.group(0))
            return ""
        imported = flatten_css(local_file(reference, path.parent), remote, (*stack, path))
        return f"@media {media} {{\n{imported}\n}}" if media else imported

    source = CSS_IMPORT.sub(expand, source)

    def embed_url(match: re.Match[str]) -> str:
        reference = match.group(2).strip()
        if urlsplit(reference).scheme or reference.startswith(("//", "#")):
            return match.group(0)
        return f'url("{data_url(local_file(reference, path.parent))}")'

    return CSS_URL.sub(embed_url, source)


def attributes(tag: str) -> dict[str, str]:
    return {
        match.group(1).lower(): match.group(2) or match.group(3) or match.group(4) or ""
        for match in re.finditer(r"([\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag)
    }


def script_text(source: str) -> str:
    """Prevent HTML raw-text parsing from terminating an embedded script."""
    return re.sub(r"</script", r"<\\/script", source, flags=re.I)


def application_script(data_path: Path, app_path: Path) -> str:
    data_source = data_path.read_text(encoding="utf-8")
    exports = [match.group(2) for match in JS_EXPORT.finditer(data_source)]
    data_source = JS_EXPORT.sub(r"\1", data_source)
    if re.search(r"^\s*(?:import|export)\s", data_source, re.M):
        raise ValueError(
            "The portable builder supports named data declarations, not extra imports."
        )
    app_source = app_path.read_text(encoding="utf-8")
    imports = list(JS_IMPORT.finditer(app_source))
    if len(imports) != 1:
        raise ValueError("app.js must have one single-line named import from './data.js'.")
    bindings = []
    for binding in imports[0].group(1).split(","):
        parts = binding.strip().split(" as ")
        if not parts[0]:
            continue
        if parts[0] not in exports or len(parts) > 2:
            raise ValueError(f"Unsupported data-module import: {binding}")
        bindings.append(":".join(parts))
    app_source = JS_IMPORT.sub("", app_source)
    if re.search(r"^\s*(?:import|export)\s", app_source, re.M):
        raise ValueError("The portable builder cannot resolve additional JavaScript modules.")
    return script_text(
        "(() => {\nconst dataModule = (() => {\n"
        + data_source
        + "\nreturn {"
        + ",".join(exports)
        + "};\n})();\nconst {"
        + ",".join(bindings)
        + "} = dataModule;\n"
        + app_source
        + "\n})();"
    )


def build(output: Path, fixture_path: Path = DIST / "data/demo.json") -> Path:
    document = (DIST / "index.html").read_text(encoding="utf-8")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    # JSON strings must not introduce HTML tags, even when an imported fixture
    # contains source text with a closing script tag.
    fixture_json = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"))
    fixture_json = (
        fixture_json.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

    def link(match: re.Match[str]) -> str:
        tag = match.group(0)
        attrs = attributes(tag)
        if attrs.get("rel") == "stylesheet":
            remote: list[str] = []
            css = flatten_css(local_file(attrs["href"], DIST), remote)
            css = "\n".join(remote) + "\n" + css
            css = re.sub(r"</style", r"<\\/style", css, flags=re.I)
            return f"<style>\n{css}\n</style>"
        if attrs.get("rel") == "icon":
            image = data_url(local_file(attrs["href"], DIST))
            return f'<link rel="icon" href="{image}">'
        return tag

    document = re.sub(r"<link\b[^>]*>", link, document, flags=re.I)

    def image(match: re.Match[str]) -> str:
        tag = match.group(0)
        source = attributes(tag).get("src", "")
        if not source or urlsplit(source).scheme or source.startswith("//"):
            return tag
        inline = data_url(local_file(source, DIST))
        return re.sub(r"\bsrc\s*=\s*(['\"])[^'\"]*\1", f'src="{inline}"', tag, flags=re.I)

    document = re.sub(r"<img\b[^>]*>", image, document, flags=re.I)
    combined = application_script(DIST / "data.js", DIST / "app.js")
    embedded = f"<script>globalThis.COMPANYBENCH_DATA = {fixture_json};</script>\n<script>\n{combined}\n</script>"
    replacements = 0

    def script(match: re.Match[str]) -> str:
        nonlocal replacements
        attrs = attributes(match.group(0))
        if attrs.get("type") != "module" or attrs.get("src") != "./app.js":
            raise ValueError(
                "Unexpected external script in index.html; add explicit bundling support."
            )
        replacements += 1
        return embedded

    document = re.sub(r"<script\b[^>]*\bsrc\s*=[^>]*>\s*</script\s*>", script, document, flags=re.I)
    if replacements != 1:
        raise ValueError("Expected exactly one application script in index.html.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=WEBSITE / "preview.html")
    parser.add_argument("--data", type=Path, default=DIST / "data/demo.json")
    args = parser.parse_args()
    path = build(args.output.resolve(), args.data.resolve())
    print(f"Built {path} ({path.stat().st_size:,} bytes). Open directly in a browser.")


if __name__ == "__main__":
    main()
