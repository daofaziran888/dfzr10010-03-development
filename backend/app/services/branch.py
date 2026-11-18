"""数据分支合并业务规则：状态流转、字段校验与筛选口径都收在这里。

分支状态只允许按「已创建 → 合并中 → 已合并」单向推进：
- 执行合并：读取源分支与目标分支的字段记录，登记合并批次与冲突依据，状态进入合并中；
- 生成目标快照：沿用同一个合并批次生成唯一的目标数据表快照，同步血缘、业务链路与审计台账，
  状态进入已合并。任何跳级、回退、重复合并都会被拒绝。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "branch"
FIELD_MODULE = "_branch_field"
BATCH_MODULE = "_branch_merge"
REQUIRED_FIELDS = ["分支编号", "表名称", "分支类型"]
STATUS_ORDER = ["已创建", "合并中", "已合并"]
# 动作 -> (允许的当前状态, 目标状态)
ACTION_RULES = {
    "执行合并": ("已创建", "合并中"),
    "生成目标快照": ("合并中", "已合并"),
    "完成合并": ("合并中", "已合并"),
}
LEGACY_ACTIONS = {"删除分支", "归档分支", "回退分支"}
STRATEGY_LATEST = "最新写入优先"
STRATEGY_SOURCE = "源分支优先"
STRATEGY_TARGET = "目标分支优先"
STRATEGIES = [STRATEGY_LATEST, STRATEGY_SOURCE, STRATEGY_TARGET]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _status_of(entry: dict[str, Any]) -> str:
    return str(entry.get("分支状态") or entry.get("status") or STATUS_ORDER[0])


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class BranchService:
    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if any(
                    keyword in str(row.get(field) or "")
                    for field in ("分支编号", "表名称", "源分支", "目标分支")
                )
            ]
        if status:
            rows = [row for row in rows if _status_of(row) == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 分支记录可选字段同时保留，供合并流程读取源/目标分支配置。
        for field in ("源分支", "目标分支", "冲突处理", "合并时间", "合并批次", "父快照", "源分支快照", "目标分支快照"):
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["分支状态"] = STATUS_ORDER[0]
        entry["合并状态"] = "已创建"
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------------ 动作
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分支记录 {entry_id} 不存在或已归档"
        if action in LEGACY_ACTIONS or action not in ACTION_RULES:
            return None, (
                f"动作「{action}」不属于数据分支合并可执行范围；"
                "分支状态只能按已创建、合并中、已合并单向流转，不允许删除、回退或跳级"
            )

        required_from, _ = ACTION_RULES[action]
        current = _status_of(entry)
        if current == "已合并" or current not in STATUS_ORDER:
            return None, f"分支记录当前为「{current}」，已合并记录不允许回退或重复合并"
        if current != required_from:
            return None, (
                f"分支记录当前为「{current}」，执行「{action}」前必须处于「{required_from}」；"
                "不允许跳级流转"
            )

        if action == "执行合并":
            return self._start_merge(entry)
        return self._complete_merge(entry)

    # ------------------------------------------------------------------ 合并过程
    def _new_batch(self, entry: dict[str, Any], batch_id: str) -> dict[str, Any]:
        """同一合并批次同时承载冲突处置与后续目标快照，二者不拆成两批。"""
        strategy = self._strategy_of(entry)
        detail, conflicts = self._build_field_detail(entry, strategy)
        now = _now()
        batch = {
            "id": _next_id(store.rows(BATCH_MODULE)),
            "合并批次": batch_id,
            "分支编号": entry.get("分支编号"),
            "表名称": entry.get("表名称"),
            "源分支": entry.get("源分支") or "—",
            "目标分支": entry.get("目标分支") or "—",
            "源分支快照": entry.get("源分支快照") or "",
            "目标分支快照": entry.get("目标分支快照") or "",
            "父快照": entry.get("父快照") or "",
            "冲突处理": strategy,
            "分支状态": "合并中",
            "合并状态": "合并中",
            "冲突字段": "、".join(item["字段名"] for item in conflicts) or "无",
            "冲突数量": len(conflicts),
            "字段冲突": conflicts,
            "字段明细": detail,
            "校验结果": "待生成目标快照",
            "目标快照": "",
            "血缘记录": "",
            "联动处理": [],
            "确认状态": "待复核" if conflicts else "无需复核",
            "创建时间": now,
            "合并时间": now,
            "status": "合并中",
            "pending": True,
            "abnormal": bool(conflicts),
        }
        store.rows(BATCH_MODULE).append(batch)
        entry["合并批次"] = batch_id
        return batch

    def _start_merge(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        existing = self._find_batch_for_branch(entry)
        if existing and existing.get("合并状态") == "合并中":
            return None, f"分支记录已在合并中，合并批次 {existing.get('合并批次')} 不允许重复创建"

        batch_id = str(entry.get("合并批次") or "").strip() or self._generate_batch_id(entry)
        batch = self._new_batch(entry, batch_id)
        now = str(batch.get("合并时间") or _now())
        entry["合并时间"] = now
        self._set_status(entry, "合并中")
        entry["abnormal"] = bool(batch["字段冲突"])
        conflicts = batch["字段冲突"]
        return entry, (
            f"分支记录已执行合并，合并批次 {batch_id} 已创建"
            + (f"，识别到 {len(conflicts)} 个冲突字段" if conflicts else "，未识别到字段冲突")
        )

    def _complete_merge(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        batch = self._find_batch_for_branch(entry)
        if batch is None:
            # 兼容历史数据：已处于合并中但尚未登记批次时，在当前环节补建同一批次。
            batch_id = str(entry.get("合并批次") or "").strip() or self._generate_batch_id(entry)
            batch = self._new_batch(entry, batch_id)
        if batch.get("目标快照"):
            return None, (
                f"合并批次 {batch.get('合并批次')} 已生成目标快照 {batch.get('目标快照')}，"
                "同一批次不允许重复生成或回退"
            )

        batch_id = str(batch["合并批次"])
        if entry.get("合并批次") and str(entry.get("合并批次")) != batch_id:
            return None, "分支记录的合并批次与冲突处置批次不一致，无法绑定目标快照"

        strategy = str(batch.get("冲突处理") or self._strategy_of(entry))
        detail, conflicts = self._build_field_detail(entry, strategy)
        now = _now()
        snapshot = self._upsert_snapshot(entry, batch, detail, conflicts, now)
        lineage = self._upsert_lineage(entry, batch, snapshot, detail, now)
        linked = self._sync_linked_modules(entry, batch, snapshot, detail, now)

        conflict_names = "、".join(item["字段名"] for item in conflicts) or "无"
        check_result = (
            "通过：未发现字段冲突，目标快照已按目标分支记录生成"
            if not conflicts
            else (
                f"通过：字段 {conflict_names} 存在先后更新冲突，"
                f"已按{strategy}生成唯一目标快照"
            )
        )
        process_result = (
            f"目标快照 {snapshot['快照编号']} 已生成；"
            f"湖仓血缘 {lineage['记录编号']} 已记录；"
            + "；".join(f"{item['链路']}{item['结果']}" for item in linked)
        )

        batch.update({
            "字段冲突": conflicts,
            "字段明细": detail,
            "冲突字段": conflict_names,
            "冲突数量": len(conflicts),
            "校验结果": check_result,
            "目标快照": snapshot["快照编号"],
            "血缘记录": lineage["记录编号"],
            "联动处理": linked,
            "处理结果": process_result,
            "合并状态": "已完成",
            "分支状态": "已合并",
            "确认状态": "待复核" if conflicts else "已确认",
            "合并时间": now,
            "status": "已完成",
            "pending": False,
            "abnormal": bool(conflicts),
        })

        entry["合并批次"] = batch_id
        entry["合并时间"] = now
        self._set_status(entry, "已合并")
        entry["abnormal"] = bool(conflicts)
        return entry, (
            f"分支记录已生成目标快照 {snapshot['快照编号']}，血缘记录 {lineage['记录编号']} 已同步，"
            f"合并批次 {batch_id} 已写入合并审计台账"
        )

    def _set_status(self, entry: dict[str, Any], status: str) -> None:
        """分支状态字段与筛选用的 status 始终同步，且只接受单向序列内的值。"""
        if status not in STATUS_ORDER:
            raise ValueError(f"非法分支状态：{status}")
        entry["status"] = status
        entry["分支状态"] = status
        entry["pending"] = status != STATUS_ORDER[-1]

    # ------------------------------------------------------------------ 字段与冲突
    def _strategy_of(self, entry: dict[str, Any]) -> str:
        value = str(entry.get("冲突处理") or "").strip()
        return value if value in STRATEGIES else STRATEGY_LATEST

    def _field_rows(self, entry: dict[str, Any], branch: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(FIELD_MODULE)
            if str(row.get("分支编号")) == str(entry.get("分支编号"))
            and str(row.get("分支")) == str(branch)
        ]

    def _build_field_detail(
        self,
        entry: dict[str, Any],
        strategy: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source_branch = str(entry.get("源分支") or "")
        target_branch = str(entry.get("目标分支") or "")
        source_rows = {str(row.get("字段名")): row for row in self._field_rows(entry, source_branch)}
        target_rows = {str(row.get("字段名")): row for row in self._field_rows(entry, target_branch)}

        names: list[str] = []
        for name in list(source_rows) + list(target_rows):
            if name not in names:
                names.append(name)

        details: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for name in names:
            source = source_rows.get(name)
            target = target_rows.get(name)
            source_value = source.get("字段值") if source else (target or {}).get("字段值")
            target_value = target.get("字段值") if target else (source or {}).get("字段值")
            base_value = (source or {}).get("基础值") or (target or {}).get("基础值") or ""
            source_time = _as_text((source or {}).get("更新时间"))
            target_time = _as_text((target or {}).get("更新时间"))
            is_conflict = source is not None and target is not None and _as_text(source_value) != _as_text(target_value)

            adopted_value = target_value
            adopted_from = "目标分支"
            if is_conflict:
                adopted_from, adopted_value = self._resolve(strategy, source_value, target_value, source_time, target_time)
            elif source is not None and target is None:
                adopted_from, adopted_value = "源分支", source_value
            elif source is None and target is not None:
                adopted_from, adopted_value = "目标分支", target_value

            if is_conflict:
                later = "源分支" if source_time >= target_time else "目标分支"
                basis = (
                    f"目标分支于 {target_time or '未知时间'} 写入 {target_value}，"
                    f"源分支于 {source_time or '未知时间'} 写入 {source_value}；"
                    f"两侧先后更新同一字段，按{strategy}采用{adopted_from}值 {adopted_value}"
                    + (f"（{later}写入较晚）" if strategy == STRATEGY_LATEST else "")
                )
                conflict_type = "先后更新冲突"
            else:
                basis = f"两侧取值一致，沿用{adopted_from}值 {adopted_value}"
                conflict_type = ""

            item = {
                "字段名": name,
                "源分支值": source_value if source_value is not None else "",
                "目标分支值": target_value if target_value is not None else "",
                "基础值": base_value,
                "源分支更新时间": source_time,
                "目标分支更新时间": target_time,
                "是否冲突": is_conflict,
                "冲突类型": conflict_type,
                "冲突处理": strategy if is_conflict else "无冲突",
                "处理依据": basis,
                "采用值": adopted_value if adopted_value is not None else "",
                "采用来源": adopted_from,
            }
            details.append(item)
            if is_conflict:
                conflicts.append(dict(item))

        return details, conflicts

    def _resolve(
        self,
        strategy: str,
        source_value: Any,
        target_value: Any,
        source_time: str,
        target_time: str,
    ) -> tuple[str, Any]:
        if strategy == STRATEGY_SOURCE:
            return "源分支", source_value
        if strategy == STRATEGY_TARGET:
            return "目标分支", target_value
        # 最新写入优先：时间相同则沿用目标分支，保证结果确定。
        if source_time > target_time:
            return "源分支", source_value
        return "目标分支", target_value

    # ------------------------------------------------------------------ 快照 / 血缘 / 联动
    def _generate_batch_id(self, entry: dict[str, Any] | None = None) -> str:
        """合并批次编号按合并数据版本时间编制，缺省回退到当天。"""
        day = date.today()
        if entry is not None:
            times = [
                _as_text(row.get("更新时间"))
                for row in store.rows(FIELD_MODULE)
                if str(row.get("分支编号")) == str(entry.get("分支编号"))
            ]
            latest = max((item for item in times if item), default="")
            if len(latest) >= 10 and latest[:4].isdigit():
                try:
                    day = datetime.strptime(latest[:10], "%Y-%m-%d").date()
                except ValueError:
                    day = date.today()
        prefix = f"SNAP-BR-{day.strftime('%Y%m%d')}-"
        existing = {
            str(row.get("合并批次"))
            for row in store.rows(BATCH_MODULE)
            if str(row.get("合并批次") or "").startswith(prefix)
        }
        seq = 1
        while f"{prefix}{seq:02d}" in existing:
            seq += 1
        return f"{prefix}{seq:02d}"

    def _snapshot_by_id(self, snapshot_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows("snapshot") if str(row.get("快照编号")) == snapshot_id),
            None,
        )

    def _lineage_by_batch(self, batch_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows("lineage") if str(row.get("操作批次")) == batch_id),
            None,
        )

    def _upsert_snapshot(
        self,
        entry: dict[str, Any],
        batch: dict[str, Any],
        detail: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        batch_id = str(batch["合并批次"])
        snapshot = self._snapshot_by_id(batch_id)
        content = {item["字段名"]: item["采用值"] for item in detail}
        field_text = "; ".join(f"{name}={value}" for name, value in content.items()) or "无字段变更"
        values = {
            "快照编号": batch_id,
            "表名称": entry.get("表名称"),
            "快照时间": now,
            "快照版本": entry.get("快照版本") or f"BR-{entry.get('分支编号')}",
            "快照大小": entry.get("快照大小") or "-",
            "保留天数": entry.get("保留天数") or 30,
            "创建方式": "分支合并",
            "快照状态": "已创建",
            "合并批次": batch_id,
            "分支编号": entry.get("分支编号"),
            "源分支": entry.get("源分支") or "—",
            "目标分支": entry.get("目标分支") or "—",
            "源分支快照": batch.get("源分支快照") or "",
            "目标分支快照": batch.get("目标分支快照") or "",
            "父快照": batch.get("父快照") or entry.get("目标分支快照") or "",
            "快照内容": content,
            "快照内容摘要": field_text,
            "冲突字段": "、".join(item["字段名"] for item in conflicts) or "无",
            "冲突处理": batch.get("冲突处理"),
            "确认状态": "待复核" if conflicts else "已确认",
        }
        if snapshot is None:
            snapshot = {"id": _next_id(store.rows("snapshot")), "status": "已创建", "pending": True}
            snapshot.update(values)
            snapshot["abnormal"] = bool(conflicts)
            store.rows("snapshot").append(snapshot)
        else:
            snapshot.update(values)
            snapshot["status"] = "已创建"
            snapshot["pending"] = True
            snapshot["abnormal"] = bool(conflicts)
        return snapshot

    def _upsert_lineage(
        self,
        entry: dict[str, Any],
        batch: dict[str, Any],
        snapshot: dict[str, Any],
        detail: list[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        batch_id = str(batch["合并批次"])
        lineage = self._lineage_by_batch(batch_id)
        mapping = [
            {
                "源字段": item["字段名"],
                "目标字段": item["字段名"],
                "源分支值": item["源分支值"],
                "目标分支值": item["目标分支值"],
                "采用值": item["采用值"],
                "采用来源": item["采用来源"],
            }
            for item in detail
        ]
        mapping_text = "; ".join(
            f"{item['源字段']}: {item['源分支值']}->{item['目标分支值']}->{item['采用值']}"
            for item in mapping
        ) or "无字段变更"
        values = {
            "记录编号": f"LINE-{batch_id}",
            "上游表名": entry.get("表名称"),
            "下游表名": entry.get("表名称"),
            "转换类型": "分支合并",
            "字段映射": mapping_text,
            "字段映射明细": mapping,
            "操作时间": now,
            "操作批次": batch_id,
            "记录状态": "已记录",
            "快照编号": snapshot["快照编号"],
            "父快照": snapshot.get("父快照") or "",
            "分支编号": entry.get("分支编号"),
            "源分支": entry.get("源分支") or "—",
            "目标分支": entry.get("目标分支") or "—",
            "血缘路径": (
                f"{entry.get('表名称')}（{entry.get('源分支') or '源分支'}）"
                f" → {entry.get('表名称')}（{entry.get('目标分支') or '目标分支'}）"
            ),
        }
        if lineage is None:
            lineage = {"id": _next_id(store.rows("lineage")), "status": "已记录", "pending": True}
            lineage.update(values)
            lineage["abnormal"] = False
            store.rows("lineage").append(lineage)
        else:
            lineage.update(values)
            lineage["status"] = "已记录"
            lineage["pending"] = True
            lineage["abnormal"] = False
        return lineage

    def _sync_linked_modules(
        self,
        entry: dict[str, Any],
        batch: dict[str, Any],
        snapshot: dict[str, Any],
        detail: list[dict[str, Any]],
        now: str,
    ) -> list[dict[str, Any]]:
        """其余业务链路统一消费合并后的目标快照，并把处理结果回写到合并批次。"""
        batch_id = str(batch["合并批次"])
        table = str(entry.get("表名称") or "")
        field_names = "、".join(item["字段名"] for item in detail) or "无字段变更"
        linked: list[dict[str, Any]] = []

        metadata = next(
            (row for row in store.rows("metadata") if str(row.get("表编号")) == f"META-{batch_id}"),
            None,
        )
        meta_values = {
            "表编号": f"META-{batch_id}",
            "表名称": table,
            "字段清单": field_names,
            "分区信息": "-",
            "统计信息": f"记录数 {entry.get('记录数') or 0}",
            "最后更新": now,
            "数据大小": "-",
            "表状态": "已注册",
            "合并批次": batch_id,
            "快照编号": snapshot["快照编号"],
        }
        if metadata is None:
            metadata = {"id": _next_id(store.rows("metadata")), "status": "已注册", "pending": False, "abnormal": False}
            metadata.update(meta_values)
            store.rows("metadata").append(metadata)
        else:
            metadata.update(meta_values)
        linked.append({"链路": "元数据服务", "对象": metadata["表编号"], "结果": "已刷新字段清单"})

        stats = next(
            (row for row in store.rows("stats") if str(row.get("报告编号")) == f"STAT-{batch_id}"),
            None,
        )
        stat_values = {
            "报告编号": f"STAT-{batch_id}",
            "表名称": table,
            "行总数": entry.get("记录数") or 0,
            "文件总数": "-",
            "总容量": "-",
            "列统计": f"字段数 {len(detail)}",
            "统计时间": now,
            "报告状态": "已采集",
            "合并批次": batch_id,
            "快照编号": snapshot["快照编号"],
        }
        if stats is None:
            stats = {"id": _next_id(store.rows("stats")), "status": "已采集", "pending": False, "abnormal": False}
            stats.update(stat_values)
            store.rows("stats").append(stats)
        else:
            stats.update(stat_values)
        linked.append({"链路": "表统计信息", "对象": stats["报告编号"], "结果": "已采集合并后统计"})

        catalog = next(
            (row for row in store.rows("catalog") if str(row.get("作业编号")) == f"CATL-{batch_id}"),
            None,
        )
        catalog_values = {
            "作业编号": f"CATL-{batch_id}",
            "源路径": f"lake://{table}/{batch_id}",
            "目标目录": f"lake://catalog/{table}",
            "同步模式": "增量",
            "增量字段": "快照编号",
            "过滤规则": batch_id,
            "同步频率": "合并后触发",
            "作业状态": "已完成",
            "合并批次": batch_id,
            "快照编号": snapshot["快照编号"],
        }
        if catalog is None:
            catalog = {"id": _next_id(store.rows("catalog")), "status": "已完成", "pending": False, "abnormal": False}
            catalog.update(catalog_values)
            store.rows("catalog").append(catalog)
        else:
            catalog.update(catalog_values)
        linked.append({"链路": "数据目录同步", "对象": catalog["作业编号"], "结果": "已登记快照同步作业"})

        return linked

    # ------------------------------------------------------------------ 待复核
    def _find_batch_for_branch(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        branch_no = str(entry.get("分支编号"))
        batch_id = str(entry.get("合并批次") or "")
        rows = [
            row for row in store.rows(BATCH_MODULE)
            if str(row.get("分支编号")) == branch_no
        ]
        if batch_id:
            matched = [row for row in rows if str(row.get("合并批次")) == batch_id]
            if matched:
                return matched[-1]
        return rows[-1] if rows else None

    def _find_batch(self, batch_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows(BATCH_MODULE) if str(row.get("合并批次")) == batch_id),
            None,
        )

    def _review_item(self, batch: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(batch.get("合并批次"))
        snapshot = self._snapshot_by_id(str(batch.get("目标快照") or batch_id))
        lineage = self._lineage_by_batch(batch_id)
        conflicts = [dict(item) for item in batch.get("字段冲突", []) if item.get("是否冲突")]
        detail = [dict(item) for item in batch.get("字段明细", [])]
        return {
            "合并批次": batch_id,
            "分支编号": batch.get("分支编号"),
            "表名称": batch.get("表名称"),
            "源分支": batch.get("源分支"),
            "目标分支": batch.get("目标分支"),
            "合并状态": batch.get("合并状态"),
            "确认状态": batch.get("确认状态"),
            "冲突处理": batch.get("冲突处理"),
            "冲突字段": batch.get("冲突字段") or "无",
            "冲突数量": len(conflicts),
            "字段冲突": conflicts,
            "字段明细": detail,
            "校验结果": batch.get("校验结果"),
            "处理结果": batch.get("处理结果"),
            "联动处理": batch.get("联动处理", []),
            "合并时间": batch.get("合并时间"),
            # 同一合并批次只返回这一份可恢复快照，避免出现重复快照。
            "目标快照": snapshot,
            "可恢复快照": snapshot,
            "血缘记录": lineage,
            "血缘路径": (lineage or {}).get("血缘路径") or f"{batch.get('表名称')} → {batch.get('目标分支')}",
            "父快照": (snapshot or {}).get("父快照") or batch.get("父快照") or "",
        }

    def list_reviews(self, *, status: str | None = None) -> tuple[list[dict[str, Any]], int]:
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in reversed(store.rows(BATCH_MODULE)):
            batch_id = str(batch.get("合并批次"))
            if not batch.get("目标快照") or batch_id in seen:
                continue
            if status and str(batch.get("确认状态")) != status:
                continue
            seen.add(batch_id)
            items.append(self._review_item(batch))
        return items, len(items)

    def list_audits(self) -> list[dict[str, Any]]:
        audits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for batch in store.rows(BATCH_MODULE):
            batch_id = str(batch.get("合并批次"))
            if batch_id in seen:
                continue
            seen.add(batch_id)
            audits.append({
                "合并批次": batch_id,
                "分支编号": batch.get("分支编号"),
                "表名称": batch.get("表名称"),
                "源分支": batch.get("源分支"),
                "目标分支": batch.get("目标分支"),
                "合并状态": batch.get("合并状态"),
                "冲突字段": batch.get("冲突字段"),
                "冲突数量": batch.get("冲突数量", 0),
                "校验结果": batch.get("校验结果"),
                "目标快照": batch.get("目标快照"),
                "血缘记录": batch.get("血缘记录"),
                "联动处理": batch.get("联动处理", []),
                "处理结果": batch.get("处理结果"),
                "确认状态": batch.get("确认状态"),
                "合并时间": batch.get("合并时间"),
                "status": batch.get("status") or "已完成",
            })
        return audits

    def confirm_review(
        self,
        batch_id: str,
        *,
        resolution: str | None = None,
        field_values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        batch = self._find_batch(batch_id)
        if batch is None:
            return None, f"合并批次 {batch_id} 不存在"
        if not batch.get("目标快照"):
            return None, f"合并批次 {batch_id} 尚未生成目标快照，不能提前复核"

        snapshot = self._snapshot_by_id(str(batch.get("目标快照")))
        if snapshot is None:
            return None, f"合并批次 {batch_id} 的目标快照不存在，无法复核"
        if batch.get("确认状态") == "已确认":
            return batch, f"合并批次 {batch_id} 已复核确认，沿用快照 {snapshot['快照编号']}，未生成重复快照"

        strategy = self._normalize_strategy(resolution) or str(batch.get("冲突处理") or STRATEGY_LATEST)
        entry = next(
            (row for row in store.rows(MODULE) if str(row.get("分支编号")) == str(batch.get("分支编号"))),
            None,
        )
        target_entry = entry or {
            "分支编号": batch.get("分支编号"),
            "表名称": batch.get("表名称"),
            "源分支": batch.get("源分支"),
            "目标分支": batch.get("目标分支"),
        }
        detail, conflicts = self._build_field_detail(target_entry, strategy)
        if field_values:
            for item in detail:
                if item["字段名"] in field_values:
                    item["采用值"] = field_values[item["字段名"]]
                    item["采用来源"] = "人工复核"
                    item["处理依据"] = (
                        f"复核人工指定 {item['字段名']}={item['采用值']}；"
                        f"源分支值 {item['源分支值']}，目标分支值 {item['目标分支值']}"
                    )

        now = _now()
        content = {item["字段名"]: item["采用值"] for item in detail}
        snapshot.update({
            "快照内容": content,
            "快照内容摘要": "; ".join(f"{name}={value}" for name, value in content.items()) or "无字段变更",
            "冲突字段": "、".join(item["字段名"] for item in conflicts) or "无",
            "冲突处理": strategy,
            "确认状态": "已确认",
            "确认时间": now,
        })
        lineage = self._upsert_lineage(
            target_entry,
            batch,
            snapshot,
            detail,
            str(batch.get("合并时间") or now),
        )
        lineage["复核确认"] = "已确认"
        lineage["确认时间"] = now

        batch.update({
            "字段明细": detail,
            "字段冲突": conflicts,
            "冲突数量": len(conflicts),
            "冲突处理": strategy,
            "确认状态": "已确认",
            "确认时间": now,
            "校验结果": (
                f"{batch.get('校验结果') or '通过'}；复核确认按{strategy}沿用快照 {snapshot['快照编号']}"
            ),
            "处理结果": (
                f"{batch.get('处理结果') or ''}；复核确认：同一快照 {snapshot['快照编号']} 已确认，"
                f"冲突处理策略为{strategy}"
            ),
        })
        return batch, f"合并批次 {batch_id} 已确认，快照 {snapshot['快照编号']} 保持唯一且可恢复"

    def _normalize_strategy(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if "源" in text:
            return STRATEGY_SOURCE
        if "目标" in text:
            return STRATEGY_TARGET
        if "最新" in text:
            return STRATEGY_LATEST
        if text in STRATEGIES:
            return text
        return None

    # ------------------------------------------------------------------ 合并依据
    def merge_context(self, entry_id: int) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        strategy = self._strategy_of(entry)
        detail, conflicts = self._build_field_detail(entry, strategy)
        batch = self._find_batch_for_branch(entry)
        return {
            "分支记录": entry,
            "合并批次": (batch or {}).get("合并批次") or entry.get("合并批次"),
            "源分支": entry.get("源分支") or "—",
            "目标分支": entry.get("目标分支") or "—",
            "源分支快照": entry.get("源分支快照") or "",
            "目标分支快照": entry.get("目标分支快照") or "",
            "父快照": entry.get("父快照") or "",
            "冲突处理": strategy,
            "字段明细": detail,
            "字段冲突": conflicts,
            "冲突数量": len(conflicts),
            "合并状态": (batch or {}).get("合并状态") or _status_of(entry),
        }
