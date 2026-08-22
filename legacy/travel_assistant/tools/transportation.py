"""
交通费用计算工具 - 根据不同出发地和目的地计算往返交通费用。
"""

from typing import Optional

from data.loader import DestinationDataLoader


def calculate_transportation(
    departure_city: str,
    destination_name: str,
    travelers: int = 1,
    preferred_mode: str = None,
) -> Optional[dict]:
    """
    计算从出发地到目的地的往返交通费用。

    Args:
        departure_city: 出发城市（如 "上海"）
        destination_name: 目的地城市（如 "舟山"）
        travelers: 出行人数
        preferred_mode: 偏好交通方式（"高铁"/"飞机"/"自驾"/"大巴"），不传则选最优

    Returns:
        {
            "departure_city": "上海",
            "destination": "舟山",
            "mode": "高铁",
            "unit_price": 150,           # 单人单程
            "duration_hours": 2.5,
            "travelers": 2,
            "round_trip_total": 600      # 多人往返总价
        }
        如果出发城市不在预设中，返回 None
    """
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return None

    transport_options = city.transportation.get(departure_city)
    if not transport_options:
        # 出发城市不在预设中，尝试找最近的城市
        return None

    # 选择交通方式
    selected = None
    if preferred_mode:
        for opt in transport_options:
            if opt.mode == preferred_mode:
                selected = opt
                break

    if not selected:
        # 默认选第一个（通常按推荐排序）
        selected = transport_options[0]

    round_trip_total = selected.unit_price * 2 * travelers

    return {
        "departure_city": departure_city,
        "destination": destination_name,
        "mode": selected.mode,
        "unit_price": selected.unit_price,
        "duration_hours": selected.duration_hours,
        "travelers": travelers,
        "round_trip_total": round_trip_total,
    }


def get_available_departures(destination_name: str) -> list[str]:
    """获取某目的地支持的所有出发城市。"""
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return []
    return list(city.transportation.keys())


def get_transport_options(departure_city: str, destination_name: str) -> list[dict]:
    """获取两城市间的所有交通选项。"""
    loader = DestinationDataLoader()
    city = loader.get_by_name(destination_name)
    if not city:
        return []
    options = city.transportation.get(departure_city, [])
    return [
        {
            "mode": o.mode,
            "unit_price": o.unit_price,
            "duration_hours": o.duration_hours,
        }
        for o in options
    ]
