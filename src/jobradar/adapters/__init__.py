import httpx

from jobradar.adapters.ashby import AshbyAdapter
from jobradar.adapters.base import Adapter
from jobradar.adapters.fileai import FileAiAdapter
from jobradar.adapters.greenhouse import GreenhouseAdapter
from jobradar.adapters.japandev import JapanDevAdapter
from jobradar.adapters.lever import LeverAdapter
from jobradar.adapters.michaelpage import MichaelPageAdapter
from jobradar.adapters.mycareersfuture import MyCareersFutureAdapter
from jobradar.adapters.workday import WorkdayAdapter
from jobradar.adapters.ycombinator import YCombinatorAdapter
from jobradar.models import AtsType

ADAPTERS: dict[AtsType, type[Adapter]] = {
    "workday": WorkdayAdapter,
    "greenhouse": GreenhouseAdapter,
    "ashby": AshbyAdapter,
    "lever": LeverAdapter,
    "mycareersfuture": MyCareersFutureAdapter,
    "ycombinator": YCombinatorAdapter,
    "fileai": FileAiAdapter,
    "japandev": JapanDevAdapter,
    "michaelpage": MichaelPageAdapter,
}

# 搜尋型來源：職缺來自很多公司，要略過已經有專屬來源的公司
SEARCH_ATS: set[AtsType] = {"mycareersfuture", "ycombinator", "japandev", "michaelpage"}


class UnsupportedSource(Exception):
    pass


def get_adapter(ats: AtsType, client: httpx.Client) -> Adapter:
    try:
        return ADAPTERS[ats](client)
    except KeyError:
        raise UnsupportedSource(f"{ats} 類型的 adapter 還沒實作") from None
