def test_signup_and_login_round_trip(client):
    signup = client.post(
        "/api/auth/signup",
        json={
            "name": "Jane Doe",
            "email": "jane@example.com",
            "password": "secret123",
        },
    )
    assert signup.status_code == 201
    payload = signup.get_json()
    assert payload["user"]["email"] == "jane@example.com"
    assert payload["user"]["name"] == "Jane Doe"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == "jane@example.com"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={
            "email": "jane@example.com",
            "password": "secret123",
        },
    )
    assert login.status_code == 200
    assert login.get_json()["user"]["email"] == "jane@example.com"

    again = client.get("/api/auth/me")
    assert again.status_code == 200
    assert again.get_json()["user"]["email"] == "jane@example.com"


def test_login_rejects_bad_password(client):
    client.post(
        "/api/auth/signup",
        json={
            "name": "John Doe",
            "email": "john@example.com",
            "password": "correct-password",
        },
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": "john@example.com",
            "password": "wrong-password",
        },
    )

    assert response.status_code == 401
    assert "Invalid" in response.get_json()["error"]
