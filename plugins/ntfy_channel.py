"""Alert channel plug-in: free push notifications through ntfy.sh (no account needed).

1. Install the free "ntfy" app on your phone and subscribe to a hard-to-guess topic name,
   for example  stocks-k8x2q9.
2. Put  NTFY_TOPIC=stocks-k8x2q9  in .env (or as a GitHub secret for cloud mode).
`ntfy` is already listed under alerts.channels in config.yaml.
"""

import logging

from tracker.alerts.channels import Channel, post_with_retry
from tracker.plugins import register

log = logging.getLogger(__name__)


@register("channels", "ntfy")
class NtfyChannel(Channel):
    name = "ntfy"

    def __init__(self, topic, server="https://ntfy.sh"):
        self.url = f"{server.rstrip('/')}/{topic}"

    @classmethod
    def from_config(cls, cfg):
        topic = cfg.env("NTFY_TOPIC")
        return cls(topic, cfg.env("NTFY_SERVER", "https://ntfy.sh")) if topic else None

    def send(self, text):
        title = text.splitlines()[0][:120]
        priority = "urgent" if "URGENT" in title else "high" if "HOT" in title else "default"
        # HTTP headers must be plain ASCII with no leading space, so drop the emoji.
        header_title = title.encode("ascii", "ignore").decode().strip() or "Stock alert"
        return post_with_retry(self.url, name="ntfy", data=text.encode("utf-8"),
                               headers={"Title": header_title, "Priority": priority})
