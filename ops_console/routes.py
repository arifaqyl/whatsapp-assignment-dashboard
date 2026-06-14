from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from .services import (
    approve_queue_item,
    dismiss_queue_item,
    get_health_view,
    get_queue_item_view,
    get_queue_view,
    retry_queue_item,
)


ops_bp = Blueprint("ops", __name__, template_folder="templates")


def _queue_context_params():
    return {
        "queue_status": request.form.get("queue_status") or request.args.get("queue_status") or "pending",
        "source": request.form.get("source") or request.args.get("source") or None,
        "reason": request.form.get("reason") or request.args.get("reason") or None,
        "page": request.form.get("page", type=int) or request.args.get("page", type=int) or 1,
        "page_size": request.form.get("page_size", type=int) or request.args.get("page_size", type=int) or 10,
    }


def _redirect_after_action(queue_item_id, *, ok, status_ok, status_error, detail, queue_status_success=None, queue_status_error=None):
    return_to = request.form.get("return_to") or request.args.get("return_to") or "queue"
    if return_to == "detail":
        return redirect(
            url_for(
                "ops.queue_item_page",
                queue_item_id=queue_item_id,
                status=status_ok if ok else status_error,
                detail=detail,
            )
        )

    params = _queue_context_params()
    params["status"] = status_ok if ok else status_error
    params["detail"] = detail
    if ok and queue_status_success:
        params["queue_status"] = queue_status_success
    elif (not ok) and queue_status_error:
        params["queue_status"] = queue_status_error
    return redirect(url_for("ops.queue_page", **params))


@ops_bp.get("/")
def home():
    return queue_page()


@ops_bp.get("/queue")
def queue_page():
    status_filter = request.args.get("queue_status") or "pending"
    source_type = request.args.get("source") or None
    reason_code = request.args.get("reason") or None
    status = request.args.get("status") or None
    detail = request.args.get("detail") or None
    page = request.args.get("page", default=1, type=int) or 1
    page_size = request.args.get("page_size", default=10, type=int) or 10
    data = get_queue_view(
        status_filter=status_filter,
        source_type=source_type,
        reason_code=reason_code,
        page=page,
        page_size=page_size,
    )
    return render_template(
        "queue.html",
        title="Student Bot Ops Console",
        queue_status=status_filter,
        source_type=source_type,
        reason_code=reason_code,
        current_page=page,
        status_message=status,
        detail_message=detail,
        **data,
    )


@ops_bp.get("/queue/<int:queue_item_id>")
def queue_item_page(queue_item_id):
    data = get_queue_item_view(queue_item_id)
    if not data:
        return ("missing queue item", 404)
    return render_template(
        "queue_item.html",
        title=f"Queue Item #{queue_item_id}",
        status_message=request.args.get("status") or None,
        detail_message=request.args.get("detail") or None,
        return_queue_status=request.args.get("queue_status") or "pending",
        return_source=request.args.get("source") or None,
        return_reason=request.args.get("reason") or None,
        return_page=request.args.get("page", default=1, type=int) or 1,
        return_page_size=request.args.get("page_size", default=10, type=int) or 10,
        **data,
    )


@ops_bp.get("/health")
def health_page():
    data = get_health_view()
    return render_template("health.html", title="Student Bot Health", **data)


@ops_bp.get("/api/ping")
def api_ping():
    return jsonify({"status": "ok", "service": "ops_console"}), 200


@ops_bp.post("/queue/<int:queue_item_id>/dismiss")
def dismiss_queue(queue_item_id):
    ok, detail = dismiss_queue_item(queue_item_id)
    return _redirect_after_action(
        queue_item_id,
        ok=ok,
        status_ok="dismissed",
        status_error="error",
        detail=detail,
        queue_status_success="dismissed",
    )


@ops_bp.post("/queue/<int:queue_item_id>/approve")
def approve_queue(queue_item_id):
    ok, detail = approve_queue_item(
        queue_item_id,
        task=request.form.get("task", ""),
        course=request.form.get("course", ""),
        due=request.form.get("due", ""),
    )
    return _redirect_after_action(
        queue_item_id,
        ok=ok,
        status_ok="approved",
        status_error="error",
        detail=detail,
        queue_status_success="resolved",
    )


@ops_bp.post("/queue/<int:queue_item_id>/retry")
def retry_queue(queue_item_id):
    ok, detail = retry_queue_item(queue_item_id)
    return _redirect_after_action(
        queue_item_id,
        ok=ok,
        status_ok="retried",
        status_error="retry_pending",
        detail=detail,
        queue_status_success="resolved",
        queue_status_error="pending",
    )
