import json
import os
import time
import traceback
import urllib.parse
import urllib.request

import boto3


secrets = boto3.client("secretsmanager")


def log(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def get_secret() -> dict:
    response = secrets.get_secret_value(
        SecretId=os.environ["OPENSKY_SECRET_ARN"]
    )

    secret = json.loads(response["SecretString"])

    client_id = secret.get("client_id") or secret.get("clientId")
    client_secret = secret.get("client_secret") or secret.get("clientSecret")

    if not client_id or not client_secret:
        raise ValueError("OpenSky secret must contain client_id/client_secret or clientId/clientSecret")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
    }


def test_public_internet() -> dict:
    started = time.time()

    request = urllib.request.Request(
        "https://example.com",
        method="GET",
        headers={"User-Agent": "wilvor-fargate-probe"},
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        return {
            "ok": True,
            "stage": "public_internet",
            "status": response.status,
            "latency_ms": int((time.time() - started) * 1000),
        }


def get_opensky_token() -> dict:
    creds = get_secret()

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode("utf-8")

    request = urllib.request.Request(
        os.environ["OPENSKY_TOKEN_URL"],
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "wilvor-fargate-probe",
        },
    )

    started = time.time()

    with urllib.request.urlopen(request, timeout=20) as response:
        token_response = json.loads(response.read().decode("utf-8"))

    return {
        "access_token": token_response["access_token"],
        "token_type": token_response.get("token_type"),
        "expires_in": token_response.get("expires_in"),
        "latency_ms": int((time.time() - started) * 1000),
    }


def test_opensky_states() -> dict:
    token_response = get_opensky_token()

    params = urllib.parse.urlencode({
        "lamin": os.environ["OPENSKY_LAMIN"],
        "lomin": os.environ["OPENSKY_LOMIN"],
        "lamax": os.environ["OPENSKY_LAMAX"],
        "lomax": os.environ["OPENSKY_LOMAX"],
    })

    url = f"{os.environ['OPENSKY_STATES_URL']}?{params}"

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token_response['access_token']}",
            "Accept": "application/json",
            "User-Agent": "wilvor-fargate-probe",
        },
    )

    started = time.time()

    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))

    states = body.get("states") or []

    return {
        "ok": True,
        "stage": "opensky_states",
        "opensky_time": body.get("time"),
        "states_count": len(states),
        "token_latency_ms": token_response["latency_ms"],
        "states_latency_ms": int((time.time() - started) * 1000),
    }


def main() -> None:
    log({
        "service": "opensky-fargate-probe",
        "event": "STARTED",
    })

    internet_result = test_public_internet()
    log(internet_result)

    states_result = test_opensky_states()
    log(states_result)

    log({
        "service": "opensky-fargate-probe",
        "event": "COMPLETED",
        "ok": True,
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log({
            "service": "opensky-fargate-probe",
            "event": "FAILED",
            "ok": False,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        })
        raise