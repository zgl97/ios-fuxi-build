#!/usr/bin/env python3
"""Trace every control request the wireless worker handles (r592).

r590 shows the shape of the next wall: `vdev_start` finally succeeds
(`NWDIAG vdev_setup done waited 154 limit 15000 status 0`), `sheng: INFO ok
generation=2` and `Add interface en0 to a new radio` follow, and then the very
next request comes back as `sheng: rpc op=0 result=-1`.  From that point on no
control request is ever answered again: every restarted wpa_supplicant instance
burns eight `sheng: INFO attempt N failed errno=60` retries and the kernel keeps
logging `Failed to add interface en0` (26 times in one boot).

errno 60 is ETIMEDOUT on the supplicant side, so either the worker thread is
stuck inside a handler or it never dequeues the request at all.  Split the
request entry point into a wrapper so both the operation and the result are
visible, and stamp the session open/close paths: whichever request prints `req`
without a matching `rsp` is the one that blocks the worker.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ctl = root / "src/nw_cfg80211_control.c"

text = ctl.read_text()


def replace(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"control-request-log: anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"control-request-log: anchor not unique ({text.count(old)}): {label}")
    text = text.replace(old, new, 1)


if "NWDIAG ctl req op" in text:
    print("control-request-log: already patched")
    sys.exit(0)

# 1) Wrap the request entry point so entry, result and elapsed ticks are logged.
ORIGINAL = (
    "static int native_request(void *unused, uint64_t session, const struct nw_control_view *request, "
    "struct nw_control_builder *reply) {\n"
    "  (void)unused;\n"
)
WRAPPER = (
    "static int native_request_impl(void *unused, uint64_t session, const struct nw_control_view *request, "
    "struct nw_control_builder *reply);\n"
    "/* r592: log the operation before it runs and the result after it returns, so a\n"
    " * request that never answers names the handler that wedged the worker. */\n"
    "static int native_request(void *unused, uint64_t session, const struct nw_control_view *request, "
    "struct nw_control_builder *reply) {\n"
    "  unsigned long started = jiffies;\n"
    "  lx_log(6, \"NWDIAG ctl req op %u gen %u session %lu owner %lu\",\n"
    "         (unsigned)request->header.operation, (unsigned)request->header.generation,\n"
    "         (unsigned long)session, (unsigned long)owner);\n"
    "  int result = native_request_impl(unused, session, request, reply);\n"
    "  lx_log(6, \"NWDIAG ctl rsp op %u result %d ticks %lu\",\n"
    "         (unsigned)request->header.operation, result, (unsigned long)(jiffies - started));\n"
    "  return result;\n"
    "}\n"
    "static int native_request_impl(void *unused, uint64_t session, const struct nw_control_view *request, "
    "struct nw_control_builder *reply) {\n"
    "  (void)unused;\n"
)
replace(ORIGINAL, WRAPPER, "native_request entry")

# 2) Session open: the worker sets owner here, so a stall before the first reply
#    is visible as an open without a following `ctl req`.
replace(
    "static void opened(void *unused, uint64_t session) { (void)unused; owner = session; generation++; snapshot_free(); }",
    "static void opened(void *unused, uint64_t session) {\n"
    "  (void)unused;\n"
    "  lx_log(6, \"NWDIAG ctl open session %lu ticks %lu\", (unsigned long)session, (unsigned long)jiffies);\n"
    "  owner = session; generation++; snapshot_free();\n"
    "  lx_log(6, \"NWDIAG ctl open done session %lu generation %u\", (unsigned long)session, (unsigned)generation);\n"
    "}",
    "opened()",
)

# 3) Session close: this path calls dev_close(), which is the one place the
#    worker can block on driver teardown while the socket already accepts the
#    next client.
replace(
    "static void closed(void *unused, uint64_t session) {\n"
    "  (void)unused;\n"
    "  if (owner != session) return;\n",
    "static void closed(void *unused, uint64_t session) {\n"
    "  (void)unused;\n"
    "  lx_log(6, \"NWDIAG ctl close session %lu owner %lu ticks %lu\",\n"
    "         (unsigned long)session, (unsigned long)owner, (unsigned long)jiffies);\n"
    "  if (owner != session) return;\n",
    "closed() entry",
)
replace(
    "    dev_close(primary); rtnl_unlock();\n"
    "  }\n"
    "}\n",
    "    dev_close(primary); rtnl_unlock();\n"
    "  }\n"
    "  lx_log(6, \"NWDIAG ctl close done session %lu ticks %lu\", (unsigned long)session, (unsigned long)jiffies);\n"
    "}\n",
    "closed() exit",
)

ctl.write_text(text)
print("control-request-log: patched src/nw_cfg80211_control.c")
