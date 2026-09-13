"""Private result browsing and bounded synchronization API."""
import hmac

from flask import Blueprint, jsonify, request, send_file


def create_server_results_blueprint(service, session_token):
    bp = Blueprint("server_results", __name__, url_prefix="/api/server-results")

    @bp.before_request
    def authorize():
        if not hmac.compare_digest(request.headers.get("X-Quant-Session", ""), session_token):
            return jsonify(error="invalid local session token"), 403

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @bp.errorhandler(FileNotFoundError)
    @bp.errorhandler(KeyError)
    def missing(exc):
        return jsonify(error="服务器结果尚不可用"), 404

    @bp.get("/status")
    def status():
        return jsonify(service.status())

    @bp.post("/sync")
    def sync():
        if request.get_json(silent=True) not in (None, {}):
            raise ValueError("同步请求不接受远程命令或连接参数")
        return jsonify(service.start()), 202

    @bp.post("/cancel")
    def cancel():
        return jsonify(service.cancel())

    @bp.get("/releases")
    def releases():
        return jsonify(items=service.releases())

    @bp.get("/summary")
    def summary():
        return jsonify(service.summary(request.args.get("release_id")))

    @bp.get("/data/<dataset>")
    def dataset(dataset):
        return jsonify(service.dataset(request.args.get("release_id"), dataset,
                                       int(request.args.get("offset", 0)), int(request.args.get("limit", 100)),
                                       request.args.get("strategy")))

    @bp.get("/artifact/<artifact_id>")
    def artifact(artifact_id):
        path, meta = service.artifact(request.args.get("release_id"), artifact_id)
        # Restrict inline rendering to PNG; everything else is plain text/download.
        png = path.suffix.lower() == ".png" and meta["media_type"] == "image/png"
        response = send_file(path, mimetype="image/png" if png else "text/plain; charset=utf-8",
                             as_attachment=not png, download_name=path.name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    return bp
