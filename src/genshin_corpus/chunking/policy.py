"""Versioned, conservative admission rules over observed OBC structure."""
from __future__ import annotations

POLICY_VERSION = "gc-story-admission-pilot-2"

# These components have a single observed mechanical/editorial purpose.
MECHANICAL_COMPONENTS = {
    "role_ascension", "equipment_growth_info", "recommend", "strategy",
    "spiral_abyss_target",
}
MECHANICAL_SECTIONS = {
    "任务奖励", "奖励", "攻略方法", "攻略推荐", "推荐装备", "角色突破",
    "天赋演示", "获取方式", "获取途径", "商店", "商品列表", "配装推荐",
}
MECHANICAL_LABELS = {
    "攻击力", "基础攻击力", "防御力", "基础防御力", "生命值", "基础生命值",
    "生命值上限", "暴击率", "暴击伤害", "元素精通", "元素充能效率",
    "技能伤害", "冷却时间", "元素能量", "购买价格", "售价", "价格", "库存",
    "数量", "好感度", "突破材料", "培养材料", "获得方式", "获取方式",
    "获取途径", "星级", "品质", "兑换价格", "刷新时间",
}
CONTROL_KEYS = {
    "moduleName", "tab_id", "tab_name", "id", "ep_id", "content_id", "icon",
    "img", "image", "image_url", "avatar_m", "avatar_pc", "icon_url", "link",
    "audio", "audio_url", "audio_name", "video", "video_url", "cover", "star", "order",
    "is_show_switch", "baseInfoSwitchDisabled", "role_attribute", "energy_bg",
    "gold_img", "common_img", "title", "name", "key", "nickname",
}
OUTFIT_MECHANICAL_LABELS = {"部件数量", "适用性别", "色系", "部件/色系"}
FURNISHING_MECHANICAL_LABELS = {
    "摆设获取途径", "摆设图纸获取途径", "摆设获取方式", "图纸获取方式",
    "洞天仙力", "摆放区域", "一级分类", "二级分类", "相关套装",
}


def normalized_label(text: str) -> str:
    """Match source field labels despite surrounding whitespace or a final colon."""
    return text.strip().rstrip("：:").strip()


def admission(component: str, section_title: str, field_keys: list[str],
              field_labels: list[str], kind: str, *,
              channel_names: set[str] | None = None) -> tuple[str, str]:
    """No prose keyword classification. Unknown/mixed text stays admitted."""
    if kind in {"media", "separator"}:
        return "resource", "resource_or_separator"
    if component in MECHANICAL_COMPONENTS:
        return "exclude", "mechanical_component"
    if section_title in MECHANICAL_SECTIONS:
        return "exclude", "mechanical_section"
    labels = {normalized_label(text) for text in field_labels}
    if labels.intersection(MECHANICAL_LABELS):
        return "exclude", "mechanical_field_label"
    channels = channel_names or set()
    if component == "material_base_info" and "千星奇域" in channels and labels.intersection(OUTFIT_MECHANICAL_LABELS):
        return "exclude", "outfit_mechanical_field"
    if component in {"good_desc", "rich_base_info", "cave_base_info"} and "洞天" in channels and labels.intersection(FURNISHING_MECHANICAL_LABELS):
        return "exclude", "furnishing_mechanical_field"
    paths = [key.partition(":")[2] for key in field_keys]
    if any(path.endswith("/materials") or "/materials/" in path
           or path.endswith("/feedback") or "/feedback/" in path for path in paths):
        return "exclude", "materials_or_rewards"
    if component == "role_talent" and any("/attr" in path for path in paths):
        return "exclude", "talent_numeric_table"
    if field_keys and field_keys[-1].rsplit("/", 1)[-1] in CONTROL_KEYS:
        return "context", "structural_or_resource_field"
    if kind == "scalar":
        return "include", "uncertain_scalar"
    if kind == "opaque":
        return "review", "unsupported_payload"
    if component == "role_talent":
        return "include", "mixed_talent_description"
    return "include", "conservative_text"
