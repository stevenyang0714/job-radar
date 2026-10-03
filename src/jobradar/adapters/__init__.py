import httpx

from jobradar.adapters.base import Adapter
from jobradar.adapters.workday import WorkdayAdapter
from jobradar.models import AtsType

ADAPTERS: dict[AtsType, type[Adapter]] = {
    "workday": WorkdayAdapter,
}


class UnsupportedSource(Exception):
    pass


def get_adapter(ats: AtsType, client: httpx.Client) -> Adapter:
    try:
        return ADAPTERS[ats](client)
    except KeyError:
        raise UnsupportedSource(f"{ats} 類型的 adapter 還沒實作") from None
