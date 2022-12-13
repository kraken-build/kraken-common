import abc
import argparse
import dataclasses
import hashlib
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, TextIO, Tuple

from kraken.common import NotSet, flatten


def parse_requirement(value: str) -> "PipRequirement | LocalRequirement":
    """
    Parse a string as a requirement. Return a :class:`PipRequirement` or :class:`LocalRequirement`.
    """

    match = re.match(r"(.+?)@(.+)", value)
    if match:
        return LocalRequirement(match.group(1).strip(), Path(match.group(2).strip()))

    match = re.match(r"([\w\d\-\_]+)(.*)", value)
    if match:
        return PipRequirement(match.group(1), match.group(2).strip() or None)

    raise ValueError(f"invalid requirement: {value!r}")


class Requirement(abc.ABC):

    name: str  #: The distribution name.

    @abc.abstractmethod
    def to_args(self, base_dir: Path) -> List[str]:
        """Convert the requirement to Pip args."""

        raise NotImplementedError


@dataclasses.dataclass(frozen=True)
class PipRequirement(Requirement):
    """Represents a Pip requriement."""

    name: str
    spec: "str | None"

    def __str__(self) -> str:
        return f"{self.name}{self.spec or ''}"

    def to_args(self, base_dir: Path) -> List[str]:
        return [str(self)]


@dataclasses.dataclass(frozen=True)
class LocalRequirement(Requirement):
    """Represents a requirement on a local project on the filesystem.

    The string format of a local requirement is `name@path`. The `name` must match the distribution name."""

    name: str
    path: Path

    def __str__(self) -> str:
        return f"{self.name}@{self.path}"

    def to_args(self, base_dir: Path) -> List[str]:
        return [str((base_dir / self.path if base_dir else self.path).absolute())]


@dataclasses.dataclass(frozen=True)
class RequirementSpec:
    """Represents the requirements for a kraken build script."""

    requirements: Tuple[Requirement, ...]
    index_url: "str | None" = None
    extra_index_urls: Tuple[str, ...] = ()
    interpreter_constraint: "str | None" = None
    pythonpath: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for req in self.requirements:
            assert isinstance(req, Requirement), type(req)

    def __eq__(self, other: Any) -> bool:
        # NOTE (@NiklasRosenstein): packaging.requirements.Requirement is not properly equality comparable, so
        #       we implement a custom comparison based on the hash digest.
        if isinstance(other, RequirementSpec):
            return (type(self), self.to_hash()) == (type(other), other.to_hash())
        return False

    def with_requirements(self, reqs: Iterable["str | Requirement"]) -> "RequirementSpec":
        """Adds the given requirements and returns a new instance."""

        requirements = list(self.requirements)
        for req in reqs:
            if isinstance(req, str):
                req = parse_requirement(req)
            requirements.append(req)

        return self.replace(requirements=tuple(requirements))

    def with_pythonpath(self, path: Iterable[str]) -> "RequirementSpec":
        """Adds the given pythonpath and returns a new instance."""

        return self.replace(pythonpath=(*self.pythonpath, *path))

    def replace(
        self,
        requirements: "Iterable[Requirement] | None" = None,
        index_url: "str | None | NotSet" = NotSet.Value,
        extra_index_urls: "Iterable[str] | None" = None,
        interpreter_constraint: "str | None | NotSet" = NotSet.Value,
        pythonpath: "Iterable[str] | None" = None,
    ) -> "RequirementSpec":
        return RequirementSpec(
            requirements=self.requirements if requirements is None else tuple(requirements),
            index_url=self.index_url if index_url is NotSet.Value else index_url,
            extra_index_urls=self.extra_index_urls if extra_index_urls is None else tuple(extra_index_urls),
            interpreter_constraint=self.interpreter_constraint
            if interpreter_constraint is NotSet.Value
            else interpreter_constraint,
            pythonpath=self.pythonpath if pythonpath is None else tuple(pythonpath),
        )

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "RequirementSpec":
        return RequirementSpec(
            requirements=tuple(parse_requirement(x) for x in data["requirements"]),
            index_url=data.get("index_url"),
            extra_index_urls=tuple(data.get("extra_index_urls", ())),
            interpreter_constraint=data.get("interpreter_constraint"),
            pythonpath=tuple(data.get("pythonpath", ())),
        )

    def to_json(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"requirements": [str(x) for x in self.requirements], "pythonpath": self.pythonpath}
        if self.index_url is not None:
            result["index_url"] = self.index_url
        if self.extra_index_urls:
            result["extra_index_urls"] = self.extra_index_urls
        if self.interpreter_constraint:
            result["interpreter_constraint"] = self.interpreter_constraint
        return result

    @staticmethod
    def from_args(args: List[str]) -> "RequirementSpec":
        """Parses the arguments using :mod:`argparse` as if they are Pip install arguments.

        :raise ValueError: If an invalid argument is encountered."""

        parser = argparse.ArgumentParser()
        parser.add_argument("packages", nargs="*")
        parser.add_argument("--index-url")
        parser.add_argument("--extra-index-url", action="append")
        parser.add_argument("--interpreter-constraint")
        parsed, unknown = parser.parse_known_args(args)
        if unknown:
            raise ValueError(f"encountered unknown arguments in requirements: {unknown}")

        return RequirementSpec(
            requirements=tuple(parse_requirement(x) for x in parsed.packages or []),
            index_url=parsed.index_url,
            extra_index_urls=tuple(parsed.extra_index_url or ()),
            interpreter_constraint=parsed.interpreter_constraint,
        )

    def to_args(
        self,
        base_dir: Path = Path("."),
        with_options: bool = True,
        with_requirements: bool = True,
    ) -> List[str]:
        """Converts the requirements back to Pip install arguments.

        :param base_dir: The base directory that relative :class:`LocalRequirement`s should be considered relative to.
        :param with_requirements: Can be set to `False` to not return requirements in the argument, just the index URLs.
        """

        args = []
        if with_options and self.index_url:
            args += ["--index-url", self.index_url]
        if with_options:
            for url in self.extra_index_urls:
                args += ["--extra-index-url", url]
        if with_requirements:
            args += flatten(req.to_args(base_dir) for req in self.requirements)
        return args

    def to_hash(self, algorithm: str = "sha256") -> str:
        """Hash the requirements spec to a hexdigest."""

        hash_parts = [str(req) for req in self.requirements] + ["::pythonpath"] + list(self.pythonpath)
        hash_parts += ["::interpreter_constraint", self.interpreter_constraint or ""]
        return hashlib.new(algorithm, ":".join(hash_parts).encode()).hexdigest()


def parse_requirements_from_python_script(file: TextIO) -> "RequirementSpec | None":
    """
    Parses the requirements defined in a Python script.

    The Pip install arguments are extracted from all lines in the first single-line comment block, which has to start
    at the beginning of the file, which start with the text `# ::requirements` (whitespace optional). Additionally,
    paths to ass to `sys.path` can be specified with `# ::pythonpath`.

    Example:

    ```py
    #!/usr/bin/env python
    # :: requirements PyYAML
    # :: pythonpath ./build-support
    ```

    The resulting :class:`RequirementSpec` will contain `["PyYAML"]` as requirement and `"./build-support"` ain
    the Python path.

    !!! note

        This function is deprecated and is only kept for backwards compatibility, allowing Kraken wrapper to
        continue to support Kraken build scripts using the old requirements schema.
    """

    requirements = []
    pythonpath = []
    for line in map(str.rstrip, file):
        if not line.startswith("#"):
            break
        match = re.match(r"#\s*::\s*(requirements|pythonpath)(.+)", line)
        if not match:
            break
        args = shlex.split(match.group(2))
        if match.group(1) == "requirements":
            requirements += args
        else:
            pythonpath += args

    if requirements or pythonpath:
        return RequirementSpec.from_args(requirements).with_pythonpath(pythonpath)

    return None
