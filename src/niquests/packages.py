from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import typing

from ._compat import HAS_LEGACY_URLLIB3

# just to enable smooth type-completion!
if typing.TYPE_CHECKING:
    import charset_normalizer as chardet
    import urllib3

    charset_normalizer = chardet

    import idna  # type: ignore[import-not-found]

# Mapping of aliased package prefixes:
#   "niquests.packages.<alias>." to "<real-package>."
# Populated by the loop below, consumed by the import hook.
_ALIAS_TO_REAL: dict[str, str] = {}

# This code exists for backwards compatibility reasons.
# I don't like it either. Just look the other way. :)
for package in (
    "urllib3",
    "charset_normalizer",
    "idna",
    "chardet",
):
    to_be_imported: str = package

    if package == "chardet":
        to_be_imported = "charset_normalizer"
    elif package == "urllib3" and HAS_LEGACY_URLLIB3:
        to_be_imported = "urllib3_future"

    try:
        locals()[package] = __import__(to_be_imported)
    except ImportError:
        continue  # idna could be missing. not required!

    # Determine the alias prefix (what niquests code imports)
    # and the real prefix (the actual installed package).
    if package == "chardet":
        alias_prefix = "niquests.packages.chardet."
        real_prefix = "charset_normalizer."
        alias_root = "niquests.packages.chardet"
        real_root = "charset_normalizer"
    elif package == "urllib3" and HAS_LEGACY_URLLIB3:
        alias_prefix = "niquests.packages.urllib3."
        real_prefix = "urllib3_future."
        alias_root = "niquests.packages.urllib3"
        real_root = "urllib3_future"
    else:
        alias_prefix = f"niquests.packages.{package}."
        real_prefix = f"{package}."
        alias_root = f"niquests.packages.{package}"
        real_root = package

    _ALIAS_TO_REAL[alias_prefix] = real_prefix
    _ALIAS_TO_REAL[alias_root] = real_root

    # This traversal is apparently necessary such that the identities are
    # preserved (requests.packages.urllib3.* is urllib3.*)
    for mod in list(sys.modules):
        if mod == to_be_imported or mod.startswith(f"{to_be_imported}."):
            inner_mod = mod

            if HAS_LEGACY_URLLIB3 and inner_mod == "urllib3_future" or inner_mod.startswith("urllib3_future."):
                inner_mod = inner_mod.replace("urllib3_future", "urllib3")
            elif inner_mod == "charset_normalizer":
                inner_mod = "chardet"

            try:
                sys.modules[f"niquests.packages.{inner_mod}"] = sys.modules[mod]
            except KeyError:
                continue


class _AliasModuleLoader(importlib.abc.Loader):
    """A loader that returns an already-imported module without re-executing it."""

    def __init__(self, real_module: typing.Any) -> None:
        self._real_module = real_module

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> typing.Any:
        pass

    def exec_module(self, module: typing.Any) -> None:
        pass


class _NiquestsPackagesAliasImporter(importlib.abc.MetaPathFinder):
    """Made to avoid duplicate due to lazy imports at urllib3-future side(...)"""

    def find_spec(
        self,
        fullname: str,
        path: typing.Any = None,
        target: typing.Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        pass


# Insert at front so we intercept before the default PathFinder.
sys.meta_path.insert(0, _NiquestsPackagesAliasImporter())


__all__ = (
    "urllib3",
    "chardet",
    "charset_normalizer",
    "idna",
)
