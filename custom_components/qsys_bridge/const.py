"""Constants for the Q-SYS Bridge integration."""

DOMAIN = "qsys_bridge"

CONF_ADDON_URL = "addon_url"

# The add-on's hostname carries a per-install repository hash, so the URL is
# discovered through Supervisor rather than assumed.
DEFAULT_ADDON_URL = "http://localhost:8099"
DEFAULT_INGRESS_PORT = 8099
ADDON_SLUG_SUFFIX = "qsys-qrc"
SUPERVISOR_API = "http://supervisor"

# The event stream carries value changes; this poll only reconciles which
# controls are exposed, which changes in the add-on UI while HA is running.
RECONCILE_INTERVAL = 30

PLATFORMS = ["binary_sensor", "number", "sensor", "switch", "text"]
