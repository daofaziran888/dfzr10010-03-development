"""数据分支合并接口：维护分支记录，覆盖执行合并、生成目标快照、待复核确认等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.branch import BranchService

router = APIRouter(prefix="/api/branch", tags=["数据分支合并"])

service = BranchService()

LIST_FIELDS = ["分支编号", "表名称", "分支类型", "源分支", "目标分支", "冲突处理", "合并批次", "合并状态", "合并时间", "分支状态"]
STATUSES = ["已创建", "合并中", "已合并"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按分支编号检索"),
    status: str | None = Query(default=None, description="已创建、合并中、已合并"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按分支编号与状态过滤数据分支合并列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/reviews", response_model=dict)
def list_reviews(status: str | None = Query(default=None, description="按确认状态过滤，如待复核")) -> dict:
    """待复核页：按合并批次返回冲突字段、唯一目标快照与血缘路径。"""
    items, total = service.list_reviews(status=status)
    return {"items": items, "total": total, "page": 1, "size": total}


@router.get("/audit", response_model=dict)
def list_audits() -> dict:
    """合并审计台账：每个合并批次一条，包含冲突处置、目标快照、血缘与联动结果。"""
    items = service.list_audits()
    return {"items": items, "total": len(items), "page": 1, "size": len(items)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据分支合并清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "branch", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分支记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分支记录 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/merge-context", response_model=dict)
def get_merge_context(entry_id: int) -> dict:
    """读取源分支与目标分支的字段记录，给出合并前的冲突依据。"""
    context = service.merge_context(entry_id)
    if context is None:
        raise HTTPException(status_code=404, detail=f"分支记录 {entry_id} 不存在或已归档")
    return context


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分支记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分支记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行合并或生成目标快照；跳级、回退与重复合并都会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/reviews/{batch_id}/confirm", response_model=ActionResult)
def confirm_review(batch_id: str, payload: EntryPayload) -> ActionResult:
    """对待复核页的同一快照完成确认；重复确认沿用原快照，不生成新快照。"""
    values = payload.values or {}
    batch, message = service.confirm_review(
        batch_id,
        resolution=str(values.get("resolution") or values.get("冲突处理") or "") or None,
        field_values=values.get("字段值") if isinstance(values.get("字段值"), dict) else None,
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)
