"""
住宿费用计算工具 - 按目的地、天数和档次计算住宿总费用。
"""

from data.loader import DestinationDataLoader


def calculate_accommodation(
    destination_name: str,
    days: int,
    level: str = "standard",
) -> dict:
    """
    计算住宿费用。

    Args:
        destination_name: 目的地城市
        days: 旅行天数（住宿天数 = days - 1）
        level: 住宿档次 "budget"(经济) / "standard"(舒适) / "luxury"(豪华)

    Returns:
        {
            "destination": "舟山",
            "level": "standard",
            "label": "舒适型",
            "price_per_night": 350,
            "nights": 2,
            "total": 700
        }
    """
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)

    nights = max(1, days - 1)  # 至少 1 晚

    if not city or level not in city.accommodation:
        # 降级到 standard 或 budget
        if city and "standard" in city.accommodation:
            level = "standard"
        elif city and "budget" in city.accommodation:
            level = "budget"
        else:
            return {
                "destination": destination_name,
                "level": "unknown",
                "label": "未知",
                "price_per_night": 0,
                "nights": nights,
                "total": 0,
            }

    opt = city.accommodation[level]
    return {
        "destination": destination_name,
        "level": opt.level,
        "label": opt.label,
        "price_per_night": opt.price_per_night,
        "nights": nights,
        "total": opt.price_per_night * nights,
    }


def get_accommodation_options(destination_name: str) -> list[dict]:
    """获取某目的地的所有住宿档次选项。"""
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return []
    return [
        {
            "level": o.level,
            "label": o.label,
            "price_per_night": o.price_per_night,
        }
        for o in city.accommodation.values()
    ]
