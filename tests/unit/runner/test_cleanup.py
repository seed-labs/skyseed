"""A failed rule removal must not abandon other independently owned resources."""
import errno
import os

import pytest

from skyseed.runner.instruction_executor import InstructionExecutor


@pytest.mark.parametrize("code", [errno.ESRCH, errno.EIO])
def test_failed_policy_removal_still_removes_interfaces_and_closes_namespace(code):
    calls = []
    class Channel:
        def local_output_rule(self, *args, **kwargs):
            calls.append("rule")
            raise OSError(code, "injected cleanup failure")
        def delete_group(self, group):
            calls.append("group")
        def delete_link(self, index):
            calls.append("identity")
        def close(self):
            calls.append("socket")
    namespace = os.open("/proc/self/ns/net", os.O_RDONLY)
    network = InstructionExecutor("linux")
    network.nodes["a"] = {"socket": Channel(), "injection": {"198.18.0.0/16"},
                          "identity_index": 10, "namespace": namespace}
    if code == errno.EIO:
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            network.close()
    else:
        network.close()
    assert calls == ["rule", "group", "identity", "socket"]
    with pytest.raises(OSError):
        os.fstat(namespace)
