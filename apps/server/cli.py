"""Minimal ANIMA CLI — talk to your companion from the terminal."""

import asyncio
import json
import sys

import httpx

BASE_URL = "http://127.0.0.1:3031"
TOKEN_FILE = ".anima-cli-session"
DEBUG = "--debug" in sys.argv


def load_session() -> dict | None:
    """Load saved session from disk."""
    try:
        from pathlib import Path

        data = Path(TOKEN_FILE).read_text()
        return json.loads(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_session(token: str, user_id: int, username: str) -> None:
    """Persist session to disk."""
    from pathlib import Path

    Path(TOKEN_FILE).write_text(
        json.dumps({"token": token, "user_id": user_id, "username": username})
    )


def clear_session() -> None:
    from pathlib import Path

    Path(TOKEN_FILE).unlink(missing_ok=True)


async def register(client: httpx.AsyncClient) -> dict:
    """Register a new user."""
    print("\n— New user registration —")
    username = input("Username: ").strip()
    name = input("Your name: ").strip()
    password = input("Password: ").strip()
    agent_name = input(
        "Name your companion (default: Anima): ").strip() or "Anima"
    user_directive = (
        input("Any instructions for your companion? (optional): ").strip() or ""
    )

    resp = await client.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "username": username,
            "name": name,
            "password": password,
            "agentName": agent_name,
            "userDirective": user_directive,
        },
    )
    if resp.status_code != 200:
        print(f"Registration failed: {resp.text}")
        sys.exit(1)

    data = resp.json()
    token = data["unlockToken"]
    user_id = data["id"]
    save_session(token, user_id, username)
    print(f"Registered as {username}. Welcome.\n")
    return {"token": token, "user_id": user_id, "username": username}


async def login(client: httpx.AsyncClient) -> dict:
    """Login an existing user."""
    print("\n— Login —")
    username = input("Username: ").strip()
    password = input("Password: ").strip()

    resp = await client.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        sys.exit(1)

    data = resp.json()
    token = data["unlockToken"]
    user_id = data["id"]
    save_session(token, user_id, username)
    print(f"Logged in as {username}.\n")
    return {"token": token, "user_id": user_id, "username": username}


async def authenticate(client: httpx.AsyncClient) -> dict:
    """Load existing session or prompt for auth."""
    session = load_session()
    if session:
        # Verify token still works
        resp = await client.get(
            f"{BASE_URL}/api/auth/me",
            headers={"x-anima-unlock": session["token"]},
        )
        if resp.status_code == 200:
            print(f"Resuming as {session['username']}.")
            return session
        else:
            print("Session expired.")
            clear_session()

    print("1) Login")
    print("2) Register")
    choice = input("> ").strip()
    if choice == "2":
        return await register(client)
    return await login(client)


async def send_message_stream(
    client: httpx.AsyncClient, session: dict, message: str
) -> str:
    """Send a message and stream the response."""
    headers = {"x-anima-unlock": session["token"]}
    full_response = ""

    async with client.stream(
        "POST",
        f"{BASE_URL}/api/chat",
        json={"message": message,
              "userId": session["user_id"], "stream": True},
        headers=headers,
        timeout=120.0,
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            return f"[Error {resp.status_code}]: {body.decode()}"

        current_event = ""
        async for line in resp.aiter_lines():
            line = line.strip()
            if DEBUG:
                print(f"  [SSE] {repr(line)}", flush=True)
            if not line:
                current_event = ""
                continue
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue

            data_str = line[len("data:"):].strip()
            try:
                event_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if current_event == "chunk":
                chunk = event_data.get("content", "")
                if chunk:
                    print(chunk, end="", flush=True)
                    full_response += chunk
            elif current_event == "tool_return":
                if event_data.get("name") == "send_message" and event_data.get("isTerminal"):
                    msg = event_data.get("output", "")
                    if msg and not full_response:
                        print(msg, end="", flush=True)
                        full_response = msg
            elif current_event == "error":
                err = event_data.get("error", "unknown error")
                print(f"\n[Error]: {err}")

    if not full_response:
        # Fallback to non-streaming
        if DEBUG:
            print("[DEBUG] No response from stream, falling back to sync", flush=True)
        full_response = await send_message_sync(client, session, message)
        print(full_response, end="")

    print()  # newline after stream ends
    return full_response


async def send_message_sync(
    client: httpx.AsyncClient, session: dict, message: str
) -> str:
    """Send a message and wait for complete response."""
    headers = {"x-anima-unlock": session["token"]}
    resp = await client.post(
        f"{BASE_URL}/api/chat",
        json={"message": message,
              "userId": session["user_id"], "stream": False},
        headers=headers,
        timeout=120.0,
    )
    if resp.status_code != 200:
        return f"[Error {resp.status_code}]: {resp.text}"

    data = resp.json()
    return data.get("response", "")


async def main() -> None:
    print("ANIMA — personal companion")
    print("Commands: /quit, /clear, /memory, /self, /episodes\n")

    async with httpx.AsyncClient() as client:
        # Check server is up
        try:
            health = await client.get(f"{BASE_URL}/health", timeout=5.0)
            if health.status_code != 200:
                print(f"Server not healthy: {health.text}")
                sys.exit(1)
        except httpx.ConnectError:
            print(f"Cannot connect to server at {BASE_URL}")
            print("Start it with: bun run dev:server")
            sys.exit(1)

        session = await authenticate(client)
        headers = {"x-anima-unlock": session["token"]}

        while True:
            try:
                user_input = input("\nyou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye.")
                break

            if not user_input:
                continue

            if user_input == "/quit":
                print("bye.")
                break

            if user_input == "/clear":
                resp = await client.post(
                    f"{BASE_URL}/api/chat/reset",
                    params={"userId": session["user_id"]},
                    headers=headers,
                )
                print("New conversation started." if resp.status_code ==
                      200 else f"Error: {resp.text}")
                continue

            if user_input == "/memory":
                resp = await client.get(
                    f"{BASE_URL}/api/memory",
                    params={"userId": session["user_id"]},
                    headers=headers,
                )
                if resp.status_code == 200:
                    items = resp.json()
                    if isinstance(items, list):
                        for item in items[:20]:
                            cat = item.get("category", "?")
                            key = item.get("key", "")
                            val = item.get("value", "")
                            print(f"  [{cat}] {key}: {val}")
                    else:
                        print(json.dumps(items, indent=2))
                else:
                    print(f"Error: {resp.text}")
                continue

            if user_input == "/self":
                resp = await client.get(
                    f"{BASE_URL}/api/self-model",
                    params={"userId": session["user_id"]},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for block in data:
                            name = block.get(
                                "block_name", block.get("name", "?"))
                            content = block.get("content", "")
                            print(f"\n— {name} —")
                            print(content[:500])
                    elif isinstance(data, dict):
                        for key, val in data.items():
                            print(f"\n— {key} —")
                            print(str(val)[:500])
                else:
                    print(f"Error: {resp.text}")
                continue

            if user_input == "/episodes":
                resp = await client.get(
                    f"{BASE_URL}/api/episodes",
                    params={"userId": session["user_id"]},
                    headers=headers,
                )
                if resp.status_code == 200:
                    episodes = resp.json()
                    if isinstance(episodes, list):
                        for ep in episodes[:10]:
                            title = ep.get("title", ep.get(
                                "summary", "untitled"))
                            date = ep.get("created_at", ep.get("date", ""))
                            print(f"  [{date}] {title}")
                    else:
                        print(json.dumps(episodes, indent=2))
                else:
                    print(f"Error: {resp.text}")
                continue

            # Regular message — stream by default
            print("\nanima: ", end="", flush=True)
            await send_message_stream(client, session, user_input)


if __name__ == "__main__":
    asyncio.run(main())
