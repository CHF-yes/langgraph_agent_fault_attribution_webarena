"""景点数据模型"""

from dataclasses import dataclass, field


@dataclass
class Attraction:
    """旅游景点"""
    name: str                    # 景点名
    category: str                # 类别: "自然景观", "人文古迹", "美食街区"
    description: str             # 简介
    ticket_price: float          # 门票价（0 表示免费）
    duration_hours: float        # 建议游览时长
    tags: list[str] = field(default_factory=list)  # 标签: ["海景", "登山"]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "ticket_price": self.ticket_price,
            "duration_hours": self.duration_hours,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Attraction":
        return cls(
            name=data["name"],
            category=data["category"],
            description=data["description"],
            ticket_price=data.get("ticket_price", 0),
            duration_hours=data.get("duration_hours", 2),
            tags=data.get("tags", []),
        )
