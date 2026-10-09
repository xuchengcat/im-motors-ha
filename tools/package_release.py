"""Build an allowlisted local ZIP; no credentials, runtime data or analysis files."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    integration = ROOT / "custom_components/im_motors"
    version = json.loads((integration / "manifest.json").read_text(encoding="utf-8"))["version"]
    files = [ROOT / name for name in ("README.md", "hacs.json", "LICENSE", "CHANGELOG.md")]
    for directory in ("custom_components/im_motors", "docker"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and path.suffix in (".py", ".json", ".yaml", ".example"))
    files.extend([ROOT / "requirements-test.txt", ROOT / "pytest.ini"])
    files.extend((ROOT / "tests").glob("*.py"))
    files.extend(path for path in (ROOT / "tools").glob("*.py") if path.name != "vendor_runtime.py")
    files.extend((ROOT / "docs").glob("*.md"))
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"im_motors_ha-{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(set(files)):
            bundle.write(path, path.relative_to(ROOT))
    # HACS extracts this archive directly into custom_components/im_motors.
    runtime_archive = dist / "im_motors.zip"
    with zipfile.ZipFile(runtime_archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(integration.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".json"):
                bundle.write(path, path.relative_to(integration))
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                        for path in (archive, runtime_archive))
    (dist / "SHA256SUMS").write_text(checksums, encoding="ascii")
    print("Packaged", len(set(files)), "allowlisted files:", archive.name)
    print("Packaged HACS runtime:", runtime_archive.name)


if __name__ == "__main__":
    main()
