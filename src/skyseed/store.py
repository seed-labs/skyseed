"""etcd-backed JSON values and revisioned change subscriptions."""
import base64
from contextlib import contextmanager
import json
from urllib.request import Request, build_opener, ProxyHandler


def _encoded(value):
    return base64.b64encode(value).decode()


def _prefix_end(value):
    return value[:-1] + bytes([value[-1] + 1])


class Store:
    """One experiment namespace; user measurements use the user/ subtree."""
    def __init__(self, endpoint="http://127.0.0.1:2379", *, namespace="skyseed"):
        self.endpoint = endpoint.rstrip("/")
        self.prefix = "/" + namespace.strip("/") + "/"
        # Runtime control traffic goes directly to the configured etcd service.
        self._http = build_opener(ProxyHandler({}))

    def _key(self, key):
        return (self.prefix + key.lstrip("/")).encode()

    def _request(self, method, payload):
        return Request(self.endpoint + "/v3/" + method, json.dumps(payload).encode(),
                       {"Content-Type": "application/json"})

    def _call(self, method, payload):
        with self._http.open(self._request(method, payload)) as response:
            return json.load(response)

    def put(self, key, value):
        result = self._call("kv/put", {"key": _encoded(self._key(key)),
                                      "value": _encoded(json.dumps(value).encode())})
        return int(result["header"]["revision"])

    def get(self, key):
        result = self._call("kv/range", {"key": _encoded(self._key(key))})
        values = result.get("kvs", ())
        if not values:
            return None
        value_bytes = base64.b64decode(values[0]["value"])
        return json.loads(value_bytes)

    def put_many(self, values):
        operations = []
        for key, value in values.items():
            request = {
                "key": _encoded(self._key(key)),
                "value": _encoded(json.dumps(value).encode()),
            }
            operations.append({"request_put": request})
        result = self._call("kv/txn", {"success": operations})
        return int(result["header"]["revision"])

    def list(self, prefix=""):
        key = self._key(prefix)
        result = self._call("kv/range", {"key": _encoded(key), "range_end": _encoded(_prefix_end(key))})
        values = {}
        for item in result.get("kvs", ()):
            full_key = base64.b64decode(item["key"]).decode()
            local_key = full_key[len(self.prefix):]
            value_bytes = base64.b64decode(item["value"])
            values[local_key] = json.loads(value_bytes)
        return values

    def put_user(self, key, value):
        return self.put("user/" + key.lstrip("/"), value)

    def get_user(self, key):
        return self.get("user/" + key.lstrip("/"))

    @contextmanager
    def watch(self, prefix="", *, revision=0):
        """Yield a stream of changes; leaving the with block closes the socket."""
        key = self._key(prefix)
        payload = {
            "create_request": {
                "key": _encoded(key),
                "range_end": _encoded(_prefix_end(key)),
                "start_revision": revision,
            },
        }
        response = self._http.open(self._request("watch", payload))

        def changes():
            for line in response:
                result = json.loads(line).get("result", {})
                if result.get("canceled"):
                    raise RuntimeError(f"etcd watch canceled: {result}")
                for event in result.get("events", ()):
                    item = event["kv"]
                    full_key = base64.b64decode(item["key"]).decode()
                    value = None
                    if "value" in item:
                        value = json.loads(base64.b64decode(item["value"]))
                    yield {
                        "key": full_key[len(self.prefix):],
                        "value": value,
                        "revision": int(item["mod_revision"]),
                        "type": event.get("type", "PUT"),
                    }

        try:
            yield changes()
        finally:
            response.close()
