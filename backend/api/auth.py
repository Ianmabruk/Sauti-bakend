"""Email/password authentication for the app session."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import db
from ..models import User

auth_bp = Blueprint("auth", __name__)


def _serialize_user(user: User) -> dict:
    """Return the subset of user data exposed to the browser."""
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone_number": user.phone_number,
    }


@auth_bp.route("/auth/signup", methods=["POST"])
def signup():
    """Create a local account and sign the user in."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not name or not email or not password:
        return jsonify({"error": "name, email, and password are required"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409

    user = User(name=name, email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    return jsonify({"user": _serialize_user(user), "message": "Account created"}), 201


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    """Authenticate a user by email and password."""
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password"}), 401

    session["user_id"] = user.id
    return jsonify({"user": _serialize_user(user), "message": "Logged in"})


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    """Clear the current session."""
    session.clear()
    return jsonify({"message": "Logged out"})


@auth_bp.route("/auth/me", methods=["GET"])
def me():
    """Return the currently authenticated user, if any."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return jsonify({"error": "Not authenticated"}), 401

    return jsonify({"user": _serialize_user(user)})
