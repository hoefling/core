from typing import List
from typing import Optional

from poetry.core.utils._compat import Path


class Include(object):
    """
    Represents an "include" entry.

    It can be a glob string, a single file or a directory.

    This class will then detect the type of this include:

        - a package
        - a module
        - a file
        - a directory
    """

    def __init__(
        self, base, include, formats=None
    ):  # type: (Path, str, Optional[List[str]]) -> None
        self._base = base
        self._include = str(include)
        self._formats = formats

        self._elements = sorted(list(self._base.glob(str(self._include))))

    @property
    def base(self):  # type: () -> Path
        return self._base

    @property
    def elements(self):  # type: () -> List[Path]
        return self._elements

    @property
    def formats(self):  # type: () -> Optional[List[str]]
        return self._formats

    def is_empty(self):  # type: () -> bool
        return len(self._elements) == 0

    def refresh(self):  # type: () -> Include
        self._elements = sorted(list(self._base.glob(self._include)))

        return self


class IncludeFile:
    def __init__(
        self,
        path,  # type: Path
        source_root=None,  # type: Optional[Path]
    ):
        self.path = path
        self.source_root = source_root or Path(".")

    def __repr__(self):  # type: () -> str
        return str(self.path)

    @property
    def rel_path(self):  # type: () -> Path
        return self.path

    @property
    def full_path(self):  # type: () -> Path
        return self.source_root / self.path
