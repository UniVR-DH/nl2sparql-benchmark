from abc import ABC, abstractmethod
from typing import Iterable, Union
from pathlib import Path
import io

from ..model import Annotation


class InputAdapter(ABC):
    @abstractmethod
    def read(self, source: Union[Path, io.IOBase, str]):
        raise NotImplementedError()


class OutputAdapter(ABC):
    @abstractmethod
    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ):
        raise NotImplementedError()
