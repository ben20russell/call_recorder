"""Call Recorder for the Claude desktop app: a small MCP server (standard library only) that answers Claude by
asking the Call Recorder app running on this Mac. It reads none of your files itself, so the Claude app needs no
access to your Downloads or Documents folders; Call Recorder does the reading, with the access it already has.
Runs on macOS's own /usr/bin/python3 (3.9), so keep it free of newer syntax."""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NOT_RUNNING = "Call Recorder isn't running on this Mac. Open it (it lives in the menu bar), then try again."


def base():
    try:
        with open(os.path.expanduser("~/.callrec/config.json")) as f:
            port = json.load(f).get("port", 8765)
    except (OSError, ValueError):
        port = 8765
    return "http://127.0.0.1:%d" % port


def api(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base() + path, data=data,
                                     headers={"Content-Type": "application/json", "X-CallRec": "1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def tools_and_instructions():
    """From the running app; the copy saved with the extension when it isn't running."""
    try:
        return api("/api/mcp")
    except Exception:
        with open(os.path.join(HERE, "tools.json")) as f:
            return json.load(f)


def failure(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def handle(message):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "Call Recorder", "version": "1.0.2"},
                "instructions": tools_and_instructions().get("instructions", "")}
    if method == "tools/list":
        return {"tools": tools_and_instructions()["tools"]}
    if method == "tools/call":
        try:
            return api("/api/mcp/call", {"name": params.get("name"), "arguments": params.get("arguments") or {}})
        except urllib.error.HTTPError as e:
            try:
                detail = json.load(e).get("error", "")
            except ValueError:
                detail = ""
            return failure("Call Recorder couldn't answer (%s). If it was just updated, quit and reopen it from the menu bar."
                           % (detail or e.code))
        except (urllib.error.URLError, OSError):
            return failure(NOT_RUNNING)
    if method == "ping":
        return {}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # a notification: nothing to answer
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            reply["result"] = handle(message)
        except LookupError:
            reply["error"] = {"code": -32601, "message": "Method not found: %s" % message.get("method")}
        except Exception as e:
            reply["error"] = {"code": -32603, "message": str(e)}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
