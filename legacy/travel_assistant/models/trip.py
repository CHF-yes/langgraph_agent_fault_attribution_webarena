"""行程相关数据模型"""

from dataclasses import dataclass, field


@dataclass
class TransportOption:
    """交通选项"""
    mode: str                    # "高铁", "飞机", "自驾", "大巴"
    unit_price: float            # 单人单程价格
    duration_hours: float        # 耗时
    from_city: str               # 出发城市

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "unit_price": self.unit_price,
            "duration_hours": self.duration_hours,
        }

    @classmethod
    def from_dict(cls, from_city: str, data: dict) -> "TransportOption":
        return cls(
            mode=data["mode"],
            unit_price=data["unit_price"],
            duration_hours=data["duration_hours"],
            from_city=from_city,
        )


@dataclass
class AccommodationOption:
    """住宿选项"""
    level: str                   # "budget", "standard", "luxury"
    label: str                   # "经济型", "舒适型", "豪华型"
    price_per_night: float       # 每晚价格

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "label": self.label,
            "price_per_night": self.price_per_night,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AccommodationOption":
        return cls(
            level=data["level"],
            label=data["label"],
            price_per_night=data["price_per_night"],
        )


@dataclass
class CityData:
    """城市完整旅游数据"""
    destination: object          # Destination
    attractions: list            # list[Attraction]
    transportation: dict         # {from_city: [TransportOption]}
    accommodation: dict          # {level: AccommodationOption}
