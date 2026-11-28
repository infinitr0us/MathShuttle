"""Download the pinned pandoc release and install it for this user."""
from __future__ import annotations

import hashlib
import http.client
import os
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, NamedTuple, Optional, Tuple

from .paths import local_data_dir

PANDOC_VERSION = "3.14"
_RELEASE_URL = "https://github.com/jgm/pandoc/releases/download/" + PANDOC_VERSION + "/"
_MAX_DOWNLOAD = 200_000_000
_MIN_FREE = 400_000_000
_CHUNK = 64 * 1024
_TIMEOUT = 20
_SLOW_AFTER = 60  # seconds: after that, a download below _MIN_RATE bytes per second on average stops
_MIN_RATE = 20_000
_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_EXE = "pandoc.exe" if sys.platform == "win32" else "pandoc"
_Progress = Callable[[int, Optional[int]], None]


class FetchError(RuntimeError):
    """The download or installation failed. The message is meant for the user."""


class FetchCancelled(FetchError):
    """The user stopped the installation."""


class Asset(NamedTuple):
    name: str
    size: int
    sha256: str


_ASSETS = {
    ("win32", "x86_64"): Asset("pandoc-3.14-windows-x86_64.zip", 43614099,
                               "0144b96e8c7b023fd382ec5ef5bdc475e873f1019d6fa72502492df2c9a94a3b"),
    ("darwin", "arm64"): Asset("pandoc-3.14-arm64-macOS.zip", 43712865,
                               "58d6593884fec9d4424b641b92503c316d9c36decb2891ff8d73ae74dd3b6304"),
    ("darwin", "x86_64"): Asset("pandoc-3.14-x86_64-macOS.zip", 27490795,
                                "891708579061e7b1c1a6ce0df30ebb3cb2b6d0ca045ed3c8554ab830c1e0c270"),
    ("linux", "x86_64"): Asset("pandoc-3.14-linux-amd64.tar.gz", 36635549,
                               "c5f4720cdefb3f7bdc5d07202ef579feef0d1370773c873804dd8fdaa8c4f1b8"),
    ("linux", "arm64"): Asset("pandoc-3.14-linux-arm64.tar.gz", 39208196,
                              "c692f3d78a415741bbbac4cdbbbe26fa7edca1f43d2301b982fbf4009cc8cf7f"),
}


def _release_asset() -> Asset:
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if sys.platform == "win32" and arch == "arm64":
        arch = "x86_64"
    asset = _ASSETS.get((sys.platform, arch))
    if asset is None:
        raise FetchError(f"No pandoc download is available for {sys.platform} ({machine}). "
                         "Install pandoc manually and set MATHSHUTTLE_PANDOC to its full path.")
    return asset


def _folder() -> Path:
    return Path(local_data_dir()) / "pandoc" / PANDOC_VERSION


def installed_pandoc() -> Optional[str]:
    """Return the downloaded binary's path if it exists."""
    path = _folder() / _EXE
    return str(path) if path.is_file() else None


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise FetchCancelled("Pandoc installation cancelled.")


class _SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.type == "https" and not newurl.lower().startswith("https://"):
            fp.close()
            raise FetchError("The download was redirected to an insecure address. Install pandoc manually.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url):
    https = urllib.request.HTTPSHandler(context=ssl.create_default_context())
    return urllib.request.build_opener(https, _SecureRedirect()).open(url, timeout=_TIMEOUT)


def _system_curl() -> Optional[str]:
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot")
        path = os.path.join(root, "System32", "curl.exe") if root else ""
    elif sys.platform == "darwin":
        path = "/usr/bin/curl"
    else:
        return shutil.which("curl")
    return path if os.path.isfile(path) else None


def _save_body(source: BinaryIO, path: Path, total: Optional[int], progress, cancel) -> Tuple[int, str]:
    """Limit the actual bytes received, including when the server gives no size."""
    too_large = "The download is too large (over 200 MB). Install pandoc manually."
    if total is not None and total > _MAX_DOWNLOAD:
        raise FetchError(too_large)
    digest = hashlib.sha256()
    done = 0
    start = time.monotonic()
    with path.open("wb") as output:
        while True:
            _check_cancel(cancel)
            block = source.read1(_CHUNK)
            if not block:
                break
            done += len(block)
            if done > _MAX_DOWNLOAD:
                raise FetchError(too_large)
            # a connection that stops does so after _TIMEOUT; one that only trickles would go on for hours
            elapsed = time.monotonic() - start
            if elapsed > _SLOW_AFTER and done < _MIN_RATE * elapsed:
                raise FetchError("The download is too slow (%d kB/s). Check your connection and try again, "
                                 "or install pandoc manually." % (done / elapsed / 1000))
            output.write(block)
            digest.update(block)
            if progress:
                progress(done, total)
    return done, digest.hexdigest()


def _download(url, path, progress, cancel):
    try:
        response = _open_url(url)
    except (urllib.error.URLError, ssl.SSLError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if not (isinstance(reason, ssl.SSLCertVerificationError) or
                getattr(reason, "reason", "") == "CERTIFICATE_VERIFY_FAILED"):
            raise
        _check_cancel(cancel)
        curl = _system_curl()
        if not curl:
            raise FetchError("Python could not verify the download certificate. Update your certificates "
                             "or install pandoc manually.") from exc
    else:
        with response:
            length = response.headers.get("Content-Length", "")
            total = None
            if re.fullmatch(r"[0-9]+", length):
                digits = length.lstrip("0") or "0"
                total = int(digits) if len(digits) <= len(str(_MAX_DOWNLOAD)) else _MAX_DOWNLOAD + 1
            return _save_body(response, path, total, progress, cancel)

    # Let the system curl use its usual certificate store. Keep the same byte limit and hash check.
    args = [curl, "-q", "-fL", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout",
            str(_TIMEOUT), "--speed-limit", "1", "--speed-time", str(_TIMEOUT), "--", url]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=_FLAGS) as proc:
        try:
            result = _save_body(proc.stdout, path, None, progress, cancel)
            if proc.wait(timeout=_TIMEOUT) != 0:
                raise FetchError("The system downloader could not fetch pandoc. Check your connection "
                                 "and certificates, or install pandoc manually.")
            return result
        finally:
            if proc.poll() is None:
                proc.kill()


def _temp_path(folder, cleanup, suffix="") -> Path:
    fd, name = tempfile.mkstemp(dir=folder, suffix=suffix)
    os.close(fd)
    path = Path(name)
    cleanup.callback(path.unlink, missing_ok=True)
    return path


def _member_name(raw: str) -> Optional[str]:
    parts = PurePosixPath(raw).parts
    if not parts or raw.startswith("/") or "\\" in raw or ":" in raw or ".." in parts:
        return None
    name = parts[-1]
    if name == _EXE and len(parts) <= 4:
        return _EXE
    if name.startswith(("COPYING", "COPYRIGHT")) and re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]+', name):
        return name
    return None


def _unpack(path, asset, folder, cleanup, cancel):
    """Stage only the regular binary and licence files, each under a fresh local name."""
    staged = {}

    def stage(name, source):
        with source:
            if name in staged:
                raise FetchError(f"The archive contains more than one {name}. Install pandoc manually.")
            target = _temp_path(folder, cleanup, suffix=".exe" if name == "pandoc.exe" else "")
            with target.open("wb") as output:
                while True:
                    _check_cancel(cancel)
                    block = source.read(_CHUNK)
                    if not block:
                        break
                    output.write(block)
            staged[name] = target

    if asset.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                _check_cancel(cancel)
                name = _member_name(info.filename)
                kind = stat.S_IFMT(info.external_attr >> 16)
                if name and not info.is_dir() and not info.external_attr & 0x10 and kind in (0, stat.S_IFREG):
                    stage(name, archive.open(info))
    else:
        with tarfile.open(path, "r|gz") as archive:
            for info in archive:
                _check_cancel(cancel)
                name = _member_name(info.name)
                if name and info.isreg():
                    stage(name, archive.extractfile(info))
    if _EXE not in staged:
        raise FetchError("The archive did not contain the pandoc program. Install pandoc manually.")
    return staged


def _check_version(binary: Path) -> None:
    if os.name != "nt":
        binary.chmod(0o755)
    try:
        proc = subprocess.run([str(binary), "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=_TIMEOUT, creationflags=_FLAGS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FetchError("Could not start the downloaded pandoc. Install pandoc manually.") from exc
    version = rb"^pandoc " + re.escape(PANDOC_VERSION.encode()) + rb"(?:\s|$)"
    if proc.returncode or not re.match(version, proc.stdout):
        raise FetchError(f"The program did not report pandoc {PANDOC_VERSION}. Install pandoc manually.")


def _publish(source: Path, destination: Path) -> None:
    """Windows can briefly lock a file while another installer replaces it."""
    for delay in (0.05, 0.1, 0.2, 0.4):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(delay)
    os.replace(source, destination)


def _prune_older(folder: Path) -> None:
    version = tuple(map(int, PANDOC_VERSION.split(".")))
    for path in folder.parent.iterdir():
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", path.name) and tuple(map(int, path.name.split("."))) < version:
            if not path.is_symlink() and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


def install(progress: Optional[_Progress] = None, cancel: Optional[threading.Event] = None,
            url: Optional[str] = None, *, asset: Optional[Asset] = None) -> str:
    """Download, verify and install pandoc. Progress receives bytes downloaded and the total if known."""
    _check_cancel(cancel)
    asset = asset or _release_asset()
    folder = _folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < _MIN_FREE:
            raise FetchError("Free at least 400 MB of disk space and try again.")
        with ExitStack() as cleanup:
            archive = _temp_path(folder, cleanup)
            try:
                size, digest = _download(url or _RELEASE_URL + asset.name, archive, progress, cancel)
            except (OSError, http.client.HTTPException, subprocess.TimeoutExpired) as exc:
                _check_cancel(cancel)
                raise FetchError(f"Could not download pandoc. Check your connection and try again. ({exc})") from exc
            _check_cancel(cancel)
            if size != asset.size or digest != asset.sha256:
                raise FetchError("The download was not pandoc's release file. Nothing was installed. Try again.")
            staged = _unpack(archive, asset, folder, cleanup, cancel)
            binary = staged.pop(_EXE)
            # Check our own copy before publishing. A failed or concurrent install keeps the working binary.
            _check_version(binary)
            _check_cancel(cancel)
            for name, licence in staged.items():
                _publish(licence, folder / name)
            _publish(binary, folder / _EXE)
        _prune_older(folder)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        raise FetchError("Could not read the pandoc archive. Download it again or install pandoc manually.") from exc
    except OSError as exc:
        raise FetchError(f"Could not install pandoc in {folder}. Check the folder permissions and free space. "
                         f"({exc})") from exc
    return str(folder / _EXE)
