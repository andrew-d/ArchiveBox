__package__ = 'plugins_pkg.playwright'

import os
import platform
from pathlib import Path
from typing import List, Optional, Dict, ClassVar

from pydantic import computed_field, Field
from pydantic_pkgr import (
    BinName,
    BinProviderName,
    BinProviderOverrides,
    InstallArgs,
    PATHStr,
    HostBinPath,
    bin_abspath,
    OPERATING_SYSTEM,
    DEFAULT_ENV_PATH,
)

from archivebox.config import CONSTANTS

from abx.archivebox.base_binary import BaseBinProvider, env

from plugins_pkg.pip.binproviders import SYS_PIP_BINPROVIDER

from .binaries import PLAYWRIGHT_BINARY


MACOS_PLAYWRIGHT_CACHE_DIR: Path = Path("~/Library/Caches/ms-playwright")
LINUX_PLAYWRIGHT_CACHE_DIR: Path = Path("~/.cache/ms-playwright")


class PlaywrightBinProvider(BaseBinProvider):
    name: BinProviderName = "playwright"
    INSTALLER_BIN: BinName = PLAYWRIGHT_BINARY.name

    PATH: PATHStr = f"{CONSTANTS.DEFAULT_LIB_DIR / 'bin'}:{DEFAULT_ENV_PATH}"

    playwright_install_args: List[str] = ["install"]

    packages_handler: BinProviderOverrides = Field(default={
        "chrome": ["chromium"],
    }, exclude=True)

    _browser_abspaths: ClassVar[Dict[str, HostBinPath]] = {}

    @computed_field
    @property
    def INSTALLER_BIN_ABSPATH(self) -> HostBinPath | None:
        try:
            return PLAYWRIGHT_BINARY.load().abspath
        except Exception as e:
            return None

    @property
    def playwright_browsers_dir(self) -> Path:
        # The directory where playwright stores browsers can be overridden with
        # the "PLAYWRIGHT_BROWSERS_PATH" environment variable; if it's present
        # and a directory, we should use that. See the playwright documentation
        # for more details:
        #    https://playwright.dev/docs/browsers#managing-browser-binaries
        dir_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if dir_path and os.path.isdir(dir_path):
            return Path(dir_path)

        # Otherwise return the default path based on the operating system.
        return (
            MACOS_PLAYWRIGHT_CACHE_DIR.expanduser()
            if OPERATING_SYSTEM == "darwin" else
            LINUX_PLAYWRIGHT_CACHE_DIR.expanduser()
        )

    def setup(self) -> None:
        # update paths from config if they arent the default
        from archivebox.config.common import STORAGE_CONFIG
        if STORAGE_CONFIG.LIB_DIR != CONSTANTS.DEFAULT_LIB_DIR:
            self.PATH = f"{STORAGE_CONFIG.LIB_DIR / 'bin'}:{DEFAULT_ENV_PATH}"

        assert SYS_PIP_BINPROVIDER.INSTALLER_BIN_ABSPATH, "Pip bin provider not initialized"

        if self.playwright_browsers_dir:
            self.playwright_browsers_dir.mkdir(parents=True, exist_ok=True)

    def installed_browser_bins(self, browser_name: str = "*") -> List[Path]:
        if browser_name == 'chrome':
            browser_name = 'chromium'
        
        # if on macOS, browser binary is inside a .app, otherwise it's just a plain binary
        if platform.system().lower() == "darwin":
            # ~/Library/caches/ms-playwright/chromium-1097/chrome-mac/Chromium.app/Contents/MacOS/Chromium
            return sorted(
                self.playwright_browsers_dir.glob(
                    f"{browser_name}-*/*-mac*/*.app/Contents/MacOS/*"
                )
            )

        # ~/Library/caches/ms-playwright/chromium-1097/chrome-linux/chromium
        paths = []
        for path in sorted(self.playwright_browsers_dir.glob(f"{browser_name}-*/*-linux/*")):
            if 'xdg-settings' in str(path):
                continue
            if 'ffmpeg' in str(path):
                continue
            if '/chrom' in str(path) and 'chrom' in path.name.lower():
                paths.append(path)
        return paths

    def default_abspath_handler(self, bin_name: BinName, **context) -> Optional[HostBinPath]:
        assert bin_name == "chrome", "Only chrome is supported using the @puppeteer/browsers install method currently."

        # already loaded, return abspath from cache
        if bin_name in self._browser_abspaths:
            return self._browser_abspaths[bin_name]

        # first time loading, find browser in self.playwright_browsers_dir by searching filesystem for installed binaries
        matching_bins = [abspath for abspath in self.installed_browser_bins() if bin_name in str(abspath)]
        if matching_bins:
            newest_bin = matching_bins[-1]  # already sorted alphabetically, last should theoretically be highest version number
            self._browser_abspaths[bin_name] = newest_bin
            return self._browser_abspaths[bin_name]
        
        # playwright sometimes installs google-chrome-stable via apt into system $PATH, check there as well
        abspath = bin_abspath('google-chrome-stable', PATH=env.PATH)
        if abspath:
            self._browser_abspaths[bin_name] = abspath
            return self._browser_abspaths[bin_name]

        return None

    def default_install_handler(self, bin_name: str, packages: Optional[InstallArgs] = None, **context) -> str:
        """playwright install chrome"""
        self.setup()
        assert bin_name == "chrome", "Only chrome is supported using the playwright install method currently."

        if not self.INSTALLER_BIN_ABSPATH:
            raise Exception(
                f"{self.__class__.__name__} install method is not available on this host ({self.INSTALLER_BIN} not found in $PATH)"
            )
        packages = packages or self.get_packages(bin_name)

        # print(f'[*] {self.__class__.__name__}: Installing {bin_name}: {self.INSTALLER_BIN_ABSPATH} install {packages}')


        # playwright install-deps (to install system dependencies like fonts, graphics libraries, etc.)
        if platform.system().lower() != 'darwin':
            # libglib2.0-0, libnss3, libnspr4, libdbus-1-3, libatk1.0-0, libatk-bridge2.0-0, libcups2, libdrm2, libxcb1, libxkbcommon0, libatspi2.0-0, libx11-6, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxrandr2, libgbm1, libcairo2, libasound2
            proc = self.exec(bin_name=self.INSTALLER_BIN_ABSPATH, cmd=['install-deps'])
            if proc.returncode != 0:
                print(proc.stdout.strip())
                print(proc.stderr.strip())

        proc = self.exec(bin_name=self.INSTALLER_BIN_ABSPATH, cmd=['install', *packages])

        if proc.returncode != 0:
            print(proc.stdout.strip())
            print(proc.stderr.strip())
            raise Exception(f"{self.__class__.__name__}: install got returncode {proc.returncode} while installing {packages}: {packages} PACKAGES={packages}")

        # chrome@129.0.6668.58 /data/lib/browsers/chrome/mac_arm-129.0.6668.58/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
        # playwright build v1010 downloaded to /home/squash/.cache/ms-playwright/ffmpeg-1010
        output_lines = [
            line for line in proc.stdout.strip().split('\n')
            if '/chrom' in line
            and 'chrom' in line.rsplit('/', 1)[-1].lower()   # if final path segment (filename) contains chrome or chromium
            and 'xdg-settings' not in line
            and 'ffmpeg' not in line
        ]
        if output_lines:
            relpath = output_lines[0].split(str(self.playwright_browsers_dir))[-1]
            abspath = self.playwright_browsers_dir / relpath
            if os.path.isfile(abspath) and os.access(abspath, os.X_OK):
                self._browser_abspaths[bin_name] = abspath
        
        return (proc.stderr.strip() + "\n" + proc.stdout.strip()).strip()

PLAYWRIGHT_BINPROVIDER = PlaywrightBinProvider()
