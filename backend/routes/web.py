"""Rutas HTML (sirve el dashboard)."""
from flask import Blueprint, render_template

web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def index():
    return render_template("index.html", page="dashboard")


@web_bp.get("/snapshots")
def snapshots():
    return render_template("snapshots.html", page="snapshots")


@web_bp.get("/logs")
def logs():
    return render_template("logs.html", page="logs")


@web_bp.get("/settings")
def settings():
    return render_template("settings.html", page="settings")
