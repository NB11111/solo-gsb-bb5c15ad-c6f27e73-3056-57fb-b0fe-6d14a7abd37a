"""口令版本三方合并核心逻辑（纯标准库实现，不依赖 Web 框架）。

规则概述
--------
* 输入为 base / left / right 三份与 ``POST /compile`` 同格式的口令数组，
  先补全默认字段（``anchor`` 缺省 ``START``、``offset_ms`` 缺省 0），
  再以 ``id`` 为合并键、逐字段做三方裁决。
* 逐字段裁决：两侧取值相同（含都未改、都删除、双侧新增内容一致）直接采纳；
  仅一侧相对 base 改动则采纳该侧；两侧改成不同值记一条分歧。
  口令的删除视为该口令全部字段变为「缺席」，因此一侧删除而另一侧修改时，
  被修改的字段记分歧；双侧同时删除视为相同改动，直接采纳删除。
* 同 id 双侧新增但内容不同时，同样按字段记分歧。分歧按 ``(id, field)``
  排序返回；只要存在分歧就不生成合并结果。
* 无分歧时把合并口令交给 ``app.compiler.compile_commands`` 校验并编排，
  缺失锚点、依赖环、负起点等沿用 /compile 的原错误码；成功时返回按 id
  排序的合并口令，以及与 /compile 同结构的时间线和冲突对。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .compiler import START, CompileError, compile_commands

# 参与逐字段合并的口令字段（id 是合并键，不参与裁决）
MERGE_FIELDS = ("channel", "anchor", "offset_ms", "duration_ms")

# 字段级哨兵：该侧不存在此口令（未新增或已删除）
_ABSENT = object()


class MergeError(CompileError):
    """三方合并失败的业务异常；复用 CompileError 的 422 转换通道。"""


def merge_versions(
    base: list[dict[str, Any]],
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> dict[str, Any]:
    """对 base / left / right 三份口令做按 id、按字段的三方合并。

    不会修改传入的三份数组。返回字典始终包含 ``divergences``（按
    ``(id, field)`` 排序）；无分歧时另含 ``merged``（按 id 排序的合并
    口令）、``timeline`` 与 ``conflicts``（与 /compile 同结构），
    有分歧时这三项为 ``None``。

    :raises MergeError: 任一份数组内部存在重复 id 时抛出（``DUPLICATE_ID``）。
    :raises CompileError: 无分歧但合并口令编排非法（缺失锚点、依赖环、
        负起点等，与 /compile 相同）。
    """
    base_by_id = _index_by_id(base, "base")
    left_by_id = _index_by_id(left, "left")
    right_by_id = _index_by_id(right, "right")

    divergences: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []

    for cid in sorted(set(base_by_id) | set(left_by_id) | set(right_by_id)):
        base_item = base_by_id.get(cid)
        left_item = left_by_id.get(cid)
        right_item = right_by_id.get(cid)

        merged_fields: dict[str, Any] = {}
        for field in MERGE_FIELDS:
            bv = base_item[field] if base_item is not None else _ABSENT
            lv = left_item[field] if left_item is not None else _ABSENT
            rv = right_item[field] if right_item is not None else _ABSENT
            if lv == rv:
                # 两侧相同：都未改、改成相同值、都删除或双侧新增内容一致
                merged_fields[field] = lv
            elif lv == bv:
                merged_fields[field] = rv  # 仅右侧改动
            elif rv == bv:
                merged_fields[field] = lv  # 仅左侧改动
            else:
                # 两侧改成不同值，或一侧删除（缺席）而另一侧修改
                divergences.append(
                    {
                        "id": cid,
                        "field": field,
                        "base": None if bv is _ABSENT else bv,
                        "left": None if lv is _ABSENT else lv,
                        "right": None if rv is _ABSENT else rv,
                    }
                )
        # 无分歧时同一 id 的字段要么全部缺席（口令被删除），要么齐全；
        # 有分歧时合并结果整体不生成，这里的半截字段不会进入响应
        if all(value is not _ABSENT for value in merged_fields.values()):
            merged.append({"id": cid, **merged_fields})

    divergences.sort(key=lambda item: (item["id"], item["field"]))
    if divergences:
        return {
            "divergences": divergences,
            "merged": None,
            "timeline": None,
            "conflicts": None,
        }

    # 无分歧：交给现有编排器校验合并口令并生成时间线、冲突对
    # （深拷贝，编排器的 setdefault 不会回写合并结果）
    compiled = compile_commands(deepcopy(merged))
    return {
        "divergences": [],
        "merged": merged,
        "timeline": compiled["timeline"],
        "conflicts": compiled["conflicts"],
    }


def _index_by_id(
    items: list[dict[str, Any]], side: str
) -> dict[str, dict[str, Any]]:
    """补全默认字段后按 id 建索引（只读取拷贝，不改调用方数据）。"""
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for item in items:
        cid = item["id"]
        if cid in by_id:
            if cid not in duplicates:
                duplicates.append(cid)
            continue
        by_id[cid] = {
            "channel": item["channel"],
            "anchor": item.get("anchor", START),
            "offset_ms": item.get("offset_ms", 0),
            "duration_ms": item["duration_ms"],
        }
    if duplicates:
        raise MergeError(
            "DUPLICATE_ID",
            f"{side} 口令数组中存在重复的口令 id: {', '.join(sorted(duplicates))}",
            {"side": side, "duplicates": sorted(duplicates)},
        )
    return by_id
