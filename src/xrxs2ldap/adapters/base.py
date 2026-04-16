from __future__ import annotations

from abc import ABC, abstractmethod

from xrxs2ldap.models import HrSnapshot


class HrAdapter(ABC):
    @abstractmethod
    def fetch_snapshot(self) -> HrSnapshot:
        raise NotImplementedError
