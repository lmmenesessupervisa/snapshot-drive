"""Vista HTML agregada del central."""
from flask import Blueprint, current_app, render_template

from . import models as m
from . import tokens as tok
from .permissions import require_central_perm

central_dashboard_bp = Blueprint(
    "central_dashboard", __name__,
    template_folder="../../frontend/templates",
)


@central_dashboard_bp.get("/dashboard-central")
@require_central_perm("central.dashboard:view")
def dashboard():
    rows = m.dashboard_summary(current_app.config["DB_CONN"])
    return render_template("central/dashboard.html", rows=rows)


@central_dashboard_bp.get("/dashboard-central/clients/<int:cid>")
@require_central_perm("central.clients:read")
def client_detail(cid):
    db = current_app.config["DB_CONN"]
    client = m.get_client(db, cid)
    if not client:
        return render_template("central/_404.html"), 404
    targets = m.list_targets_by_client(db, cid)
    events = m.list_events(db, client_id=cid, limit=50)
    return render_template("central/client_detail.html",
                           client=client, targets=targets, events=events)


@central_dashboard_bp.get("/dashboard-central/clients")
@require_central_perm("central.clients:read")
def clients_page():
    return render_template(
        "central/clients.html",
        clients=m.list_clients(current_app.config["DB_CONN"]),
    )


@central_dashboard_bp.get("/dashboard-central/clients/<int:cid>/tokens")
@require_central_perm("central.clients:read")
def tokens_page(cid):
    db = current_app.config["DB_CONN"]
    client = m.get_client(db, cid)
    if not client:
        return render_template("central/_404.html"), 404
    return render_template(
        "central/tokens.html",
        client=client, tokens=tok.list_active(db, cid),
    )
