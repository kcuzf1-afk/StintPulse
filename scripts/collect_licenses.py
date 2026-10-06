"""Collect installed runtime license texts for the distributable bundle."""

from importlib import metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "licenses"
PYTHON_PACKAGES = [
    "fastapi",
    "starlette",
    "uvicorn",
    "numpy",
    "pydantic",
    "pydantic_core",
    "psutil",
    "websockets",
    "anyio",
    "typing_extensions",
    "annotated-types",
    "typing-inspection",
    "click",
    "h11",
    "idna",
    "sniffio",
    "colorama",
    # Setup assistant: official Anthropic SDK and its runtime dependencies.
    "anthropic",
    "httpx2",
    "httpcore2",
    "truststore",
    "jiter",
    "docstring_parser",
]
NODE_PACKAGES = [
    "react",
    "react-dom",
    "scheduler",
    "echarts",
    "zrender",
    "lucide-react",
    "tslib",
    "@fontsource/titillium-web",
]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in PYTHON_PACKAGES:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            # Platform/version-dependent runtime helpers may not be installed.
            continue
        versions[name] = dist.version
        chunks = [
            f"{name} {dist.version}\n",
            f"License metadata: {dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See bundled license text'}\n",
        ]
        for entry in dist.files or []:
            if "license" in entry.name.lower() or entry.name.lower().startswith(
                "notice"
            ):
                file = Path(dist.locate_file(entry))
                if file.is_file():
                    chunks.append(
                        f"\n--- {entry.name} ---\n"
                        + file.read_text(encoding="utf-8", errors="replace")
                    )
        (OUTPUT / f"python-{name}.txt").write_text("\n".join(chunks), encoding="utf-8")
    for name in NODE_PACKAGES:
        package = ROOT / "frontend" / "node_modules" / name
        info = json.loads((package / "package.json").read_text(encoding="utf-8"))
        versions[name] = info["version"]
        files = [
            p
            for p in package.iterdir()
            if p.is_file()
            and ("license" in p.name.lower() or p.name.lower().startswith("notice"))
        ]
        if (package / "licenses").is_dir():
            files.extend(p for p in (package / "licenses").iterdir() if p.is_file())
        chunks = [
            f"{name} {info['version']}\nLicense: {info.get('license', 'See bundled texts')}\n"
        ]
        for file in sorted(files):
            chunks.append(
                f"\n--- {file.name} ---\n"
                + file.read_text(encoding="utf-8", errors="replace")
            )
        safe = name.replace("@", "").replace("/", "-")  # scoped packages
        (OUTPUT / f"node-{safe}.txt").write_text("\n".join(chunks), encoding="utf-8")
    (OUTPUT / "versions.json").write_text(
        json.dumps(versions, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Collected license notices for {len(versions)} runtime packages.")


if __name__ == "__main__":
    main()
