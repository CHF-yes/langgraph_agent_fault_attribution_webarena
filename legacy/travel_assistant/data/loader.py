"""
数据加载器 - 单例模式，从 JSON 文件加载目的地数据并提供查询接口。
"""

import json
import os
from typing import Optional

from models.destination import Destination
from models.attraction import Attraction
from models.trip import TransportOption, AccommodationOption, CityData


class DestinationDataLoader:
    """目的地数据加载器（单例），加载并缓存所有预设旅游数据。"""

    _instance: Optional["DestinationDataLoader"] = None
    _cities: dict[str, CityData] = {}          # name -> CityData
    _destinations: list[Destination] = []       # 所有目的地列表

    def __new__(cls) -> "DestinationDataLoader":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        """从 JSON 文件加载数据并解析为 dataclass 对象。"""
        json_path = os.path.join(os.path.dirname(__file__), "destinations.json")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in data["destinations"]:
            # 解析目的地
            destination = Destination.from_dict(item)

            # 解析景点
            attractions = [Attraction.from_dict(a) for a in item.get("attractions", [])]

            # 解析交通
            transportation: dict[str, list[TransportOption]] = {}
            for city, options in item.get("transportation", {}).items():
                transportation[city] = [TransportOption.from_dict(city, o) for o in options]

            # 解析住宿
            accommodation: dict[str, AccommodationOption] = {}
            for level, opt in item.get("accommodation", {}).items():
                accommodation[level] = AccommodationOption.from_dict(opt)

            city_data = CityData(
                destination=destination,
                attractions=attractions,
                transportation=transportation,
                accommodation=accommodation,
            )

            self._cities[destination.name] = city_data
            self._destinations.append(destination)

    # ---- 查询接口 ----

    def list_all(self) -> list[Destination]:
        """返回所有目的地摘要列表。"""
        return self._destinations

    def get_by_name(self, name: str) -> Optional[CityData]:
        """按精确名称获取城市完整数据。"""
        return self._cities.get(name)

    def search(self, keyword: str = None, month: str = None,
               interests: list[str] = None) -> list[dict]:
        """
        搜索匹配的目的地。
        - keyword: 模糊匹配城市名
        - month: 匹配最佳旅行月份
        - interests: 匹配兴趣标签
        返回匹配结果列表，每项含目的地信息和匹配分数。
        """
        results = []
        for dest in self._destinations:
            score = 0
            city_data = self._cities[dest.name]

            # 关键词匹配
            if keyword:
                keyword_lower = keyword.lower()
                if keyword_lower in dest.name.lower():
                    score += 10  # 名称匹配，高分
                elif keyword_lower in dest.province.lower():
                    score += 5
                else:
                    # 在标签和描述中搜索
                    for tag in dest.tags:
                        if keyword_lower in tag.lower():
                            score += 3
                            break
                    if keyword_lower in dest.description.lower():
                        score += 2

            # 月份匹配
            if month and month in dest.best_seasons:
                score += 8

            # 兴趣匹配
            if interests:
                for interest in interests:
                    for tag in dest.tags:
                        if interest.lower() in tag.lower():
                            score += 4
                            break

            # 无筛选条件时列出所有
            if keyword is None and month is None and interests is None:
                score = 1

            if score > 0:
                results.append({
                    "destination": dest,
                    "score": score,
                    "attraction_count": len(city_data.attractions),
                    "has_transport_from": list(city_data.transportation.keys()),
                })

        # 按分数降序排列
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def filter_by_tags(self, tags: list[str]) -> list[Destination]:
        """按兴趣标签筛选目的地。"""
        return [
            d for d in self._destinations
            if any(tag.lower() in [t.lower() for t in d.tags] for tag in tags)
        ]

    def filter_by_month(self, month: str) -> list[Destination]:
        """按最佳旅行月份筛选目的地。"""
        return [d for d in self._destinations if month in d.best_seasons]

    def get_all_city_names(self) -> list[str]:
        """返回所有城市名称列表。"""
        return list(self._cities.keys())
