"""CI only: download pandoc's release for this runner, check its SHA-256, unpack the binary, and print
the folder it is in (for $GITHUB_PATH).  The app itself does this with `mathshuttle get-pandoc`."""
import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile

VERSION = "3.14"
ASSETS = {
    "win32": ("pandoc-3.14-windows-x86_64.zip",
              "0144b96e8c7b023fd382ec5ef5bdc475e873f1019d6fa72502492df2c9a94a3b"),
    "darwin-arm64": ("pandoc-3.14-arm64-macOS.zip",
                     "58d6593884fec9d4424b641b92503c316d9c36decb2891ff8d73ae74dd3b6304"),
    "darwin-x86_64": ("pandoc-3.14-x86_64-macOS.zip",
                      "891708579061e7b1c1a6ce0df30ebb3cb2b6d0ca045ed3c8554ab830c1e0c270"),
    "linux-x86_64": ("pandoc-3.14-linux-amd64.tar.gz",
                     "c5f4720cdefb3f7bdc5d07202ef579feef0d1370773c873804dd8fdaa8c4f1b8"),
    "linux-aarch64": ("pandoc-3.14-linux-arm64.tar.gz",
                      "c692f3d78a415741bbbac4cdbbbe26fa7edca1f43d2301b982fbf4009cc8cf7f"),
}


def main() -> None:
    key = "win32" if sys.platform == "win32" else "%s-%s" % (sys.platform, platform.machine().lower())
    name, digest = ASSETS[key]
    url = "https://github.com/jgm/pandoc/releases/download/%s/%s" % (VERSION, name)
    data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != digest:
        sys.exit("%s: SHA-256 mismatch" % name)
    exe = "pandoc.exe" if sys.platform == "win32" else "pandoc"
    if name.endswith(".zip"):
        archive = zipfile.ZipFile(io.BytesIO(data))
        member = next(n for n in archive.namelist() if n.rsplit("/", 1)[-1] == exe)
        binary = archive.read(member)
    else:
        archive = tarfile.open(fileobj=io.BytesIO(data))
        member = next(m for m in archive.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == exe)
        binary = archive.extractfile(member).read()
    folder = os.path.join(os.environ.get("RUNNER_TEMP", "."), "pandoc-bin")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, exe)
    with open(path, "wb") as f:
        f.write(binary)
    os.chmod(path, 0o755)
    print(folder)


if __name__ == "__main__":
    main()
