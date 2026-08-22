"""目的地数据模型"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Destination:
    """旅游目的地城市"""
    name: str                              # 城市名，如 "舟山"
    province: str                          # 省份
    description: str                       # 简介
    tags: list[str] = field(default_factory=list)         # 标签: ["海滩", "海鲜", "佛教文化"]
    best_seasons: list[str] = field(default_factory=list) # 最佳月份: ["5月", "6月"]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "province": self.province,
            "description": self.description,
            "tags": self.tags,
            "best_seasons": self.best_seasons,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Destination":
        return cls(
            name=data["name"],
            province=data["province"],
            description=data["description"],
            tags=data.get("tags", []),
            best_seasons=data.get("best_seasons", []),
        )
