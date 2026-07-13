"""Market capability and status routes."""
from __future__ import annotations

from flask import Blueprint, jsonify

from market_data.capabilities import capability_payload


markets_blueprint = Blueprint("markets_api", __name__, url_prefix="/api/markets")


@markets_blueprint.get("/capabilities")
def get_market_capabilities():
    return jsonify(capability_payload())
