# -*- coding: utf-8 -*-
import logging
import re
import shutil
import tempfile

from collections import defaultdict
from contextlib import contextmanager
from typing import List
from typing import Set
from typing import Union

from poetry.core.utils._compat import Path
from poetry.core.utils._compat import glob
from poetry.core.utils._compat import lru_cache
from poetry.core.utils._compat import to_str
from poetry.core.vcs import get_vcs

from ..metadata import Metadata
from ..utils.include import IncludeFile
from ..utils.module import Module
from ..utils.package_include import PackageInclude


AUTHOR_REGEX = re.compile(r"(?u)^(?P<name>[- .,\w\d'’\"()]+) <(?P<email>.+?)>$")

METADATA_BASE = """\
Metadata-Version: 2.1
Name: {name}
Version: {version}
Summary: {summary}
"""

logger = logging.getLogger(__name__)


class Builder(object):
    AVAILABLE_PYTHONS = {"2", "2.7", "3", "3.4", "3.5", "3.6", "3.7"}

    format = None

    def __init__(
        self, poetry, ignore_packages_formats=False
    ):  # type: ("Poetry", "Env", "IO", bool) -> None
        self._poetry = poetry
        self._package = poetry.package
        self._path = poetry.file.parent
        self._original_path = self._path

        packages = []
        for p in self._package.packages:
            formats = p.get("format", [])
            if not isinstance(formats, list):
                formats = [formats]

            if (
                formats
                and self.format
                and self.format not in formats
                and not ignore_packages_formats
            ):
                continue

            packages.append(p)

        includes = []
        for i in self._package.include:
            formats = i.get("format", [])

            if (
                formats
                and self.format
                and self.format not in formats
                and not ignore_packages_formats
            ):
                continue

            includes.append(i)

        self._module = Module(
            self._package.name,
            self._path.as_posix(),
            packages=packages,
            includes=includes,
        )
        self._meta = Metadata.from_package(self._package)

    def build(self):
        raise NotImplementedError()

    @lru_cache(maxsize=None)
    def find_excluded_files(self):  # type: () -> Set[str]
        # Checking VCS
        vcs = get_vcs(self._original_path)
        if not vcs:
            vcs_ignored_files = set()
        else:
            vcs_ignored_files = set(vcs.get_ignored_files())

        explicitely_excluded = set()
        for excluded_glob in self._package.exclude:

            for excluded in glob(
                Path(self._path, excluded_glob).as_posix(), recursive=True
            ):
                explicitely_excluded.add(
                    Path(excluded).relative_to(self._path).as_posix()
                )

        ignored = vcs_ignored_files | explicitely_excluded
        result = set()
        for file in ignored:
            result.add(file)

        # The list of excluded files might be big and we will do a lot
        # containment check (x in excluded).
        # Returning a set make those tests much much faster.
        return result

    def is_excluded(self, filepath):  # type: (Union[str, Path]) -> bool
        exclude_path = Path(filepath)

        while True:
            if exclude_path.as_posix() in self.find_excluded_files():
                return True

            if len(exclude_path.parts) > 1:
                exclude_path = exclude_path.parent
            else:
                break

        return False

    def find_files_to_add(
        self, exclude_build=True
    ):  # type: (bool) -> List[IncludeFile]
        """
        Finds all files to add to the tarball
        """
        to_add = []

        for include in self._module.includes:
            include.refresh()
            formats = include.formats or ["sdist"]

            for file in include.elements:
                if "__pycache__" in str(file):
                    continue

                if file.is_dir():
                    if self.format in formats:
                        for f in file.glob("**/*"):
                            rel_path = f.relative_to(self._path)

                            if (
                                rel_path not in set([t.path for t in to_add])
                                and not f.is_dir()
                                and not self.is_excluded(rel_path)
                            ):
                                to_add.append(
                                    IncludeFile(path=rel_path, source_root=self._path)
                                )
                    continue

                if (
                    isinstance(include, PackageInclude)
                    and include.source
                    and self.format == "wheel"
                ):
                    source_root = include.base
                else:
                    source_root = self._path

                file = file.relative_to(source_root)
                include_file = IncludeFile(path=file, source_root=source_root.resolve())

                if self.is_excluded(file) and isinstance(include, PackageInclude):
                    continue

                if file.suffix == ".pyc":
                    continue

                if file in set([f.path for f in to_add]):
                    # Skip duplicates
                    continue

                logger.debug(" - Adding: {}".format(str(file)))
                to_add.append(include_file)

        # If a build script is specified and explicitly required
        # we add it to the list of files
        if self._package.build and not exclude_build:
            to_add.append(
                IncludeFile(path=Path(self._package.build), source_root=self._path)
            )

        return to_add

    def get_metadata_content(self):  # type: () -> bytes
        content = METADATA_BASE.format(
            name=self._meta.name,
            version=self._meta.version,
            summary=to_str(self._meta.summary),
        )

        # Optional fields
        if self._meta.home_page:
            content += "Home-page: {}\n".format(self._meta.home_page)

        if self._meta.license:
            content += "License: {}\n".format(self._meta.license)

        if self._meta.keywords:
            content += "Keywords: {}\n".format(self._meta.keywords)

        if self._meta.author:
            content += "Author: {}\n".format(to_str(self._meta.author))

        if self._meta.author_email:
            content += "Author-email: {}\n".format(to_str(self._meta.author_email))

        if self._meta.maintainer:
            content += "Maintainer: {}\n".format(to_str(self._meta.maintainer))

        if self._meta.maintainer_email:
            content += "Maintainer-email: {}\n".format(
                to_str(self._meta.maintainer_email)
            )

        if self._meta.requires_python:
            content += "Requires-Python: {}\n".format(self._meta.requires_python)

        for classifier in self._meta.classifiers:
            content += "Classifier: {}\n".format(classifier)

        for extra in sorted(self._meta.provides_extra):
            content += "Provides-Extra: {}\n".format(extra)

        for dep in sorted(self._meta.requires_dist):
            content += "Requires-Dist: {}\n".format(dep)

        for url in sorted(self._meta.project_urls, key=lambda u: u[0]):
            content += "Project-URL: {}\n".format(to_str(url))

        if self._meta.description_content_type:
            content += "Description-Content-Type: {}\n".format(
                self._meta.description_content_type
            )

        if self._meta.description is not None:
            content += "\n" + to_str(self._meta.description) + "\n"

        return content

    def convert_entry_points(self):  # type: () -> dict
        result = defaultdict(list)

        # Scripts -> Entry points
        for name, ep in self._poetry.local_config.get("scripts", {}).items():
            extras = ""
            if isinstance(ep, dict):
                extras = "[{}]".format(", ".join(ep["extras"]))
                ep = ep["callable"]

            result["console_scripts"].append("{} = {}{}".format(name, ep, extras))

        # Plugins -> entry points
        plugins = self._poetry.local_config.get("plugins", {})
        for groupname, group in plugins.items():
            for name, ep in sorted(group.items()):
                result[groupname].append("{} = {}".format(name, ep))

        for groupname in result:
            result[groupname] = sorted(result[groupname])

        return dict(result)

    @classmethod
    def convert_author(cls, author):  # type: (...) -> dict
        m = AUTHOR_REGEX.match(author)

        name = m.group("name")
        email = m.group("email")

        return {"name": name, "email": email}

    @classmethod
    @contextmanager
    def temporary_directory(cls, *args, **kwargs):
        try:
            from tempfile import TemporaryDirectory

            with TemporaryDirectory(*args, **kwargs) as name:
                yield name
        except ImportError:
            name = tempfile.mkdtemp(*args, **kwargs)

            yield name

            shutil.rmtree(name)
