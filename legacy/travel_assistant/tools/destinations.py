"""
目的地查询工具 - 封装预设数据的搜索和查询接口。
"""

from typing import Optional

from data.loader import DestinationDataLoader


def search_destinations(
    keyword: str = None,
    month: str = None,
    interests: list[str] = None,
) -> list[dict]:
    """
    搜索匹配的旅游目的地。

    Args:
        keyword: 城市名关键词（支持模糊匹配）
        month: 出行月份，用于匹配最佳季节
        interests: 兴趣标签列表（如 ["海滩", "山景", "古镇"]）

    Returns:
        匹配的目的地列表，每项含目的地信息和匹配分数
    """
    loader = DestinationDataLoader()
    return loader.search(keyword=keyword, month=month, interests=interests)


def get_destination_detail(name: str) -> Optional[dict]:
    """
    按名称获取目的地完整详情（含景点、交通、住宿）。

    Args:
        name: 目的地城市名称

    Returns:
        包含 destination, attractions, transportation, accommodation 的字典
    """
    loader = DestinationDataLoader()
    city = loader.get_by_name(name)
    if not city:
        return None
    return {
        "destination": city.destination.to_dict(),
        "attractions": [a.to_dict() for a in city.attractions],
        "transportation": {
            from_city: [t.to_dict() for t in options]
            for from_city, options in city.transportation.items()
        },
        "accommodation": {
            level: opt.to_dict()
            for level, opt in city.accommodation.items()
        },
    }


def list_all_destinations() -> list[dict]:
    """
    列出所有可用目的地的摘要信息。

    Returns:
        目的地摘要列表
    """
    loader = DestinationDataLoader()
    results = loader.list_all()
    return [
        {
            "name": d.name,
            "province": d.province,
            "description": d.description,
            "tags": d.tags,
            "best_seasons": d.best_seasons,
        }
        for d in results
    ]


def get_all_city_names() -> list[str]:
    """获取所有支持的城市名称列表。"""
    return DestinationDataLoader().get_all_city_names()
