"""app.merge 的单元测试（仅依赖标准库，可直接 `python -m unittest discover`）。"""

from __future__ import annotations

import copy
import unittest

from app.compiler import START, CompileError
from app.merge import MergeError, merge_versions


def cmd(cid, channel, anchor=START, offset=0, duration=1000):
    return {
        "id": cid,
        "channel": channel,
        "anchor": anchor,
        "offset_ms": offset,
        "duration_ms": duration,
    }


def merged_by_id(result):
    return {item["id"]: item for item in result["merged"]}


class CleanMergeTests(unittest.TestCase):
    def test_empty_versions(self):
        result = merge_versions([], [], [])
        self.assertEqual(
            result,
            {"divergences": [], "merged": [], "timeline": [], "conflicts": []},
        )

    def test_identical_versions_merge_to_base(self):
        base = [cmd("b", "sub"), cmd("a", "main")]
        result = merge_versions(base, copy.deepcopy(base), copy.deepcopy(base))
        self.assertEqual(result["divergences"], [])
        # 合并口令按 id 排序，与输入顺序无关
        self.assertEqual([item["id"] for item in result["merged"]], ["a", "b"])

    def test_single_side_modify_adopted(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 0, 1200)]
        right = [cmd("a", "main", START, 0, 1000)]
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        self.assertEqual(merged_by_id(result)["a"]["duration_ms"], 1200)

    def test_single_side_add_and_delete_adopted(self):
        base = [cmd("a", "main"), cmd("c", "sub")]
        left = [cmd("a", "main")]  # 删除 c
        right = [cmd("a", "main"), cmd("c", "sub"), cmd("d", "sub", "a", 0, 300)]  # 新增 d
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        self.assertEqual([item["id"] for item in result["merged"]], ["a", "d"])

    def test_both_sides_same_change_adopted(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 100, 800)]
        right = [cmd("a", "main", START, 100, 800)]
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        item = merged_by_id(result)["a"]
        self.assertEqual((item["offset_ms"], item["duration_ms"]), (100, 800))

    def test_both_sides_delete_adopted(self):
        base = [cmd("a", "main"), cmd("b", "main", "a")]
        result = merge_versions(base, [cmd("a", "main")], [cmd("a", "main")])
        self.assertEqual(result["divergences"], [])
        self.assertEqual([item["id"] for item in result["merged"]], ["a"])

    def test_different_fields_merge_into_one_command(self):
        # 左侧改 duration，右侧改 offset：合并到同一口令
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 0, 1200)]
        right = [cmd("a", "main", START, 200, 1000)]
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        item = merged_by_id(result)["a"]
        self.assertEqual(item["offset_ms"], 200)
        self.assertEqual(item["duration_ms"], 1200)
        # 时间线随之更新：起点 200，结束 200 + 1200
        entry = result["timeline"][0]
        self.assertEqual((entry["start_ms"], entry["end_ms"]), (200, 1400))

    def test_both_sides_add_identical_command(self):
        base = [cmd("a", "main")]
        added = cmd("d", "sub", "a", 0, 300)
        result = merge_versions(
            base, [cmd("a", "main"), dict(added)], [cmd("a", "main"), dict(added)]
        )
        self.assertEqual(result["divergences"], [])
        self.assertEqual([item["id"] for item in result["merged"]], ["a", "d"])

    def test_defaults_filled_before_merge(self):
        # 缺省 anchor / offset_ms 与显式 START / 0 视为相同取值，不产生分歧
        base = [{"id": "a", "channel": "main", "duration_ms": 1000}]
        left = [cmd("a", "main", START, 0, 1000)]
        right = [{"id": "a", "channel": "main", "duration_ms": 1000}]
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        item = merged_by_id(result)["a"]
        self.assertEqual(item["anchor"], START)
        self.assertEqual(item["offset_ms"], 0)

    def test_timeline_and_conflicts_follow_compile_structure(self):
        # 合并结果产生同通道重叠时，conflicts 与 /compile 同结构
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 0, 1000), cmd("b", "main", START, 500, 500)]
        right = [cmd("a", "main", START, 0, 1000)]
        result = merge_versions(base, left, right)
        self.assertEqual(result["divergences"], [])
        self.assertEqual(
            [(pair["a"], pair["b"], pair["channel"]) for pair in result["conflicts"]],
            [("a", "b", "main")],
        )
        pair = result["conflicts"][0]
        self.assertEqual(pair["a_interval"], [0, 1000])
        self.assertEqual(pair["b_interval"], [500, 1000])

    def test_inputs_are_not_mutated(self):
        base = [{"id": "a", "channel": "main", "duration_ms": 1000}]
        left = [cmd("a", "main", START, 0, 1200)]
        right = [cmd("a", "main", START, 200, 1000)]
        snapshot = copy.deepcopy((base, left, right))
        merge_versions(base, left, right)
        self.assertEqual((base, left, right), snapshot)


class DivergenceTests(unittest.TestCase):
    def test_same_field_changed_to_different_values(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 0, 1200)]
        right = [cmd("a", "main", START, 0, 1500)]
        result = merge_versions(base, left, right)
        self.assertEqual(
            result["divergences"],
            [
                {
                    "id": "a",
                    "field": "duration_ms",
                    "base": 1000,
                    "left": 1200,
                    "right": 1500,
                }
            ],
        )
        # 有分歧时不生成合并结果
        self.assertIsNone(result["merged"])
        self.assertIsNone(result["timeline"])
        self.assertIsNone(result["conflicts"])

    def test_delete_vs_modify_is_divergence(self):
        base = [cmd("a", "main"), cmd("b", "main", "a", 0, 500)]
        left = [cmd("a", "main")]  # 删除 b
        right = [cmd("a", "main"), cmd("b", "main", "a", 100, 500)]  # 改 b.offset
        result = merge_versions(base, left, right)
        self.assertEqual(
            result["divergences"],
            [
                {
                    "id": "b",
                    "field": "offset_ms",
                    "base": 0,
                    "left": None,
                    "right": 100,
                }
            ],
        )
        self.assertIsNone(result["merged"])

    def test_modify_vs_delete_is_divergence(self):
        # 与上例镜像：左侧修改、右侧删除
        base = [cmd("a", "main"), cmd("b", "main", "a", 0, 500)]
        left = [cmd("a", "main"), cmd("b", "main", "a", 0, 800)]
        right = [cmd("a", "main")]
        result = merge_versions(base, left, right)
        self.assertEqual(
            result["divergences"],
            [
                {
                    "id": "b",
                    "field": "duration_ms",
                    "base": 500,
                    "left": 800,
                    "right": None,
                }
            ],
        )

    def test_both_add_same_id_with_different_content(self):
        base = [cmd("a", "main")]
        left = [cmd("a", "main"), cmd("d", "sub", "a", 0, 300)]
        right = [cmd("a", "main"), cmd("d", "sub", "a", 0, 400)]
        result = merge_versions(base, left, right)
        self.assertEqual(
            result["divergences"],
            [
                {
                    "id": "d",
                    "field": "duration_ms",
                    "base": None,
                    "left": 300,
                    "right": 400,
                }
            ],
        )
        self.assertIsNone(result["merged"])

    def test_divergences_sorted_by_id_then_field(self):
        base = [
            cmd("b", "main", START, 0, 1000),
            cmd("a", "main", START, 0, 1000),
        ]
        # 同一字段被两侧改成不同值：a.anchor、a.duration_ms、b.channel、b.offset_ms
        left = [
            cmd("b", "alt", START, 50, 1000),
            cmd("a", "main", "x", 0, 1200),
        ]
        right = [
            cmd("b", "backup", START, 100, 1000),
            cmd("a", "main", "y", 0, 1500),
        ]
        result = merge_versions(base, left, right)
        self.assertEqual(
            [(d["id"], d["field"]) for d in result["divergences"]],
            [("a", "anchor"), ("a", "duration_ms"), ("b", "channel"), ("b", "offset_ms")],
        )

    def test_delete_vs_multi_field_modify_reports_each_field(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = []  # 删除 a
        right = [cmd("a", "alt", START, 0, 1500)]  # 改 channel 与 duration
        result = merge_versions(base, left, right)
        self.assertEqual(
            [(d["id"], d["field"]) for d in result["divergences"]],
            [("a", "channel"), ("a", "duration_ms")],
        )
        for divergence in result["divergences"]:
            self.assertIsNone(divergence["left"])


class MergedValidationTests(unittest.TestCase):
    """无分歧时由现有编排器校验合并口令，沿用 /compile 原错误码。"""

    def test_missing_anchor_from_adopted_deletion(self):
        # 左侧删除锚点口令 a（右侧未改 -> 采纳删除），b 仍锚定 a
        base = [cmd("a", "main"), cmd("b", "main", "a", 0, 500)]
        left = [cmd("b", "main", "a", 0, 500)]
        right = [cmd("a", "main"), cmd("b", "main", "a", 0, 500)]
        with self.assertRaises(CompileError) as ctx:
            merge_versions(base, left, right)
        self.assertEqual(ctx.exception.code, "MISSING_ANCHOR")
        self.assertEqual(ctx.exception.details["anchors"], ["a"])

    def test_dependency_cycle_from_two_sided_reanchor(self):
        # 左侧把 a 锚到 b，右侧把 b 锚到 a：各自合法，合并后成环
        base = [cmd("a", "main"), cmd("b", "main")]
        left = [cmd("a", "main", "b"), cmd("b", "main")]
        right = [cmd("a", "main"), cmd("b", "main", "a")]
        with self.assertRaises(CompileError) as ctx:
            merge_versions(base, left, right)
        self.assertEqual(ctx.exception.code, "DEPENDENCY_CYCLE")

    def test_negative_start_from_adopted_offset(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, -500, 1000)]
        right = [cmd("a", "main", START, 0, 1000)]
        with self.assertRaises(CompileError) as ctx:
            merge_versions(base, left, right)
        self.assertEqual(ctx.exception.code, "NEGATIVE_START")

    def test_invalid_duration_from_adopted_change(self):
        base = [cmd("a", "main", START, 0, 1000)]
        left = [cmd("a", "main", START, 0, 0)]
        right = [cmd("a", "main", START, 0, 1000)]
        with self.assertRaises(CompileError) as ctx:
            merge_versions(base, left, right)
        self.assertEqual(ctx.exception.code, "INVALID_DURATION")

    def test_divergence_skips_merged_validation(self):
        # 有分歧时直接返回分歧，不触发合并口令的编排校验
        base = [cmd("a", "main"), cmd("b", "main", "a", 0, 500)]
        left = [cmd("b", "main", "a", 0, 500)]  # 删除 a
        right = [cmd("a", "main", START, 0, 2000), cmd("b", "main", "a", 0, 500)]
        result = merge_versions(base, left, right)
        self.assertEqual(
            [(d["id"], d["field"]) for d in result["divergences"]],
            [("a", "duration_ms")],
        )
        self.assertIsNone(result["merged"])


class InputValidationTests(unittest.TestCase):
    def test_duplicate_id_in_any_side(self):
        dup = [cmd("a", "main"), cmd("a", "main", START, 0, 500)]
        for side in ("base", "left", "right"):
            arrays = {"base": [], "left": [], "right": []}
            arrays[side] = dup
            with self.assertRaises(MergeError) as ctx:
                merge_versions(arrays["base"], arrays["left"], arrays["right"])
            self.assertEqual(ctx.exception.code, "DUPLICATE_ID")
            self.assertEqual(ctx.exception.details["side"], side)
            self.assertEqual(ctx.exception.details["duplicates"], ["a"])

    def test_merge_error_is_compile_error(self):
        # 复用 CompileError 的 422 转换通道
        self.assertTrue(issubclass(MergeError, CompileError))


if __name__ == "__main__":
    unittest.main()
