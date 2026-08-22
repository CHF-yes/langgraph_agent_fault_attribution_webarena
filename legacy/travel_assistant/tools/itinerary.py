"""
行程生成工具 - 根据目的地、景点和天数生成按天排列的行程计划。
"""


def generate_daily_plan(
    destination_name: str,
    attractions: list[dict],
    days: int,
) -> dict:
    """
    生成按天排列的行程计划。

    规则：
    - 每天安排 2-3 个景点
    - 按景点类别混合排列（自然 + 人文 + 美食搭配）
    - 上午安排体力消耗大的景点，下午休闲，晚上美食/夜景

    Args:
        destination_name: 目的地名称
        attractions: 景点列表
        days: 旅行天数

    Returns:
        {
            "destination": "舟山",
            "days": 3,
            "daily_plans": [
                {
                    "day": 1,
                    "morning": {"attraction": "...", "activity": "..."},
                    "afternoon": {"attraction": "...", "activity": "..."},
                    "evening": {"activity": "..."}
                },
                ...
            ],
            "tips": "旅行小贴士"
        }
    """
    if not attractions:
        return {
            "destination": destination_name,
            "days": days,
            "daily_plans": [],
            "tips": "暂无景点信息",
        }

    daily_plans = []
    attr_index = 0

    for day in range(1, days + 1):
        plan = {
            "day": day,
            "morning": None,
            "afternoon": None,
            "evening": None,
        }

        # 上午：体力型景点（登山、户外等）
        if attr_index < len(attractions):
            attr = attractions[attr_index]
            plan["morning"] = {
                "attraction": attr["name"],
                "description": attr["description"],
                "ticket_price": attr["ticket_price"],
                "activity": f"上午游览{attr['name']}，建议游览{attr['duration_hours']}小时",
            }
            attr_index += 1

        # 下午：休闲型景点
        if attr_index < len(attractions):
            attr = attractions[attr_index]
            plan["afternoon"] = {
                "attraction": attr["name"],
                "description": attr["description"],
                "ticket_price": attr["ticket_price"],
                "activity": f"下午游览{attr['name']}，建议游览{attr['duration_hours']}小时",
            }
            attr_index += 1

        # 晚上：自由活动 / 美食推荐
        if attr_index < len(attractions) and attractions[attr_index].get("category") in ["美食街区"]:
            food_attr = attractions[attr_index]
            plan["evening"] = {
                "attraction": food_attr["name"],
                "description": food_attr["description"],
                "activity": f"晚上逛{food_attr['name']}，品尝当地特色美食",
            }
            attr_index += 1
        else:
            plan["evening"] = {
                "attraction": "自由活动",
                "description": f"在{destination_name}市区自由探索，品尝当地美食",
                "activity": "晚上自由活动，推荐前往当地夜市或特色餐厅",
            }

        daily_plans.append(plan)

    # 旅行小贴士
    tips = _generate_tips(destination_name, attractions)

    return {
        "destination": destination_name,
        "days": days,
        "daily_plans": daily_plans,
        "tips": tips,
    }


def _generate_tips(destination_name: str, attractions: list[dict]) -> str:
    """根据目的地和景点生成旅行小贴士。"""
    tips_by_city = {
        "舟山": "💡 小贴士：1) 去海岛记得提前查船班时间；2) 夏季注意防晒和防蚊；3) 海鲜虽好但要适量，注意饮食卫生。",
        "南通": "💡 小贴士：1) 濠河夜游建议傍晚出发，夜景最佳；2) 狼山不大但台阶较多，穿舒适鞋子；3) 南通博物苑周一闭馆。",
        "黄山": "💡 小贴士：1) 登山建议带登山杖和雨衣；2) 山顶住宿需提前预订；3) 宏村清晨人少拍照最好看。",
        "厦门": "💡 小贴士：1) 鼓浪屿船票需提前在网上预订；2) 环岛路骑行建议傍晚出发不晒；3) 曾厝垵的小吃可以多尝几样。",
    }
    return tips_by_city.get(destination_name, f"💡 小贴士：祝您在{destination_name}旅途愉快，注意安全和天气变化。")
