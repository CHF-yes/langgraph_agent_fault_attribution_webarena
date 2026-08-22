"""
景点查询工具 - 按目的地和偏好筛选景点。
"""

from typing import Optional

from data.loader import DestinationDataLoader


def get_attractions(
    destination_name: str,
    interests: list[str] = None,
    days: int = 3,
) -> list[dict]:
    """
    获取指定城市的景点列表，按天数和兴趣筛选。

    Args:
        destination_name: 城市名称
        interests: 兴趣标签过滤（如 ["海滩", "登山"]）
        days: 旅行天数，用于控制推荐数量

    Returns:
        景点列表，每个含 name, category, description, ticket_price, duration_hours, tags
    """
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return []

    attractions = city.attractions

    # 按兴趣过滤
    if interests:
        filtered = []
        for attr in attractions:
            attr_tags_lower = [t.lower() for t in attr.tags]
            for interest in interests:
                if interest.lower() in attr_tags_lower:
                    filtered.append(attr)
                    break
        if filtered:
            attractions = filtered

    # 按天数推荐合理数量（每天 2-3 个景点）
    max_count = days * 3
    attractions = attractions[:max_count]

    return [
        {
            "name": a.name,
            "category": a.category,
            "description": a.description,
            "ticket_price": a.ticket_price,
            "duration_hours": a.duration_hours,
            "tags": a.tags,
        }
        for a in attractions
    ]


def get_attraction_detail(destination_name: str, attraction_name: str) -> Optional[dict]:
    """获取单个景点的详细信息。"""
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return None
    for attr in city.attractions:
        if attr.name == attraction_name:
            return {
                "name": attr.name,
                "category": attr.category,
                "description": attr.description,
                "ticket_price": attr.ticket_price,
                "duration_hours": attr.duration_hours,
                "tags": attr.tags,
            }
    return None
